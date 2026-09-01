"""Servidor DICOM SCP para recepção local de C-ECHO e C-STORE."""

from __future__ import annotations

import io
import threading
from collections.abc import Callable
from typing import Any

from pynetdicom import AE, AllStoragePresentationContexts, VerificationPresentationContexts, evt

from app.core.logging import get_logger
from app.dicom.ingest import DicomIngestor, DicomValidationError

LOGGER = get_logger(__name__)


class DicomScp:
    def __init__(self, ingestor: DicomIngestor, ae_title: str, port: int, max_associations: int = 8, orthanc_store: Callable[[bytes], str | None] | None = None) -> None:
        self.ingestor = ingestor
        self.ae_title = ae_title
        self.port = port
        self.max_associations = max_associations
        self.orthanc_store = orthanc_store
        self._server: Any | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._server is not None and bool(getattr(self._server, "is_alive", lambda: False)())

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            ae = AE(ae_title=self.ae_title)
            ae.maximum_associations = self.max_associations
            for context in VerificationPresentationContexts:
                ae.add_supported_context(context.abstract_syntax, context.transfer_syntax)
            for context in AllStoragePresentationContexts:
                ae.add_supported_context(context.abstract_syntax, context.transfer_syntax)
            self._server = ae.start_server(
                ("0.0.0.0", self.port),
                block=False,
                evt_handlers=[(evt.EVT_C_STORE, self._handle_store), (evt.EVT_ACCEPTED, self._handle_accepted), (evt.EVT_RELEASED, self._handle_released)],
            )
            LOGGER.info("dicom_scp_started", ae_title=self.ae_title, port=self.port)

    def stop(self) -> None:
        with self._lock:
            if self._server is not None:
                self._server.shutdown()
                self._server = None
                LOGGER.info("dicom_scp_stopped", ae_title=self.ae_title, port=self.port)

    def _handle_store(self, event: Any) -> int:
        try:
            dataset = event.dataset
            dataset.file_meta = event.file_meta
            buffer = io.BytesIO()
            dataset.save_as(buffer, enforce_file_format=True)
            raw_bytes = buffer.getvalue()
            orthanc_instance_id = self.orthanc_store(raw_bytes) if self.orthanc_store else None
            result = self.ingestor.receive_dataset(dataset, raw_bytes, orthanc_instance_id)
            LOGGER.info("dicom_c_store_accepted", study_instance_uid=result["study_instance_uid"], duplicate=result["duplicate"])
            return 0x0000
        except DicomValidationError as exc:
            LOGGER.warning("dicom_c_store_invalid", error=str(exc))
            return 0xC210
        except Exception:
            LOGGER.exception("dicom_c_store_failure")
            return 0xA700

    @staticmethod
    def _handle_accepted(event: Any) -> None:
        LOGGER.info("dicom_association_accepted", calling_ae=str(event.assoc.requestor.ae_title).strip())

    @staticmethod
    def _handle_released(event: Any) -> None:
        LOGGER.info("dicom_association_released")
