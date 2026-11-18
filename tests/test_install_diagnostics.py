"""Fase 4: diagnóstico avisa rede Pública, exige autenticação do Orthanc e confere portas."""

from __future__ import annotations

import pytest

from app.monitoring import network
from scripts import diagnose_install


def test_network_profiles_ignore_vpn_and_virtual_adapters():
    output = "Ethernet|Private\r\nVOXELVPN|Public\r\nvEthernet (WSL)|Public\r\nWi-Fi|DomainAuthenticated\r\n\r\n"
    assert network.network_profiles(lambda command: output) == {"Ethernet": "Private", "Wi-Fi": "DomainAuthenticated"}


def test_network_profile_command_uses_absolute_powershell_without_shell():
    assert network.POWERSHELL.is_absolute()
    assert network.POWERSHELL.name == "powershell.exe"
    assert network.PROFILE_TIMEOUT_SECONDS > 0


@pytest.mark.parametrize(
    ("profiles", "status"),
    [({"Ethernet": "Private"}, "OK"), ({"Ethernet": "DomainAuthenticated"}, "OK"), ({"Ethernet": "Public", "Wi-Fi": "Private"}, "ATENÇÃO")],
)
def test_public_network_is_reported_as_warning(monkeypatch, profiles, status):
    monkeypatch.setattr(diagnose_install.platform, "system", lambda: "Windows")
    monkeypatch.setattr(diagnose_install, "network_profiles", lambda: profiles)

    [check] = diagnose_install.network_profile_checks()

    assert check.status == status
    assert check.required is False
    if status != "OK":
        assert "Ethernet" in check.detail and "Privada" in check.detail


def test_unreadable_network_profile_never_breaks_diagnostics(monkeypatch):
    def broken():
        raise OSError("sem powershell")

    monkeypatch.setattr(diagnose_install.platform, "system", lambda: "Windows")
    monkeypatch.setattr(diagnose_install, "network_profiles", broken)
    [check] = diagnose_install.network_profile_checks()
    assert (check.status, check.required) == ("ATENÇÃO", False)


async def run_orthanc_checks(monkeypatch, settings, anonymous_code, open_ports):
    async def port_ready(host: str, port: int) -> bool:
        return True

    async def endpoint_ready(url: str, auth=None) -> tuple[bool, str]:
        return True, "200"

    async def code(url: str, auth=None):
        return anonymous_code

    monkeypatch.setattr(diagnose_install, "wait_for_tcp", port_ready)
    monkeypatch.setattr(diagnose_install, "wait_for_http", endpoint_ready)
    monkeypatch.setattr(diagnose_install, "http_code", code)
    monkeypatch.setattr(diagnose_install, "local_ipv4_addresses", lambda: ["192.168.0.10"])
    monkeypatch.setattr(diagnose_install, "tcp_open", lambda host, port, timeout=2.0: (host, port) in open_ports)
    return {check.name: check for check in await diagnose_install.diagnose(settings, "orthanc")}


@pytest.mark.asyncio
async def test_orthanc_diagnostics_pass_with_auth_loopback_and_no_dicom(monkeypatch, settings):
    checks = await run_orthanc_checks(monkeypatch, settings, 401, set())

    assert checks["Orthanc exige autenticação"].status == "OK"
    assert checks["URL do Orthanc em loopback"].status == "OK"
    assert checks["Orthanc REST fora da LAN"].status == "OK"
    assert checks["Orthanc DICOM desligado (porta 4243)"].status == "OK"
    assert not any("Porta Orthanc DICOM" in name for name in checks)


@pytest.mark.asyncio
async def test_orthanc_diagnostics_flag_open_rest_without_auth_and_legacy_dicom(monkeypatch, settings):
    checks = await run_orthanc_checks(monkeypatch, settings, 200, {("192.168.0.10", 8042), ("127.0.0.1", 4243)})

    assert (checks["Orthanc exige autenticação"].status, checks["Orthanc exige autenticação"].required) == ("FALHA", True)
    assert checks["Orthanc REST fora da LAN"].status == "ATENÇÃO"
    assert "192.168.0.10" in checks["Orthanc REST fora da LAN"].detail
    assert checks["Orthanc DICOM desligado (porta 4243)"].status == "ATENÇÃO"


@pytest.mark.asyncio
async def test_orthanc_url_outside_loopback_fails(monkeypatch, settings):
    settings.update("orthanc", {"url": "http://10.0.0.9:8042"})
    checks = await run_orthanc_checks(monkeypatch, settings, 401, set())
    assert checks["URL do Orthanc em loopback"].status == "FALHA"
