from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.monitoring.health import HealthMonitor
from app.orthanc.client import OrthancHealth
from app.orthanc.configuration import configure_orthanc
from app.security.secrets import create_development_key

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class OnlineOrthanc:
    async def health(self) -> OrthancHealth:
        return OrthancHealth("ONLINE", "1.13.0")


class RunningScp:
    running = True
    ae_title = "VOXEL_ROUTER"
    port = 4242


async def no_network() -> dict[str, str]:
    return {"local_network": "OK", "dns": "OK", "internet": "OK"}


def test_orthanc_configuration_disables_dicom_and_keeps_rest_on_loopback(settings, monkeypatch):
    monkeypatch.setenv("VOXEL_ROUTER_DEV_SECRET_KEY", create_development_key())
    target = configure_orthanc(settings)
    payload = json.loads(target.read_text(encoding="utf-8"))

    # Nada no Router usa o DICOM do Orthanc (gravação e consulta são via REST): sem listener 4243.
    assert payload["DicomServerEnabled"] is False
    assert "DicomPort" not in payload
    assert payload["HttpServerEnabled"] is True
    assert payload["HttpPort"] == 8042
    assert payload["HttpBindAddresses"] == ["127.0.0.1"]
    assert payload["RemoteAccessAllowed"] is False
    assert payload["AuthenticationEnabled"] is True
    assert Path(payload["StorageDirectory"]) == settings.paths.orthanc_storage
    assert Path(payload["IndexDirectory"]) == settings.paths.orthanc_database
    assert settings.paths.orthanc_storage.is_dir()
    assert settings.paths.orthanc_database.is_dir()


@pytest.mark.asyncio
async def test_dashboard_health_reports_external_orthanc_and_separate_ports(settings, monkeypatch):
    monitor = HealthMonitor(settings, OnlineOrthanc(), RunningScp())
    monkeypatch.setattr(monitor, "network", no_network)

    result = await monitor.snapshot()

    assert result["router"]["status"] == "ONLINE"
    assert result["orthanc"] == {"status": "ONLINE", "detail": "1.13.0", "http_port": 8042}
    # Sem fila/destinos não há fato que justifique "conectado".
    assert result["cloud"]["status"] == "DISCONNECTED"
    assert result["dicom"] == {"status": "ONLINE", "ae_title": "VOXEL_ROUTER", "port": 4242}


@pytest.mark.asyncio
async def test_sequential_diagnostics_isolate_orthanc_and_router_startup(settings, monkeypatch):
    from scripts import diagnose_install

    async def port_ready(host: str, port: int) -> bool:
        return True

    async def endpoint_ready(url: str, auth=None) -> tuple[bool, str]:
        return True, "200"

    monkeypatch.setattr(diagnose_install, "wait_for_tcp", port_ready)
    monkeypatch.setattr(diagnose_install, "wait_for_http", endpoint_ready)

    orthanc_names = {check.name for check in await diagnose_install.diagnose(settings, "orthanc")}
    router_names = {check.name for check in await diagnose_install.diagnose(settings, "router")}

    assert "Health check Orthanc" in orthanc_names
    assert "Health check Router" not in orthanc_names
    assert "Health check Router" in router_names
    assert "Health check Orthanc" not in router_names


