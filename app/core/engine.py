"""Orquestração resiliente do VOXEL Router Engine."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config.settings import Settings
from app.core.database import Database
from app.core.logging import get_logger
from app.dicom.ingest import DicomIngestor
from app.dicom.scp import DicomScp
from app.monitoring.health import HealthMonitor
from app.orthanc.client import OrthancClient
from app.queue.manager import QueueManager
from app.security.secrets import SecretStoreError, WindowsSecretStore
from app.transfer.manager import TransferManager

LOGGER = get_logger(__name__)


class RouterEngine:
    def __init__(self, settings: Settings | None = None, database: Database | None = None) -> None:
        self.settings = settings or Settings()
        self.database = database or Database(self.settings.paths)
        self.ingestor = DicomIngestor(self.database, self.settings.paths)
        orthanc_password = None
        try:
            orthanc_password = WindowsSecretStore(self.settings.paths).get("orthanc.internal.password")
        except SecretStoreError:
            # Instalações de desenvolvimento podem não possuir DPAPI/cofre inicializado.
            pass
        self.orthanc = OrthancClient(
            str(self.settings.get("orthanc", "url", default="http://127.0.0.1:8042")),
            int(self.settings.get("orthanc", "timeout_seconds", default=10)),
            "voxel-router-internal" if orthanc_password else None,
            orthanc_password,
        )
        self.scp = DicomScp(
            self.ingestor,
            str(self.settings.get("dicom", "ae_title", default="VOXEL_ROUTER")),
            int(self.settings.get("dicom", "port", default=4242)),
            int(self.settings.get("dicom", "max_associations", default=8)),
            self.orthanc.store_instance_sync if bool(self.settings.get("orthanc", "enabled", default=True)) else None,
        )
        self.queue = QueueManager(self.database, list(self.settings.get("queue", "retry_delays_seconds", default=[30, 120, 300, 900])), int(self.settings.get("queue", "max_attempts", default=4)))
        self.transfer = TransferManager(self.database, self.settings, self.queue)
        self.health = HealthMonitor(self.settings, self.orthanc, self.scp)
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []

    def initialize(self, start_scp: bool = True) -> dict[str, int]:
        """Inicialização idempotente em ordem segura, apta a recuperação após reboot."""
        self.database.initialize()
        recovered = self.queue.recover_after_restart()
        ready = self.queue.mark_complete_studies_ready(int(self.settings.get("dicom", "study_quiet_window_seconds", default=30)))
        enqueued = self.queue.enqueue_ready_studies()
        if start_scp:
            self.scp.start()
        LOGGER.info("router_engine_initialized", router_id=self.settings.get("system", "router_id"), recovered_items=recovered, ready_studies=ready, enqueued_items=enqueued)
        return {"recovered": recovered, "ready": ready, "enqueued": enqueued}

    async def run(self) -> None:
        self.initialize()
        self._stop.clear()
        self._tasks = [asyncio.create_task(self.transfer.run()), asyncio.create_task(self._reconcile_loop()), asyncio.create_task(self._retention_loop())]
        try:
            await self._stop.wait()
        finally:
            await self.stop()

    async def stop(self) -> None:
        if self._stop.is_set() and not self._tasks:
            return
        self._stop.set()
        await self.transfer.stop()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        self.scp.stop()
        LOGGER.info("router_engine_stopped")

    async def reconcile_once(self) -> dict[str, int]:
        ready = self.queue.mark_complete_studies_ready(int(self.settings.get("dicom", "study_quiet_window_seconds", default=30)))
        enqueued = self.queue.enqueue_ready_studies()
        return {"ready": ready, "enqueued": enqueued}

    async def _reconcile_loop(self) -> None:
        while not self._stop.is_set():
            await self.reconcile_once()
            await asyncio.sleep(5)

    async def _retention_loop(self) -> None:
        while not self._stop.is_set():
            self.apply_retention_policy()
            await asyncio.sleep(3600)

    def apply_retention_policy(self) -> int:
        if not bool(self.settings.get("storage", "auto_delete", default=False)):
            return 0
        hours = int(self.settings.get("storage", "retention_hours", default=168))
        cutoff = (datetime.now(UTC) - timedelta(hours=max(hours, 0))).strftime("%Y-%m-%d %H:%M:%S")
        with self.database.transaction() as connection:
            rows = connection.execute(
                """SELECT i.id, i.sha256 FROM instances i JOIN studies s ON s.id = i.study_id
                   WHERE s.status = 'VALIDATED' AND s.validated_at < ?""",
                (cutoff,),
            ).fetchall()
            for row in rows:
                path = self.ingestor.instance_path(str(row["sha256"]))
                path.unlink(missing_ok=True)
                connection.execute("DELETE FROM instances WHERE id = ?", (row["id"],))
            return len(rows)
