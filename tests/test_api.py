from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.server import create_app
from app.core.engine import RouterEngine


def test_local_api_authentication_and_initial_password_change(settings, database):
    settings.update("orthanc", {"enabled": False})
    engine = RouterEngine(settings, database)
    with TestClient(create_app(engine, start_engine=False)) as client:
        response = client.post("/api/auth/provision", json={"username": "voxeladmin", "password": "SenhaInicial@2026"})
        assert response.status_code == 201

        response = client.post("/api/auth/login", json={"username": "voxeladmin", "password": "SenhaInicial@2026"})
        assert response.status_code == 200
        assert response.json()["must_change_password"] is True
        assert client.get("/api/system").status_code == 403

        response = client.post("/api/auth/change-password", json={"current_password": "SenhaInicial@2026", "new_password": "SenhaDefinitiva@2026"})
        assert response.status_code == 204
        assert client.get("/api/auth/me").status_code == 401

        response = client.post("/api/auth/login", json={"username": "voxeladmin", "password": "SenhaDefinitiva@2026"})
        assert response.status_code == 200
        assert response.json()["must_change_password"] is False
        assert client.get("/api/system").status_code == 200


def test_non_dicom_api_contract_is_protected_and_sanitized(settings, database):
    settings.update("orthanc", {"enabled": False})
    engine = RouterEngine(settings, database)
    with TestClient(create_app(engine, start_engine=False)) as client:
        assert client.get("/api/non-dicom/status").status_code == 401
        client.post("/api/auth/provision", json={"username": "voxeladmin", "password": "SenhaInicial@2026"})
        client.post("/api/auth/login", json={"username": "voxeladmin", "password": "SenhaInicial@2026"})
        client.post("/api/auth/change-password", json={"current_password": "SenhaInicial@2026", "new_password": "SenhaDefinitiva@2026"})
        client.post("/api/auth/login", json={"username": "voxeladmin", "password": "SenhaDefinitiva@2026"})

        config = client.get("/api/non-dicom/config")
        assert config.status_code == 200
        assert "voxel_pacs_token" not in config.json()
        assert client.post("/api/non-dicom/config", json={"values": {"root_path": "relative/path"}}).status_code == 400
        response = client.post("/api/non-dicom/config", json={"values": {"root_path": str(settings.paths.root / "NonDicomApi"), "polling_interval_seconds": 5}})
        assert response.status_code == 200
        assert client.get("/api/non-dicom/status").status_code == 200
        assert client.get("/api/non-dicom/queue").status_code == 200
        assert client.get("/api/non-dicom/history").status_code == 200
        assert client.post("/api/non-dicom/test").json()["directory"] == "OK"
        assert client.post("/api/non-dicom/retry/missing").status_code == 404
