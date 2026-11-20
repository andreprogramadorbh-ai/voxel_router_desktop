"""Túnel WireGuard "VOXEL VPN": chave gerada na máquina, kit do VOXEL PACS e status do handshake.

A chave privada nunca sai desta máquina: fica no cofre DPAPI e no arquivo do túnel, que só
SYSTEM e Administradores leem. API, UI, SQLite e logs só veem a chave pública.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import os
import platform
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from app.config.settings import Settings
from app.core.logging import get_logger
from app.security.secrets import WindowsSecretStore

LOGGER = get_logger(__name__)

TUNNEL_NAME = "VOXELVPN"
SERVICE_NAME = f"WireGuardTunnel${TUNNEL_NAME}"
PRIVATE_KEY_SECRET = "vpn.wireguard.private_key"
COMMAND_TIMEOUT_SECONDS = 30
# Com PersistentKeepalive 25 s o WireGuard refaz o handshake a cada ~2 min enquanto há tráfego/keepalive.
HANDSHAKE_OK_SECONDS = 180
HANDSHAKE_WARNING_SECONDS = 600
# SIDs bem conhecidos (independem do idioma do Windows): SYSTEM e Administradores.
ACL_GRANTS = ("*S-1-5-18", "*S-1-5-32-544")
HOSTNAME = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")

_PROGRAM_FILES = PureWindowsPath(os.environ.get("ProgramFiles", r"C:\Program Files"))
_SYSTEM_ROOT = PureWindowsPath(os.environ.get("SystemRoot", r"C:\Windows"))
WIREGUARD_EXE = _PROGRAM_FILES / "WireGuard" / "wireguard.exe"
WG_EXE = _PROGRAM_FILES / "WireGuard" / "wg.exe"
SC_EXE = _SYSTEM_ROOT / "System32" / "sc.exe"
ICACLS_EXE = _SYSTEM_ROOT / "System32" / "icacls.exe"


class VpnError(RuntimeError):
    """Erro operacional do túnel com mensagem segura para a UI (sem saída bruta de processo)."""


def wireguard_key(value: str, field: str = "Chave") -> str:
    """Chave WireGuard: base64 de exatamente 32 bytes."""
    text = str(value or "").strip()
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{field} WireGuard inválida") from exc
    if len(raw) != 32 or len(text) != 44:
        raise ValueError(f"{field} WireGuard inválida")
    return text


def client_address(value: str) -> str:
    """Endereço do cliente no túnel: IPv4 privado /32 (o kit do VOXEL entrega um único IP)."""
    text = str(value or "").strip()
    if "/" not in text:
        text = f"{text}/32"
    network = ipaddress.IPv4Network(text, strict=True)
    if network.prefixlen != 32 or not network.network_address.is_private:
        raise ValueError("Endereço do túnel deve ser um IPv4 privado /32")
    return str(network)


def allowed_ips(value: str) -> str:
    """Rotas do túnel: só redes privadas e específicas; nunca rota padrão (0.0.0.0/0)."""
    networks = []
    for part in str(value or "").split(","):
        part = part.strip()
        if not part:
            continue
        network = ipaddress.IPv4Network(part, strict=True)
        if network.prefixlen < 24 or not network.network_address.is_private:
            raise ValueError("AllowedIPs aceita só redes privadas de /24 a /32")
        networks.append(str(network))
    if not networks:
        raise ValueError("Informe ao menos uma rede em AllowedIPs")
    return ", ".join(networks)


def endpoint(value: str) -> str:
    """Endpoint público do concentrador VOXEL: host:porta (IPv4 ou nome DNS)."""
    text = str(value or "").strip()
    host, separator, port_text = text.rpartition(":")
    if not separator or not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise ValueError("Endpoint deve ser host:porta")
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        if not HOSTNAME.fullmatch(host):
            raise ValueError("Host do endpoint inválido") from None
    return f"{host}:{int(port_text)}"


@dataclass(frozen=True)
class VpnKit:
    """Campos do kit VPN-only do VOXEL PACS (sem material secreto)."""

    address: str
    gateway_public_key: str
    endpoint: str
    allowed_ips: str
    keepalive: int
    destination_host: str
    destination_port: int
    called_ae: str
    calling_ae: str

    def as_settings(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "gateway_public_key": self.gateway_public_key,
            "endpoint": self.endpoint,
            "allowed_ips": self.allowed_ips,
            "keepalive": self.keepalive,
            "destination_host": self.destination_host,
            "destination_port": self.destination_port,
            "called_ae": self.called_ae,
            "calling_ae": self.calling_ae,
        }


def render_config(kit: VpnKit, private_key: str) -> str:
    """Arquivo de túnel do WireGuard for Windows (nome do arquivo = nome da interface)."""
    return (
        "[Interface]\n"
        f"PrivateKey = {wireguard_key(private_key, 'Chave privada')}\n"
        f"Address = {kit.address}\n\n"
        "[Peer]\n"
        f"PublicKey = {kit.gateway_public_key}\n"
        f"Endpoint = {kit.endpoint}\n"
        f"AllowedIPs = {kit.allowed_ips}\n"
        f"PersistentKeepalive = {kit.keepalive}\n"
    )


Runner = Callable[[list[str], str | None], subprocess.CompletedProcess[str]]


def run_process(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Processo externo: caminho absoluto, sem shell, timeout. A saída nunca vai para log."""
    return subprocess.run(
        args,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


class WireGuardManager:
    def __init__(self, settings: Settings, secrets: WindowsSecretStore | None = None, runner: Runner = run_process, is_windows: Callable[[], bool] | None = None) -> None:
        self.settings = settings
        self.secrets = secrets or WindowsSecretStore(settings.paths)
        self.runner = runner
        self._is_windows = is_windows or (lambda: platform.system() == "Windows")
        self.directory = settings.paths.root / "vpn"
        self.config_path = self.directory / f"{TUNNEL_NAME}.conf"

    # ---------- estado ----------

    def installed(self) -> bool:
        return self._is_windows() and Path(str(WIREGUARD_EXE)).is_file() and Path(str(WG_EXE)).is_file()

    def public_key(self) -> str | None:
        value = self.settings.get("vpn", "public_key", default=None)
        return str(value) if value else None

    def kit(self) -> dict[str, Any] | None:
        value = self.settings.get("vpn", "kit", default=None)
        return dict(value) if isinstance(value, dict) else None

    def status(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "tunnel": TUNNEL_NAME,
            "wireguard_installed": self.installed(),
            "public_key": self.public_key(),
            "kit": self.kit(),
            "service": "UNSUPPORTED" if not self._is_windows() else "NOT_INSTALLED",
            "last_handshake_at": None,
            "handshake_age_seconds": None,
            "status": "NOT_CONFIGURED",
        }
        if not result["wireguard_installed"]:
            result["status"] = "WIREGUARD_MISSING" if self._is_windows() else "UNSUPPORTED"
            return result
        if not result["public_key"]:
            return result
        if not result["kit"]:
            result["status"] = "AWAITING_KIT"
            return result
        result["service"] = self._service_state()
        if result["service"] != "RUNNING":
            result["status"] = "OFFLINE"
            return result
        handshake = self._latest_handshake()
        if handshake:
            age = max(0, int(time.time()) - handshake)
            result["last_handshake_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(handshake))
            result["handshake_age_seconds"] = age
            result["status"] = "ONLINE" if age <= HANDSHAKE_OK_SECONDS else ("WARNING" if age <= HANDSHAKE_WARNING_SECONDS else "OFFLINE")
        else:
            # Serviço no ar sem handshake: endpoint inacessível (UDP bloqueado) ou chave pública não cadastrada no VOXEL.
            result["status"] = "NO_HANDSHAKE"
        return result

    # ---------- ações ----------

    def generate_keys(self, rotate: bool = False) -> str:
        """Gera o par na máquina; devolve só a chave pública."""
        self._require_wireguard()
        if self.public_key() and not rotate:
            raise VpnError("Já existe chave gerada. Use a rotação explícita para trocar (o VOXEL precisará da nova chave pública).")
        private = self._wg(["genkey"]).strip()
        public = self._wg(["pubkey"], stdin=private + "\n").strip()
        try:
            wireguard_key(private)
            wireguard_key(public)
        except ValueError as exc:
            raise VpnError("O WireGuard devolveu uma chave inesperada") from exc
        self.secrets.put(PRIVATE_KEY_SECRET, private)
        self.settings.update("vpn", {"public_key": public})
        if rotate and self.kit():
            # Chave nova invalida o túnel atual até o VOXEL cadastrar a nova chave pública.
            self.settings.update("vpn", {"kit": None})
            self._uninstall_service()
        LOGGER.info("vpn_keys_generated", rotated=rotate)
        return public

    def apply_kit(self, kit: VpnKit) -> None:
        """Grava o túnel protegido e (re)instala o serviço do WireGuard, que sobe no boot."""
        self._require_wireguard()
        private = self.secrets.get(PRIVATE_KEY_SECRET)
        if not private or not self.public_key():
            raise VpnError("Gere as chaves desta máquina antes de importar o kit")
        if kit.gateway_public_key == self.public_key():
            raise VpnError("A chave pública do concentrador não pode ser a chave desta máquina")
        self._write_protected_config(render_config(kit, private))
        self._uninstall_service()
        self._expect_ok([str(WIREGUARD_EXE), "/installtunnelservice", str(self.config_path)], "Não foi possível instalar o serviço do túnel")
        self.settings.update("vpn", {"kit": kit.as_settings()})
        LOGGER.info("vpn_kit_applied", endpoint=kit.endpoint)

    def remove_tunnel(self) -> None:
        """Remove só o túnel VOXELVPN (o WireGuard e outros túneis da máquina ficam)."""
        self._require_wireguard()
        self._uninstall_service()
        self.config_path.unlink(missing_ok=True)
        self.settings.update("vpn", {"kit": None})
        LOGGER.info("vpn_tunnel_removed")

    # ---------- internos ----------

    def _require_wireguard(self) -> None:
        if not self.installed():
            raise VpnError("WireGuard for Windows não encontrado nesta máquina")

    def _wg(self, args: list[str], stdin: str | None = None) -> str:
        completed = self.runner([str(WG_EXE), *args], stdin)
        if completed.returncode != 0:
            LOGGER.warning("vpn_wg_failed", command=args[0], returncode=completed.returncode)
            raise VpnError("Falha ao executar o WireGuard")
        return completed.stdout

    def _expect_ok(self, args: list[str], message: str) -> None:
        completed = self.runner(args, None)
        if completed.returncode != 0:
            LOGGER.warning("vpn_command_failed", command=PureWindowsPath(args[0]).name, returncode=completed.returncode)
            raise VpnError(message)

    def _write_protected_config(self, content: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        if self._is_windows():
            # Pasta só para SYSTEM/Administradores, sem herança: o arquivo novo herda essa ACL.
            grants = [item for sid in ACL_GRANTS for item in ("/grant:r", f"{sid}:(OI)(CI)F")]
            self._expect_ok([str(ICACLS_EXE), str(self.directory), "/inheritance:r", *grants], "Não foi possível proteger a pasta do túnel")
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(self.config_path)

    def _uninstall_service(self) -> None:
        if self._service_state() == "NOT_INSTALLED":
            return
        self._expect_ok([str(WIREGUARD_EXE), "/uninstalltunnelservice", TUNNEL_NAME], "Não foi possível remover o serviço anterior do túnel")

    def _service_state(self) -> str:
        if not self._is_windows():
            return "UNSUPPORTED"
        completed = self.runner([str(SC_EXE), "query", SERVICE_NAME], None)
        output = completed.stdout.upper()
        if completed.returncode != 0 or "STATE" not in output:
            return "NOT_INSTALLED"
        if "RUNNING" in output:
            return "RUNNING"
        if "PENDING" in output:
            return "STARTING"
        return "STOPPED"

    def _latest_handshake(self) -> int | None:
        completed = self.runner([str(WG_EXE), "show", TUNNEL_NAME, "latest-handshakes"], None)
        if completed.returncode != 0:
            return None
        for line in completed.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit() and int(parts[1]) > 0:
                return int(parts[1])
        return None
