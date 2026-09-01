from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.api.server import create_app
from app.core.engine import RouterEngine

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_auth_shell_starts_with_only_checking_state() -> None:
    """A árvore administrativa nunca pode aparecer antes de /api/auth/me confirmar a sessão."""
    document = BeautifulSoup((PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8"), "html.parser")
    stylesheet = (PROJECT_ROOT / "frontend" / "static" / "css" / "router.css").read_text(encoding="utf-8")
    controller = (PROJECT_ROOT / "frontend" / "static" / "js" / "router.js").read_text(encoding="utf-8")

    assert document.select_one("#app-view[hidden]") is not None
    assert document.select_one("#provision-form[hidden]") is not None
    assert document.select_one("#auth-form[hidden]") is not None
    assert document.select_one("#auth-checking:not([hidden])") is not None
    assert "[hidden]{display:none!important}" in stylesheet
    assert "AUTH_STATES" in controller
    assert "renderAuthChecking();" in controller
    assert "const user = await api('/api/auth/me');" in controller
    assert "renderApplication(user);" in controller


def test_administrative_shell_routes_use_existing_session_contract(settings, database) -> None:
    """Aliases da SPA são públicos apenas como shell; os dados administrativos exigem sessão válida."""
    settings.update("orthanc", {"enabled": False})
    engine = RouterEngine(settings, database)

    with TestClient(create_app(engine, start_engine=False)) as client:
        for route in ("/", "/dashboard", "/admin"):
            response = client.get(route)
            assert response.status_code == 200
            assert 'id="app-view" class="application" hidden' in response.text

        assert client.get("/api/system").status_code == 401

        assert client.post("/api/auth/provision", json={"username": "voxeladmin", "password": "SenhaInicial@2026"}).status_code == 201
        assert client.post("/api/auth/login", json={"username": "voxeladmin", "password": "SenhaInicial@2026"}).status_code == 200
        assert client.post("/api/auth/change-password", json={"current_password": "SenhaInicial@2026", "new_password": "SenhaDefinitiva@2026"}).status_code == 204
        assert client.post("/api/auth/login", json={"username": "voxeladmin", "password": "SenhaDefinitiva@2026"}).status_code == 200
        assert client.get("/api/auth/me").status_code == 200
        assert client.get("/api/system").status_code == 200

        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/system").status_code == 401
