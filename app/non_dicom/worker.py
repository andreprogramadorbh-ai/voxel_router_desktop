"""Worker assíncrono Non-DICOM; observação de pasta e processamento nunca compartilham a mesma thread."""

from __future__ import annotations

import asyncio
import json
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.config.settings import Settings
from app.core.database import Database
from app.core.logging import get_logger
from app.non_dicom.cloud import NonDicomCloudClient, NonDicomCloudConfig
from app.non_dicom.manager import NonDicomManager
from app.non_dicom.parsers import NonDicomParseError, parse_xml, safe_file_name
from app.non_dicom.storage import NonDicomPaths, NonDicomStorage, NonDicomStorageError
from app.security.secrets import SecretStoreError, WindowsSecretStore

LOGGER = get_logger(__name__)


class InputWatcher(Protocol):
    def discover(self) -> list[Path]: ...


class PollingInputWatcher:
    """Estratégia inicial de observação; pode ser substituída futuramente por watchdog."""

    def __init__(self, input_directory: Path) -> None:
        self.input_directory = input_directory

    def discover(self) -> list[Path]:
        return sorted(path for path in self.input_directory.glob("*.xml") if path.is_file())


class NonDicomWorker:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        config = settings.section("non_dicom")
        root = config.get("root_path") or str(settings.paths.root / "NonDicom")
        self.paths = NonDicomPaths.from_root(root)
        self.storage = NonDicomStorage(self.paths, str(config.get("input_mode", "VOXEL_MANAGED_FILE")), list(config.get("allowed_local_roots", [])))
        self.manager = NonDicomManager(database, self.storage, list(config.get("retry_delays_seconds", [30, 120, 300, 900])), int(config.get("max_attempts", 4)))
        self.watcher: InputWatcher = PollingInputWatcher(self.paths.input)
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._last_sync: str | None = None
        self._last_received: str | None = None
        self._last_processed: str | None = None
        self._cloud_status = "DISCONNECTED"

    @property
    def configured(self) -> dict[str, Any]:
        return self.settings.section("non_dicom")

    def reconfigure(self) -> None:
        """Recria apenas os adaptadores Non-DICOM a partir da configuração persistente atual."""
        config = self.configured
        root = config.get("root_path") or str(self.settings.paths.root / "NonDicom")
        self.paths = NonDicomPaths.from_root(root)
        self.storage = NonDicomStorage(self.paths, str(config.get("input_mode", "VOXEL_MANAGED_FILE")), list(config.get("allowed_local_roots", [])))
        self.manager = NonDicomManager(self.database, self.storage, list(config.get("retry_delays_seconds", [30, 120, 300, 900])), int(config.get("max_attempts", 4)))
        self.watcher = PollingInputWatcher(self.paths.input)

    def _client(self) -> NonDicomCloudClient:
        config = self.configured
        token = None
        try:
            token = WindowsSecretStore(self.settings.paths).get("non_dicom.voxel_pacs_token")
        except SecretStoreError:
            pass
        return NonDicomCloudClient(
            NonDicomCloudConfig(
                base_url=str(config.get("voxel_pacs_url", "")), status_path=str(config.get("status_path", "/status")),
                pending_path=str(config.get("pending_path", "/non-dicom/pending")), document_path=str(config.get("document_path", "/non-dicom/documents/{id}")),
                metadata_path=str(config.get("metadata_path", "/non-dicom/documents/{id}/metadata")), upload_path=str(config.get("upload_path", "/non-dicom/submissions")), acknowledge_path=str(config.get("acknowledge_path", "/non-dicom/acknowledge")),
                status_update_path=str(config.get("status_update_path", "/non-dicom/status")), timeout_seconds=int(config.get("timeout_seconds", 15)),
                tls_enabled=bool(config.get("tls_enabled", True)), site_id=str(config.get("site_id", "")), router_id=str(config.get("router_id") or self.settings.get("system", "router_id")),
            ),
            token,
        )

    def _record_system_event(self, severity: str, code: str, message: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO system_events(category, severity, code, message) VALUES ('NON_DICOM', ?, ?, ?)", (severity, code, message[:500]))

    def ingest_file(self, xml_path: Path) -> str | None:
        """Valida e enfileira uma submissão; retornos de falha movem somente o XML para failed."""
        config = self.configured
        try:
            if xml_path.stat().st_size > int(config.get("max_xml_size_kb", 256)) * 1024:
                raise NonDicomParseError("XML excede o limite configurado")
            existing_id, duplicate = self.manager.register_xml(xml_path)
            if duplicate:
                self.storage.move_to_completed(xml_path)
                self._record_system_event("INFO", "NON_DICOM_DUPLICATE", "XML duplicado foi ignorado")
                return existing_id
            parsed = parse_xml(xml_path.read_bytes())
            submission = parsed.submission
            file_path = self.storage.resolve_submission_file(submission)
            allowed_mime = {str(value).lower() for value in config.get("allowed_mime_types", [])}
            if submission.mime_type.lower() not in allowed_mime:
                raise NonDicomParseError("MIME type não permitido")
            if file_path.stat().st_size > int(config.get("max_file_size_mb", 50)) * 1024 * 1024:
                raise NonDicomParseError("Arquivo excede o limite configurado")
            submission_id = self.manager.create(submission, xml_path, file_path)
            self._last_received = datetime.now(UTC).isoformat()
            self._record_system_event("INFO", "NON_DICOM_RECEIVED", "XML Non-DICOM validado e colocado na fila")
            return submission_id
        except (OSError, NonDicomParseError, NonDicomStorageError) as exc:
            if xml_path.exists():
                self.storage.move_to_failed(xml_path)
            self._record_system_event("ERROR", "NON_DICOM_VALIDATION_FAILED", str(exc))
            LOGGER.warning("non_dicom_validation_failed", reason=str(exc))
            return None

    def scan_input(self) -> int:
        count = 0
        for xml_path in self.watcher.discover():
            if self.ingest_file(xml_path):
                count += 1
        return count

    async def sync_pending_from_cloud(self) -> int:
        """Busca itens no endpoint configurado e os converte em XMLs locais seguros para a fila."""
        client = self._client()
        if not client.configured:
            return 0
        try:
            response = await client.pending()
            items = response.get("items") or response.get("documents") or []
            if not isinstance(items, list):
                raise RuntimeError("Resposta de pendentes inválida")
            created = 0
            for entry in items:
                if not isinstance(entry, dict) or not entry.get("id"):
                    continue
                metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else await client.metadata(str(entry["id"]))
                content = await client.document(str(entry["id"]))
                unique_id = uuid.uuid4().hex
                file_name = safe_file_name(str(metadata.get("task_file_name") or metadata.get("file_name") or f"{unique_id}.bin"))
                directory = self.paths.files / unique_id
                directory.mkdir(parents=True, exist_ok=False)
                target = directory / file_name
                target.write_bytes(content)
                root = ET.Element("submission")
                document = ET.SubElement(root, "document")
                values = dict(metadata)
                values["task_file_path"] = str(target.relative_to(self.paths.files))
                values["task_file_name"] = file_name
                for key, value in values.items():
                    if key.startswith("task_") and value is not None:
                        ET.SubElement(document, key).text = str(value)
                xml_path = self.paths.input / f"{unique_id}.xml"
                ET.ElementTree(root).write(xml_path, encoding="utf-8", xml_declaration=True)
                created += 1
            self._last_sync = datetime.now(UTC).isoformat()
            self._cloud_status = "CONNECTED"
            return created
        except Exception:
            self._cloud_status = "DISCONNECTED"
            self._record_system_event("ERROR", "NON_DICOM_CLOUD_SYNC_FAILED", "Consulta de documentos Non-DICOM não foi concluída")
            return 0

    async def process_once(self) -> bool:
        item = self.manager.claim_next()
        if item is None:
            return False
        try:
            client = self._client()
            if not client.configured:
                raise RuntimeError("VOXEL PACS Non-DICOM não configurado")
            content = Path(str(item["file_path"])).read_bytes()
            response = await client.upload(json.loads(str(item["metadata_json"])), content, str(item["file_name"]), str(item["mime_type"]))
            await client.acknowledge(str(item["id"]), "COMPLETED")
            self.manager.complete(item, str(response.get("id") or response.get("reference") or "VOXEL_PACS_ACCEPTED"))
            if bool(self.configured.get("delete_file_after_success", False)):
                self.storage.delete_managed_file(Path(str(item["file_path"])))
            self._last_processed = datetime.now(UTC).isoformat()
            return True
        except Exception as exc:
            self.manager.fail(item, str(exc))
            self._record_system_event("ERROR", "NON_DICOM_PROCESSING_FAILED", "Processamento Non-DICOM falhou; item mantido para retry controlado")
            return False

    async def test_connection(self) -> dict[str, Any]:
        client = self._client()
        if not client.configured:
            self._cloud_status = "DISCONNECTED"
            return {"status": "DISCONNECTED", "detail": "URL do VOXEL PACS não configurada"}
        try:
            await client.status()
            self._cloud_status = "CONNECTED"
            return {"status": "CONNECTED", "detail": "Endpoint configurado respondeu"}
        except Exception:
            self._cloud_status = "DISCONNECTED"
            return {"status": "DISCONNECTED", "detail": "Endpoint configurado indisponível"}

    async def run(self) -> None:
        self._running = True
        self.manager.recover_after_restart()
        try:
            while self._running:
                await self.sync_pending_from_cloud()
                self.scan_input()
                while await self.process_once():
                    pass
                self._last_sync = datetime.now(UTC).isoformat()
                await asyncio.sleep(max(1, int(self.configured.get("polling_interval_seconds", 5))))
        finally:
            self._running = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="non-dicom-worker")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def status(self) -> dict[str, Any]:
        directory_ok = all(path.is_dir() for path in (self.paths.input, self.paths.processing, self.paths.completed, self.paths.failed, self.paths.retry, self.paths.files))
        return {
            "service": "ONLINE" if self._running else "OFFLINE",
            "directory": "OK" if directory_ok else "ERROR",
            "processor": "RUNNING" if self._running else "STOPPED",
            "voxel_pacs": self._cloud_status if self._client().configured else "DISCONNECTED",
            "last_synchronization": self._last_sync,
            "last_file_received": self._last_received,
            "last_processing": self._last_processed,
            "stats": self.manager.stats(),
        }
