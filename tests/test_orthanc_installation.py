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


class ConnectedCloud:
    async def health_check(self) -> str:
        return "CONNECTED"


async def no_network() -> dict[str, str]:
    return {"local_network": "OK", "dns": "OK", "internet": "OK"}


def test_orthanc_configuration_uses_independent_ports_and_persistent_paths(settings, monkeypatch):
    monkeypatch.setenv("VOXEL_ROUTER_DEV_SECRET_KEY", create_development_key())
    target = configure_orthanc(settings)
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert payload["DicomAet"] == "VOXEL_ORTHANC"
    assert payload["DicomPort"] == 4243
    assert payload["HttpPort"] == 8042
    assert payload["RemoteAccessAllowed"] is False
    assert Path(payload["StorageDirectory"]) == settings.paths.orthanc_storage
    assert Path(payload["IndexDirectory"]) == settings.paths.orthanc_database
    assert settings.paths.orthanc_storage.is_dir()
    assert settings.paths.orthanc_database.is_dir()


@pytest.mark.asyncio
async def test_dashboard_health_reports_external_orthanc_and_separate_ports(settings, monkeypatch):
    monitor = HealthMonitor(settings, OnlineOrthanc(), RunningScp(), ConnectedCloud())
    monkeypatch.setattr(monitor, "network", no_network)

    result = await monitor.snapshot()

    assert result["router"]["status"] == "ONLINE"
    assert result["orthanc"] == {"status": "ONLINE", "detail": "1.13.0", "dicom_port": 4243, "http_port": 8042}
    assert result["cloud"]["status"] == "CONNECTED"
    assert result["dicom"] == {"status": "ONLINE", "ae_title": "VOXEL_ROUTER", "port": 4242}


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
