"""Fase 6: túnel WireGuard "VOXEL VPN" com WireGuard simulado (nada é instalado no CI)."""

from __future__ import annotations

import base64
import os
import subprocess
import time

import pytest

from app.security.secrets import WindowsSecretStore, create_development_key
from app.vpn import wireguard
from app.vpn.wireguard import PRIVATE_KEY_SECRET, VpnError, VpnKit, WireGuardManager, allowed_ips, client_address, endpoint, render_config, wireguard_key


def key() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


PRIVATE = key()
PUBLIC = key()
GATEWAY = key()


class FakeWireGuard:
    """Simula wg.exe, wireguard.exe, sc.exe e icacls.exe registrando cada chamada."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.stdin: list[str | None] = []
        self.service = False
        self.handshake = 0
        self.fail: str | None = None

    def __call__(self, args: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        self.stdin.append(stdin)
        name = args[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
        out, code = "", 0
        if self.fail and self.fail in args:
            return subprocess.CompletedProcess(args, 1, "", "erro com dado sensível")
        if name == "wg.exe" and args[1] == "genkey":
            out = PRIVATE + "\n"
        elif name == "wg.exe" and args[1] == "pubkey":
            out = PUBLIC + "\n"
        elif name == "wg.exe" and args[1] == "show":
            out = f"{GATEWAY}\t{self.handshake}\n"
        elif name == "wireguard.exe" and args[1] == "/installtunnelservice":
            self.service = True
        elif name == "wireguard.exe" and args[1] == "/uninstalltunnelservice":
            self.service = False
        elif name == "sc.exe":
            if self.service:
                out = "SERVICE_NAME: WireGuardTunnel$VOXELVPN\n        STATE              : 4  RUNNING\n"
            else:
                code = 1060
        return subprocess.CompletedProcess(args, code, out, "")


@pytest.fixture
def fake():
    return FakeWireGuard()


@pytest.fixture
def manager(settings, monkeypatch, fake):
    monkeypatch.setenv("VOXEL_ROUTER_DEV_SECRET_KEY", create_development_key())
    monkeypatch.setattr(WireGuardManager, "installed", lambda self: True)
    return WireGuardManager(settings, WindowsSecretStore(settings.paths), runner=fake, is_windows=lambda: True)


def sample_kit(**overrides) -> VpnKit:
    values = {
        "address": "10.200.20.7/32",
        "gateway_public_key": GATEWAY,
        "endpoint": "vpn.voxelpacs.com.br:51820",
        "allowed_ips": "10.200.10.1/32",
        "keepalive": 25,
        "destination_host": "10.200.10.1",
        "destination_port": 4251,
        "called_ae": "VXCLINICA",
        "calling_ae": "VXCLINICA_RT",
    }
    values.update(overrides)
    return VpnKit(**values)


def test_keys_are_generated_locally_and_only_public_key_is_exposed(manager, fake, settings):
    assert manager.generate_keys() == PUBLIC
    assert manager.secrets.get(PRIVATE_KEY_SECRET) == PRIVATE
    assert PRIVATE not in settings.path.read_text(encoding="utf-8")
    # A chave privada só trafega pelo stdin do wg pubkey, nunca por argumento de linha de comando.
    assert all(PRIVATE not in " ".join(call) for call in fake.calls)
    assert manager.status()["public_key"] == PUBLIC


def test_second_generation_requires_explicit_rotation(manager):
    manager.generate_keys()
    with pytest.raises(VpnError):
        manager.generate_keys()


def test_kit_writes_config_and_installs_tunnel_service(manager, fake):
    manager.generate_keys()
    manager.apply_kit(sample_kit())

    config = manager.config_path.read_text(encoding="utf-8")
    assert f"PrivateKey = {PRIVATE}" in config
    assert f"PublicKey = {GATEWAY}" in config
    assert "AllowedIPs = 10.200.10.1/32" in config
    assert any(call[1:3] == ["/installtunnelservice", str(manager.config_path)] for call in fake.calls)
    # Pasta protegida antes da escrita: herança removida e só SYSTEM/Administradores.
    icacls = next(call for call in fake.calls if call[0].lower().endswith("icacls.exe"))
    assert "/inheritance:r" in icacls and "*S-1-5-18:(OI)(CI)F" in icacls and "*S-1-5-32-544:(OI)(CI)F" in icacls
    assert manager.kit()["endpoint"] == "vpn.voxelpacs.com.br:51820"


def test_kit_before_keys_is_refused(manager):
    with pytest.raises(VpnError):
        manager.apply_kit(sample_kit())


def test_reapplying_kit_replaces_existing_service(manager, fake):
    manager.generate_keys()
    manager.apply_kit(sample_kit())
    manager.apply_kit(sample_kit(endpoint="2.28.60.214:51820"))
    actions = [call[1] for call in fake.calls if call[0].lower().endswith("wireguard.exe")]
    assert actions == ["/installtunnelservice", "/uninstalltunnelservice", "/installtunnelservice"]


def test_status_reflects_handshake_age(manager, fake):
    manager.generate_keys()
    assert manager.status()["status"] == "AWAITING_KIT"
    manager.apply_kit(sample_kit())
    assert manager.status()["status"] == "NO_HANDSHAKE"
    fake.handshake = int(time.time()) - 30
    assert manager.status()["status"] == "ONLINE"
    fake.handshake = int(time.time()) - 400
    assert manager.status()["status"] == "WARNING"
    fake.service = False
    assert manager.status()["status"] == "OFFLINE"


def test_failed_command_does_not_leak_process_output(manager, fake):
    manager.generate_keys()
    fake.fail = "/installtunnelservice"
    with pytest.raises(VpnError) as error:
        manager.apply_kit(sample_kit())
    assert "sensível" not in str(error.value)


def test_remove_tunnel_keeps_wireguard_and_keys(manager, fake):
    manager.generate_keys()
    manager.apply_kit(sample_kit())
    manager.remove_tunnel()
    assert not manager.config_path.exists()
    assert manager.kit() is None
    assert manager.public_key() == PUBLIC


def test_status_without_wireguard_on_windows(settings, monkeypatch):
    monkeypatch.setattr(WireGuardManager, "installed", lambda self: False)
    status = WireGuardManager(settings, runner=FakeWireGuard(), is_windows=lambda: True).status()
    assert status["status"] == "WIREGUARD_MISSING"


@pytest.mark.parametrize("value", ["", "abc", base64.b64encode(os.urandom(31)).decode(), "x" * 44])
def test_invalid_wireguard_keys(value):
    with pytest.raises(ValueError):
        wireguard_key(value)


@pytest.mark.parametrize("value", ["8.8.8.8/32", "10.0.0.0/24", "10.200.20.7/31"])
def test_client_address_must_be_private_host(value):
    with pytest.raises(ValueError):
        client_address(value)


@pytest.mark.parametrize("value", ["0.0.0.0/0", "10.0.0.0/8", "8.8.8.0/24", ""])
def test_allowed_ips_never_take_default_or_public_routes(value):
    with pytest.raises(ValueError):
        allowed_ips(value)


@pytest.mark.parametrize("value", ["semporta", "host:0", "host:70000", "bad_host!:51820"])
def test_invalid_endpoint(value):
    with pytest.raises(ValueError):
        endpoint(value)


def test_render_config_refuses_invalid_private_key():
    with pytest.raises(ValueError):
        render_config(sample_kit(), "não-é-chave")


def test_api_applies_kit_and_creates_paused_voxel_destination(client, monkeypatch):
    engine = client.app.state.engine
    fake = FakeWireGuard()
    monkeypatch.setenv("VOXEL_ROUTER_DEV_SECRET_KEY", create_development_key())
    monkeypatch.setattr(WireGuardManager, "installed", lambda self: True)
    engine.vpn = WireGuardManager(engine.settings, WindowsSecretStore(engine.settings.paths), runner=fake, is_windows=lambda: True)

    assert client.post("/api/vpn/keys", json={}).json() == {"public_key": PUBLIC}
    kit = sample_kit().as_settings()
    response = client.post("/api/vpn/kit", json=kit)
    assert response.status_code == 200, response.text
    destination = response.json()["destination"]
    assert (destination["host"], destination["port"], destination["ae_title"], destination["enabled"]) == ("10.200.10.1", 4251, "VXCLINICA", 0)
    assert destination["outbound_ae_title"] == "VXCLINICA_RT"
    assert PRIVATE not in response.text
    assert PRIVATE not in client.get("/api/vpn").text

    # Destino fora do AllowedIPs é recusado.
    assert client.post("/api/vpn/kit", json={**kit, "destination_host": "10.200.11.1"}).status_code == 422
    # Não existe campo para chave privada: um campo extra é ignorado e nunca gravado.
    client.post("/api/vpn/kit", json={**kit, "private_key": PRIVATE})
    assert PRIVATE not in engine.settings.path.read_text(encoding="utf-8")
    assert len(client.get("/api/destinations").json()) == 1


def test_api_requires_authentication(settings, database):
    from fastapi.testclient import TestClient

    from app.api.server import create_app
    from app.core.engine import RouterEngine

    settings.update("orthanc", {"enabled": False})
    with TestClient(create_app(RouterEngine(settings, database))) as anonymous:
        assert anonymous.get("/api/vpn").status_code == 401
        assert anonymous.post("/api/vpn/keys", json={}).status_code == 401


def test_paths_are_absolute_windows_paths():
    for path in (wireguard.WIREGUARD_EXE, wireguard.WG_EXE, wireguard.SC_EXE, wireguard.ICACLS_EXE):
        assert path.is_absolute()