def test_installer_contract_packages_and_starts_independent_components() -> None:
    installer = (PROJECT_ROOT / "installer" / "VOXEL_ROUTER_SETUP.iss").read_text(encoding="utf-8")
    build = (PROJECT_ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")

    assert 'OutputBaseFilename=VOXEL_ROUTER_SETUP' in installer
    assert 'Source: "..\\dist\\VOXELRouterService\\*"' in installer
    assert 'Source: "..\\dist\\VOXELOrthancService\\*"' in installer
    assert 'Source: "..\\vendor\\orthanc\\*"; DestDir: "{app}\\orthanc"' in installer
    assert 'Name: "{commonappdata}\\VOXEL\\Router\\orthanc\\storage"' in installer
    assert 'Name: "{commonappdata}\\VOXEL\\Router\\orthanc\\database"' in installer
    assert "Instalar serviço VOXEL Orthanc" in installer
    assert "Iniciar VOXEL Orthanc" in installer
    assert "Instalar serviço VOXEL Router" in installer
    assert "Iniciar VOXEL Router" in installer
    assert "Verificar VOXEL Orthanc" in installer
    assert "Verificar VOXEL Router" in installer
    assert "Diagnóstico final Router e Orthanc" in installer
    assert "Nenhum diretório em ProgramData é removido" in installer
    assert "function OrthancInstallationIsValid" in installer
    assert "OrthancInstallationWasValid" in installer
    assert "Check: not OrthancInstallationWasValid" in installer
    assert "Orthanc válido detectado" in installer
    assert "--install --name" not in installer
    assert '--name VOXELOrthancService' in build
    assert '--name VOXELDiagnostics' in build


def test_api_health_reports_orthanc_offline_when_real_rest_is_unavailable(settings, database):
    from fastapi.testclient import TestClient

    from app.api.server import create_app
    from app.core.engine import RouterEngine

    settings.update("orthanc", {"enabled": True, "url": "http://127.0.0.1:1", "timeout_seconds": 1})
    engine = RouterEngine(settings, database)
    with TestClient(create_app(engine, start_engine=False)) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["orthanc"]["status"] == "OFFLINE"


def test_orthanc_password_is_random_per_installation_and_reused_on_upgrade(tmp_path, monkeypatch):
    from app.config.settings import AppPaths, Settings

    monkeypatch.setenv("VOXEL_ROUTER_DEV_SECRET_KEY", create_development_key())

    def install(name: str) -> dict:
        monkeypatch.setenv("VOXEL_ROUTER_DATA_DIR", str(tmp_path / name))
        current = Settings(AppPaths.from_environment())
        return json.loads(configure_orthanc(current).read_text(encoding="utf-8"))["RegisteredUsers"]

    first = install("clinica-a")
    # Atualização: o instalador regenera orthanc.json e a senha do cofre é reaproveitada.
    assert install("clinica-a") == first
    second = install("clinica-b")
    assert list(first) == list(second) == ["voxel-router-internal"]
    assert first != second
    assert len(first["voxel-router-internal"]) >= 32
    versioned = (PROJECT_ROOT / "config" / "default.json").read_text(encoding="utf-8")
    assert first["voxel-router-internal"] not in versioned
    assert "RegisteredUsers" not in versioned


def test_nothing_in_router_code_uses_orthanc_dicom_port():
    """Prova estática: o Router só fala com o Orthanc por REST; a antiga porta 4243 não é usada."""
    sources = [path for folder in ("app", "frontend") for path in (PROJECT_ROOT / folder).rglob("*") if path.suffix in {".py", ".js", ".html", ".json"}]
    offenders = [str(path) for path in sources if "4243" in path.read_text(encoding="utf-8") or "dicom_port" in path.read_text(encoding="utf-8")]
    assert offenders == []
    defaults = json.loads((PROJECT_ROOT / "config" / "default.json").read_text(encoding="utf-8"))
    assert "dicom_port" not in defaults["orthanc"]


def test_installer_firewall_opens_only_4242_on_domain_private_and_removes_4243():
    installer = (PROJECT_ROOT / "installer" / "VOXEL_ROUTER_SETUP.iss").read_text(encoding="utf-8")
    add_rules = [line for line in installer.splitlines() if "firewall add rule" in line]

    assert len(add_rules) == 1
    assert "localport=4242" in add_rules[0] and "profile=domain,private" in add_rules[0]
    assert "profile=public" not in installer.lower() and "profile=any" not in installer.lower()
    assert "localport=4243" not in installer
    # Regra legada removida na instalação/atualização e na desinstalação; netsh por caminho absoluto.
    assert installer.count('delete rule name=\\"VOXEL Orthanc DICOM\\"') == 2
    assert "'netsh.exe'" not in installer and '"netsh.exe"' not in installer
    # orthanc.json é regenerado também em atualizações e o Orthanc reiniciado para aplicá-lo.
    assert "config\\orthanc.json')) then begin" not in installer
    assert "'--configure', 'Configurar Orthanc'" in installer
    assert "'restart', 'Reiniciar VOXEL Orthanc'" in installer
