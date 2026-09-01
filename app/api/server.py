"""API local do VOXEL Router. Por padrão, deve ser publicada apenas em loopback."""

from __future__ import annotations

import os
import platform
from contextlib import asynccontextmanager
from pathlib import Path, PureWindowsPath
from typing import Any

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app.auth.service import AccountLockedError, AuthenticationError, AuthenticationService
from app.core.engine import RouterEngine
from app.core.logging import get_logger
from app.security.secrets import SecretStoreError, WindowsSecretStore

LOGGER = get_logger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=512)


class ProvisionRequest(BaseModel):
    username: str = Field(default="voxeladmin", min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=512)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=12, max_length=512)


class UsernameChangeRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)


class NodePayload(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    description: str | None = Field(default=None, max_length=500)
    ae_title: str = Field(min_length=1, max_length=16)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    modality: str | None = Field(default=None, max_length=16)
    manufacturer: str | None = Field(default=None, max_length=128)
    location: str | None = Field(default=None, max_length=128)
    enabled: bool = True


class DestinationPayload(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    kind: str = Field(pattern="^(DICOM|DICOMWEB|CLOUD)$")
    ae_title: str | None = Field(default=None, max_length=16)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    endpoint: str | None = Field(default=None, max_length=2048)
    tls_enabled: bool = False
    priority: int = Field(default=3, ge=1, le=4)
    enabled: bool = True

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str | None) -> str | None:
        if value and not value.startswith(("https://", "http://")):
            raise ValueError("Endpoint deve usar HTTP ou HTTPS")
        return value


class QueuePriorityPayload(BaseModel):
    priority: int = Field(ge=1, le=4)


class SettingsPatch(BaseModel):
    values: dict[str, Any]


class NonDicomConfigPayload(BaseModel):
    values: dict[str, Any]
    voxel_pacs_token: str | None = Field(default=None, min_length=1, max_length=4096)


def create_app(engine: RouterEngine | None = None, start_engine: bool = False) -> FastAPI:
    current_engine = engine or RouterEngine()
    database = current_engine.database
    settings = current_engine.settings
    auth = AuthenticationService(database, int(settings.get("api", "session_minutes", default=30)))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        if start_engine:
            current_engine.initialize()
        yield
        if start_engine:
            await current_engine.stop()

    app = FastAPI(title="VOXEL Router Local API", version="1.0.0", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.engine = current_engine
    app.state.database = database
    app.state.settings = settings
    app.state.auth = auth
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])
    app.mount("/assets", StaticFiles(directory=FRONTEND_ROOT / "static"), name="assets")

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "127.0.0.1"

    def get_token(request: Request, session: str | None = Cookie(default=None, alias="voxel_session")) -> str | None:
        if session:
            return session
        authorization = request.headers.get("authorization", "")
        return authorization[7:] if authorization.lower().startswith("bearer ") else None

    def current_user(request: Request, token: str | None = Depends(get_token)) -> dict[str, Any]:
        if not token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Não autenticado")
        user = auth.current_user(token)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessão expirada ou inválida")
        return user

    def configured_user(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if user["must_change_password"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Altere a senha inicial antes de continuar")
        return user

    @app.get("/")
    @app.get("/dashboard", include_in_schema=False)
    @app.get("/admin", include_in_schema=False)
    async def frontend() -> FileResponse:
        """Entrega a SPA existente; o cliente consulta a sessão antes de exibir o Dashboard."""
        return FileResponse(FRONTEND_ROOT / "index.html")

    @app.get("/health")
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return await current_engine.health.snapshot()

    @app.get("/health/orthanc")
    @app.get("/api/health/orthanc")
    async def health_orthanc() -> dict[str, Any]:
        result = await current_engine.orthanc.health()
        return {"status": result.status, "detail": result.detail}

    @app.get("/health/cloud")
    @app.get("/api/health/cloud")
    async def health_cloud() -> dict[str, str]:
        return {"status": "DISCONNECTED"}

    @app.get("/health/dicom")
    @app.get("/api/health/dicom")
    async def health_dicom() -> dict[str, Any]:
        return {"status": "LISTENING" if current_engine.scp.running else "OFFLINE", "ae_title": current_engine.scp.ae_title, "port": current_engine.scp.port}

    @app.get("/health/storage")
    @app.get("/api/health/storage")
    async def health_storage() -> dict[str, Any]:
        return current_engine.health.storage()

    @app.get("/api/auth/bootstrap-status")
    async def bootstrap_status() -> dict[str, bool]:
        return {"provisioned": auth.has_administrator()}

    @app.post("/api/auth/provision", status_code=status.HTTP_201_CREATED)
    async def provision(payload: ProvisionRequest, request: Request) -> dict[str, str]:
        try:
            user_id = auth.provision_administrator(payload.username, payload.password)
            auth.audit(user_id, "PROVISION_ADMIN", "USER", str(user_id), client_ip(request), "SUCCESS")
            return {"message": "Administrador provisionado. Altere a senha no primeiro login."}
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @app.post("/api/auth/login")
    async def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
        try:
            result = auth.login(payload.username, payload.password, client_ip(request))
        except (AuthenticationError, AccountLockedError):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário ou senha inválidos.") from None
        response.set_cookie("voxel_session", result.pop("token"), httponly=True, secure=False, samesite="strict", max_age=int(settings.get("api", "session_minutes", default=30)) * 60, path="/")
        return result

    @app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    async def logout(request: Request, response: Response, token: str | None = Depends(get_token)) -> None:
        if token:
            auth.logout(token, client_ip(request))
        response.delete_cookie("voxel_session", path="/")

    @app.get("/api/auth/me")
    async def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return {"id": user["id"], "username": user["username"], "must_change_password": bool(user["must_change_password"])}

    @app.post("/api/auth/change-password", status_code=status.HTTP_204_NO_CONTENT)
    async def change_password(payload: PasswordChangeRequest, request: Request, response: Response, user: dict[str, Any] = Depends(current_user)) -> None:
        try:
            auth.change_password(int(user["id"]), payload.current_password, payload.new_password, client_ip(request))
        except (AuthenticationError, ValueError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        response.delete_cookie("voxel_session", path="/")

    @app.put("/api/auth/username", status_code=status.HTTP_204_NO_CONTENT)
    async def change_username(payload: UsernameChangeRequest, request: Request, user: dict[str, Any] = Depends(configured_user)) -> None:
        try:
            auth.update_username(int(user["id"]), payload.username, client_ip(request))
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.get("/api/system")
    async def system(_: dict[str, Any] = Depends(configured_user)) -> dict[str, Any]:
        health_data = await current_engine.health.snapshot()
        return {"version": "1.0.0", "router_id": settings.get("system", "router_id"), "queue": current_engine.queue.stats(), "health": health_data}

    @app.get("/api/settings/{section}")
    async def get_settings(section: str, _: dict[str, Any] = Depends(configured_user)) -> dict[str, Any]:
        try:
            return settings.section(section)
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Seção não encontrada") from exc

    @app.patch("/api/settings/{section}")
    async def patch_settings(section: str, payload: SettingsPatch, request: Request, user: dict[str, Any] = Depends(configured_user)) -> dict[str, Any]:
        allowed = {
            "system": {"equipment_name", "timezone", "language"},
            "dicom": {"ae_title", "port", "association_timeout_seconds", "network_timeout_seconds", "max_associations", "study_quiet_window_seconds", "tls_enabled"},
            "queue": {"max_concurrent_transfers", "retry_delays_seconds", "max_attempts"},
            "storage": {"retention_hours", "auto_delete", "warning_percent", "critical_percent", "emergency_percent"},
        }
        if section not in allowed or not set(payload.values).issubset(allowed[section]):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Alteração de configuração não permitida")
        try:
            result = settings.update(section, payload.values)
            auth.audit(int(user["id"]), "UPDATE_SETTINGS", "SETTINGS", section, client_ip(request), "SUCCESS")
            return result
        except (KeyError, ValueError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Configuração inválida") from exc

    @app.get("/api/non-dicom/status")
    async def non_dicom_status(_: dict[str, Any] = Depends(configured_user)) -> dict[str, Any]:
        return current_engine.non_dicom.status()

    @app.get("/api/non-dicom/queue")
    async def non_dicom_queue(_: dict[str, Any] = Depends(configured_user)) -> list[dict[str, Any]]:
        return current_engine.non_dicom.manager.list()

    @app.get("/api/non-dicom/history")
    async def non_dicom_history(_: dict[str, Any] = Depends(configured_user)) -> list[dict[str, Any]]:
        return current_engine.non_dicom.manager.list(history=True)

    @app.get("/api/non-dicom/config")
    async def non_dicom_config(_: dict[str, Any] = Depends(configured_user)) -> dict[str, Any]:
        return current_engine.non_dicom.configured

    @app.post("/api/non-dicom/config")
    async def update_non_dicom_config(payload: NonDicomConfigPayload, request: Request, user: dict[str, Any] = Depends(configured_user)) -> dict[str, Any]:
        allowed = {"enabled", "root_path", "input_mode", "allowed_local_roots", "polling_interval_seconds", "max_attempts", "retry_delays_seconds", "delete_file_after_success", "max_file_size_mb", "max_xml_size_kb", "allowed_mime_types", "voxel_pacs_url", "status_path", "pending_path", "document_path", "metadata_path", "upload_path", "acknowledge_path", "status_update_path", "site_id", "router_id", "timeout_seconds", "tls_enabled"}
        if not set(payload.values).issubset(allowed):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Alteração de configuração Non-DICOM não permitida")
        root = payload.values.get("root_path")
        if root and not (Path(str(root)).is_absolute() or PureWindowsPath(str(root)).is_absolute()):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Diretório raiz deve ser absoluto")
        if payload.values.get("input_mode") and payload.values["input_mode"] not in {"LOCAL_PATH", "VOXEL_MANAGED_FILE"}:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Modo de arquivo Non-DICOM inválido")
        try:
            if payload.voxel_pacs_token:
                WindowsSecretStore(settings.paths).put("non_dicom.voxel_pacs_token", payload.voxel_pacs_token)
            result = settings.update("non_dicom", payload.values)
            current_engine.non_dicom.reconfigure()
            auth.audit(int(user["id"]), "UPDATE_NON_DICOM_SETTINGS", "NON_DICOM", "config", client_ip(request), "SUCCESS")
            return result
        except (KeyError, ValueError, SecretStoreError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Configuração Non-DICOM inválida") from exc

    @app.post("/api/non-dicom/test")
    async def test_non_dicom(request: Request, user: dict[str, Any] = Depends(configured_user)) -> dict[str, Any]:
        worker = current_engine.non_dicom
        directory_ok = all(path.is_dir() for path in (worker.paths.input, worker.paths.processing, worker.paths.completed, worker.paths.failed, worker.paths.retry, worker.paths.files))
        connection = await worker.test_connection()
        auth.audit(int(user["id"]), "TEST_NON_DICOM", "NON_DICOM", "config", client_ip(request), "SUCCESS" if directory_ok else "FAILURE")
        return {"directory": "OK" if directory_ok else "ERROR", "connection": connection, "root_path": str(worker.paths.root)}

    def get_non_dicom_submission(submission_id: str) -> dict[str, Any]:
        row = database.query_one("SELECT * FROM non_dicom_submissions WHERE id = ?", (submission_id,))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tarefa Non-DICOM não encontrada")
        return dict(row)

    @app.get("/api/non-dicom/{submission_id}/xml", response_class=PlainTextResponse)
    async def non_dicom_xml(submission_id: str, _: dict[str, Any] = Depends(configured_user)) -> str:
        item = get_non_dicom_submission(submission_id)
        path = Path(str(item["source_xml_path"]))
        if not path.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "XML local não encontrado")
        return path.read_text(encoding="utf-8", errors="replace")

    @app.post("/api/non-dicom/{submission_id}/open-folder", status_code=status.HTTP_204_NO_CONTENT)
    async def open_non_dicom_folder(submission_id: str, request: Request, user: dict[str, Any] = Depends(configured_user)) -> None:
        item = get_non_dicom_submission(submission_id)
        directory = Path(str(item["file_path"])).parent
        if not directory.is_dir():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Pasta local não encontrada")
        if platform.system() != "Windows":
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Abrir pasta requer a instalação Windows")
        os.startfile(directory)  # type: ignore[attr-defined]  # noqa: S606
        auth.audit(int(user["id"]), "OPEN_NON_DICOM_FOLDER", "NON_DICOM", submission_id, client_ip(request), "SUCCESS")

    @app.post("/api/non-dicom/retry/{submission_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def retry_non_dicom(submission_id: str, request: Request, user: dict[str, Any] = Depends(configured_user)) -> None:
        try:
            current_engine.non_dicom.manager.retry_now(submission_id)
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        auth.audit(int(user["id"]), "RETRY_NON_DICOM", "NON_DICOM", submission_id, client_ip(request), "SUCCESS")

    @app.post("/api/non-dicom/retry-all")
    async def retry_all_non_dicom(request: Request, user: dict[str, Any] = Depends(configured_user)) -> dict[str, int]:
        count = current_engine.non_dicom.manager.retry_all_failed()
        auth.audit(int(user["id"]), "RETRY_ALL_NON_DICOM", "NON_DICOM", None, client_ip(request), "SUCCESS")
        return {"requeued": count}

    @app.post("/api/non-dicom/process")
    async def process_non_dicom(request: Request, user: dict[str, Any] = Depends(configured_user)) -> dict[str, Any]:
        worker = current_engine.non_dicom
        discovered = worker.scan_input()
        processed = await worker.process_once()
        auth.audit(int(user["id"]), "PROCESS_NON_DICOM", "NON_DICOM", None, client_ip(request), "SUCCESS")
        return {"discovered": discovered, "processed": processed}

    @app.get("/api/dicom")
    async def dicom_status(_: dict[str, Any] = Depends(configured_user)) -> dict[str, Any]:
        return await health_dicom()

    @app.post("/api/dicom/start")
    async def dicom_start(request: Request, user: dict[str, Any] = Depends(configured_user)) -> dict[str, str]:
        current_engine.scp.start()
        auth.audit(int(user["id"]), "START_DICOM_SCP", "DICOM", None, client_ip(request), "SUCCESS")
        return {"status": "LISTENING"}

    @app.post("/api/dicom/stop")
    async def dicom_stop(request: Request, user: dict[str, Any] = Depends(configured_user)) -> dict[str, str]:
        current_engine.scp.stop()
        auth.audit(int(user["id"]), "STOP_DICOM_SCP", "DICOM", None, client_ip(request), "SUCCESS")
        return {"status": "OFFLINE"}

    @app.get("/api/modalities")
    async def list_modalities(_: dict[str, Any] = Depends(configured_user)) -> list[dict[str, Any]]:
        return [dict(row) for row in database.query_all("SELECT * FROM dicom_nodes ORDER BY name")]

    @app.post("/api/modalities", status_code=status.HTTP_201_CREATED)
    async def create_modality(payload: NodePayload, request: Request, user: dict[str, Any] = Depends(configured_user)) -> dict[str, int]:
        try:
            with database.transaction() as connection:
                cursor = connection.execute("INSERT INTO dicom_nodes(name, description, ae_title, host, port, modality, manufacturer, location, enabled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(payload.model_dump().values()))
                node_id = int(cursor.lastrowid)
            auth.audit(int(user["id"]), "CREATE_MODALITY", "DICOM_NODE", str(node_id), client_ip(request), "SUCCESS")
            return {"id": node_id}
        except Exception as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, "Não foi possível salvar a modalidade") from exc

    @app.put("/api/modalities/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def update_modality(node_id: int, payload: NodePayload, request: Request, user: dict[str, Any] = Depends(configured_user)) -> None:
        fields = payload.model_dump()
        with database.transaction() as connection:
            cursor = connection.execute("UPDATE dicom_nodes SET name=?, description=?, ae_title=?, host=?, port=?, modality=?, manufacturer=?, location=?, enabled=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (*fields.values(), node_id))
            if cursor.rowcount != 1:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Modalidade não encontrada")
        auth.audit(int(user["id"]), "UPDATE_MODALITY", "DICOM_NODE", str(node_id), client_ip(request), "SUCCESS")

    @app.delete("/api/modalities/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_modality(node_id: int, request: Request, user: dict[str, Any] = Depends(configured_user)) -> None:
        with database.transaction() as connection:
            cursor = connection.execute("DELETE FROM dicom_nodes WHERE id = ?", (node_id,))
            if cursor.rowcount != 1:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Modalidade não encontrada")
        auth.audit(int(user["id"]), "DELETE_MODALITY", "DICOM_NODE", str(node_id), client_ip(request), "SUCCESS")

    @app.get("/api/destinations")
    async def list_destinations(_: dict[str, Any] = Depends(configured_user)) -> list[dict[str, Any]]:
        return [dict(row) for row in database.query_all("SELECT * FROM destinations ORDER BY priority, name")]

    @app.post("/api/destinations", status_code=status.HTTP_201_CREATED)
    async def create_destination(payload: DestinationPayload, request: Request, user: dict[str, Any] = Depends(configured_user)) -> dict[str, int]:
        values = payload.model_dump()
        if values["kind"] == "DICOM" and not (values["ae_title"] and values["host"] and values["port"]):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Destino DICOM exige AE Title, host e porta")
        try:
            with database.transaction() as connection:
                cursor = connection.execute("INSERT INTO destinations(name, kind, ae_title, host, port, endpoint, tls_enabled, priority, enabled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(values.values()))
                destination_id = int(cursor.lastrowid)
            auth.audit(int(user["id"]), "CREATE_DESTINATION", "DESTINATION", str(destination_id), client_ip(request), "SUCCESS")
            return {"id": destination_id}
        except Exception as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, "Não foi possível salvar o destino") from exc

    @app.put("/api/destinations/{destination_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def update_destination(destination_id: int, payload: DestinationPayload, request: Request, user: dict[str, Any] = Depends(configured_user)) -> None:
        values = payload.model_dump()
        with database.transaction() as connection:
            cursor = connection.execute("UPDATE destinations SET name=?, kind=?, ae_title=?, host=?, port=?, endpoint=?, tls_enabled=?, priority=?, enabled=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (*values.values(), destination_id))
            if cursor.rowcount != 1:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Destino não encontrado")
        auth.audit(int(user["id"]), "UPDATE_DESTINATION", "DESTINATION", str(destination_id), client_ip(request), "SUCCESS")

    @app.delete("/api/destinations/{destination_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_destination(destination_id: int, request: Request, user: dict[str, Any] = Depends(configured_user)) -> None:
        with database.transaction() as connection:
            cursor = connection.execute("DELETE FROM destinations WHERE id = ?", (destination_id,))
            if cursor.rowcount != 1:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Destino não encontrado")
        auth.audit(int(user["id"]), "DELETE_DESTINATION", "DESTINATION", str(destination_id), client_ip(request), "SUCCESS")

    @app.get("/api/studies")
    async def list_studies(limit: int = 200, _: dict[str, Any] = Depends(configured_user)) -> list[dict[str, Any]]:
        rows = database.query_all("SELECT * FROM studies ORDER BY received_at DESC LIMIT ?", (min(max(limit, 1), 1000),))
        return [dict(row) for row in rows]

    @app.get("/api/studies/{study_id}")
    async def study_detail(study_id: int, _: dict[str, Any] = Depends(configured_user)) -> dict[str, Any]:
        study = database.query_one("SELECT * FROM studies WHERE id = ?", (study_id,))
        if study is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Estudo não encontrado")
        timelines = database.query_all("SELECT 'TRANSFER' AS kind, started_at AS occurred_at, status, remote_reference FROM transfers WHERE queue_id IN (SELECT id FROM queue WHERE study_id = ?) UNION ALL SELECT 'QUEUE' AS kind, created_at, status, NULL FROM queue WHERE study_id = ? ORDER BY occurred_at", (study_id, study_id))
        return {"study": dict(study), "series": [dict(row) for row in database.query_all("SELECT * FROM series WHERE study_id = ?", (study_id,))], "timeline": [dict(row) for row in timelines]}

    @app.get("/api/queue")
    async def list_queue(_: dict[str, Any] = Depends(configured_user)) -> list[dict[str, Any]]:
        return current_engine.queue.list_items()

    @app.post("/api/queue/{queue_id}/pause", status_code=status.HTTP_204_NO_CONTENT)
    async def queue_pause(queue_id: int, request: Request, user: dict[str, Any] = Depends(configured_user)) -> None:
        current_engine.queue.pause(queue_id)
        auth.audit(int(user["id"]), "PAUSE_QUEUE", "QUEUE", str(queue_id), client_ip(request), "SUCCESS")

    @app.post("/api/queue/{queue_id}/resume", status_code=status.HTTP_204_NO_CONTENT)
    async def queue_resume(queue_id: int, request: Request, user: dict[str, Any] = Depends(configured_user)) -> None:
        current_engine.queue.resume(queue_id)
        auth.audit(int(user["id"]), "RESUME_QUEUE", "QUEUE", str(queue_id), client_ip(request), "SUCCESS")

    @app.post("/api/queue/{queue_id}/retry", status_code=status.HTTP_204_NO_CONTENT)
    async def queue_retry(queue_id: int, request: Request, user: dict[str, Any] = Depends(configured_user)) -> None:
        current_engine.queue.retry_now(queue_id)
        auth.audit(int(user["id"]), "RETRY_QUEUE", "QUEUE", str(queue_id), client_ip(request), "SUCCESS")

    @app.post("/api/queue/{queue_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
    async def queue_cancel(queue_id: int, request: Request, user: dict[str, Any] = Depends(configured_user)) -> None:
        current_engine.queue.cancel(queue_id)
        auth.audit(int(user["id"]), "CANCEL_QUEUE", "QUEUE", str(queue_id), client_ip(request), "SUCCESS")

    @app.patch("/api/queue/{queue_id}/priority", status_code=status.HTTP_204_NO_CONTENT)
    async def queue_priority(queue_id: int, payload: QueuePriorityPayload, request: Request, user: dict[str, Any] = Depends(configured_user)) -> None:
        current_engine.queue.set_priority(queue_id, payload.priority)
        auth.audit(int(user["id"]), "UPDATE_QUEUE_PRIORITY", "QUEUE", str(queue_id), client_ip(request), "SUCCESS")

    @app.get("/api/logs")
    async def logs(limit: int = 200, _: dict[str, Any] = Depends(configured_user)) -> list[dict[str, Any]]:
        return [dict(row) for row in database.query_all("SELECT category, severity, code, message, created_at FROM system_events ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 1000),))]

    @app.get("/api/audit")
    async def audit(limit: int = 200, _: dict[str, Any] = Depends(configured_user)) -> list[dict[str, Any]]:
        return [dict(row) for row in database.query_all("SELECT action, entity_type, entity_id, source_ip, result, created_at FROM audit_logs ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 1000),))]

    return app
