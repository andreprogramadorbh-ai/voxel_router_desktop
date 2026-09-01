"""Worker assíncrono de transmissão com limite de concorrência e retry persistente."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from app.cloud.connectors import CloudConnector, CloudConnectorError, DicomCloudConnector, UnavailableCloudConnector
from app.config.settings import Settings
from app.core.database import Database
from app.core.logging import get_logger
from app.queue.manager import QueueItem, QueueManager

LOGGER = get_logger(__name__)


class TransferManager:
    def __init__(self, database: Database, settings: Settings, queue: QueueManager, connector_factory: Callable[[int], CloudConnector] | None = None) -> None:
        self.database = database
        self.settings = settings
        self.queue = queue
        self.connector_factory = connector_factory or self._default_connector
        self._stop_event = asyncio.Event()
        self._semaphore = asyncio.Semaphore(int(settings.get("queue", "max_concurrent_transfers", default=2)))
        self._tasks: set[asyncio.Task[None]] = set()

    async def run(self, poll_interval_seconds: float = 1.0) -> None:
        self._stop_event.clear()
        while not self._stop_event.is_set():
            item = self.queue.claim_next()
            if item is None:
                await asyncio.sleep(poll_interval_seconds)
                continue
            task = asyncio.create_task(self._process(item))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            await asyncio.sleep(0)
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def stop(self) -> None:
        self._stop_event.set()

    async def process_once(self) -> bool:
        item = self.queue.claim_next()
        if item is None:
            return False
        await self._process(item)
        return True

    async def _process(self, item: QueueItem) -> None:
        async with self._semaphore:
            connector = self.connector_factory(item.destination_id)
            try:
                await connector.connect()
                await connector.authenticate()
                result = await connector.send_study(item.study_id)
                self.queue.complete(item, result.bytes_sent, result.instances_sent, result.remote_reference, result.validation_reference)
            except CloudConnectorError as exc:
                self.queue.fail(item, self._error_code(exc), "Destino indisponível; o estudo permanecerá armazenado e será reenviado automaticamente.")
            except Exception:
                LOGGER.exception("transfer_unexpected_failure", queue_id=item.id)
                self.queue.fail(item, "TRANSFER_UNEXPECTED", "Falha inesperada na transmissão; o estudo será reenviado automaticamente.")
            finally:
                try:
                    await connector.disconnect()
                except Exception:
                    LOGGER.warning("connector_disconnect_failed", queue_id=item.id)

    def _default_connector(self, destination_id: int) -> CloudConnector:
        destination = self.database.query_one("SELECT kind FROM destinations WHERE id = ?", (destination_id,))
        if destination and destination["kind"] == "DICOM":
            return DicomCloudConnector(self.database, self.settings.paths.storage, destination_id, str(self.settings.get("dicom", "ae_title", default="VOXEL_ROUTER")))
        return UnavailableCloudConnector()

    @staticmethod
    def _error_code(exc: CloudConnectorError) -> str:
        value = str(exc)
        return value if value.replace("_", "").isalnum() and len(value) <= 64 else "CLOUD_UNAVAILABLE"
