from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.server import create_app
from app.config.settings import AppPaths, Settings
from app.core.database import Database
from app.core.engine import RouterEngine


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setenv("VOXEL_ROUTER_DATA_DIR", str(tmp_path / "router-data"))
    return AppPaths.from_environment()


@pytest.fixture
def settings(paths):
    instance = Settings(paths)
    # Testes nunca escutam na LAN: o SCP sobe só em loopback.
    instance.update("dicom", {"bind_host": "127.0.0.1"})
    return instance


@pytest.fixture
def database(paths):
    instance = Database(paths)
    instance.initialize()
    return instance


@pytest.fixture
def client(settings, database):
    """API autenticada (admin com senha definitiva), sem iniciar a Engine."""
    settings.update("orthanc", {"enabled": False})
    settings.update("dicom", {"association_timeout_seconds": 3, "network_timeout_seconds": 3})
    engine = RouterEngine(settings, database)
    with TestClient(create_app(engine, start_engine=False)) as test_client:
        test_client.post("/api/auth/provision", json={"username": "voxeladmin", "password": "SenhaInicial@2026"})
        test_client.post("/api/auth/login", json={"username": "voxeladmin", "password": "SenhaInicial@2026"})
        test_client.post("/api/auth/change-password", json={"current_password": "SenhaInicial@2026", "new_password": "SenhaDefinitiva@2026"})
        test_client.post("/api/auth/login", json={"username": "voxeladmin", "password": "SenhaDefinitiva@2026"})
        yield test_client
