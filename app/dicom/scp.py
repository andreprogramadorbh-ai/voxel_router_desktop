"""Servidor DICOM SCP para recepção local de C-ECHO e C-STORE."""

from __future__ import annotations

import io
import ipaddress
import threading
import time
from collections.abc import Callable
from typing import Any

from pynetdicom import AE, AllStoragePresentationContexts, VerificationPresentationContexts, evt
from pynetdicom.sop_class import Verification

from app.core.logging import get_logger
from app.dicom.access import DicomAccessControl
from app.dicom.ingest import DicomIngestor, DicomValidationError

LOGGER = get_logger(__name__)

# A-ASSOCIATE-RJ: rejected-permanent (0x01), DUL service user (0x01), diagnóstico.
REJECT_CALLING_AE_NOT_RECOGNISED = (0x01, 0x01, 0x03)
REJECT_CALLED_AE_NOT_RECOGNISED = (0x01, 0x01, 0x07)
# Status C-STORE "Refused: Not authorized".
STATUS_NOT_AUTHORIZED = 0x0124
# Status C-STORE "Refused: Out of resources": a modalidade mantém o objeto e tenta de novo.
STATUS_OUT_OF_RESOURCES = 0xA700
# Tempo para o PDU de rejeição chegar ao requisitante antes de o socket ser fechado.
REJECT_FLUSH_SECONDS = 0.1


class DicomScp:
    def __init__(
        self,
        ingestor: DicomIngestor,
        ae_title: str,
        port: int,
        max_associations: int = 8,
        orthanc_store: Callable[[bytes], str | None] | None = None,
        access: DicomAccessControl | None = None,
        bind_host: str = "0.0.0.0",
        accept_storage: Callable[[], bool] | None = None,
    ) -> None:
        self.ingestor = ingestor
        self.ae_title = ae_title
        self.port = port
        # Endereço literal IPv4: 0.0.0.0 (todas as interfaces, padrão de produção) ou um IP local específico.
        self.bind_host = str(ipaddress.IPv4Address(str(bind_host).strip()))
        self.max_associations = max_associations
        self.orthanc_store = orthanc_store
        self.access = access or DicomAccessControl(ingestor.database)
        self.accept_storage = accept_storage
        self._server: Any | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        # O servidor do pynetdicom é um socketserver (sem is_alive): existe enquanto não for parado.
        return self._server is not None

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
                (self.bind_host, self.port),
                block=False,
                evt_handlers=[
                    (evt.EVT_REQUESTED, self._handle_requested),
                    (evt.EVT_C_STORE, self._handle_store),
                    (evt.EVT_ACCEPTED, self._handle_accepted),
                    (evt.EVT_RELEASED, self._handle_released),
                ],
            )
            LOGGER.info("dicom_scp_started", ae_title=self.ae_title, bind_host=self.bind_host, port=self.port)

    def stop(self) -> None:
        with self._lock:
            if self._server is not None:
                self._server.shutdown()
                self._server = None
                LOGGER.info("dicom_scp_stopped", ae_title=self.ae_title, port=self.port)

    def _handle_requested(self, event: Any) -> None:
        """Decide a associação antes da negociação: nada é aceito (nem persistido) sem autorização.

        - Called AE diferente do AE do Router: recusa.
        - Só Verification (C-ECHO): aceita de qualquer origem, para diagnóstico.
        - Qualquer contexto de armazenamento: exige modalidade habilitada com Calling AE + IP/CIDR.
        Erros aqui recusam a associação (fail-closed).
        """
        assoc = event.assoc
        calling_ae = called_ae = source_ip = "?"
        try:
            primitive = assoc.requestor.primitive
            calling_ae = str(primitive.calling_ae_title).strip()
            called_ae = str(primitive.called_ae_title).strip()
            source_ip = str(assoc.requestor.address)
            contexts = primitive.presentation_context_definition_list or []
            echo_only = bool(contexts) and all(str(context.abstract_syntax) == str(Verification) for context in contexts)

            if called_ae != self.ae_title.strip():
                self.access.record_attempt(calling_ae, called_ae, source_ip, "CALLED_AE")
                self._reject(assoc, REJECT_CALLED_AE_NOT_RECOGNISED, "CALLED_AE", calling_ae, source_ip)
                return
            if self.access.is_allowed(calling_ae, source_ip):
                return
            if echo_only:
                self.access.record_attempt(calling_ae, called_ae, source_ip, "ECHO_ONLY")
                return
            self.access.record_attempt(calling_ae, called_ae, source_ip, "UNKNOWN_NODE")
            self._reject(assoc, REJECT_CALLING_AE_NOT_RECOGNISED, "UNKNOWN_NODE", calling_ae, source_ip)
        except Exception:
            LOGGER.exception("dicom_access_check_failed")
            self._reject(assoc, REJECT_CALLING_AE_NOT_RECOGNISED, "ACCESS_CHECK_ERROR", calling_ae, source_ip)

    @staticmethod
    def _reject(assoc: Any, reason_rsd: tuple[int, int, int], reason: str, calling_ae: str, source_ip: str) -> None:
        LOGGER.warning("dicom_association_rejected", reason=reason, calling_ae=calling_ae, source_ip=source_ip)
        try:
            assoc.acse.send_reject(*reason_rsd)
            time.sleep(REJECT_FLUSH_SECONDS)
        except Exception:
            # Se nem o RJ puder ser enviado, derruba a associação: nunca segue para a negociação.
            assoc.abort()

    def _handle_store(self, event: Any) -> int:
        # Segunda barreira: a modalidade pode ter sido desabilitada durante a associação.
        requestor = event.assoc.requestor
        if not self.access.is_allowed(str(requestor.ae_title), str(requestor.address)):
            LOGGER.warning("dicom_c_store_not_authorized", calling_ae=str(requestor.ae_title).strip(), source_ip=str(requestor.address))
            return STATUS_NOT_AUTHORIZED
        if self.accept_storage is not None and not self.accept_storage():
            LOGGER.warning("dicom_c_store_refused_disk_emergency")
            return STATUS_OUT_OF_RESOURCES
        try:
            dataset = event.dataset
            dataset.file_meta = event.file_meta
            buffer = io.BytesIO()
            dataset.save_as(buffer, enforce_file_format=True)
            raw_bytes = buffer.getvalue()
            orthanc_instance_id = self.orthanc_store(raw_bytes) if self.orthanc_store else None
            result = self.ingestor.receive_dataset(dataset, raw_bytes, orthanc_instance_id)
            LOGGER.info("dicom_c_store_accepted", duplicate=result["duplicate"])
            return 0x0000
        except DicomValidationError as exc:
            LOGGER.warning("dicom_c_store_invalid", error=str(exc))
            return 0xC210
        except Exception:
            LOGGER.exception("dicom_c_store_failure")
            return STATUS_OUT_OF_RESOURCES

    @staticmethod
    def _handle_accepted(event: Any) -> None:
        LOGGER.info("dicom_association_accepted", calling_ae=str(event.assoc.requestor.ae_title).strip())

    @staticmethod
    def _handle_released(event: Any) -> None:
        LOGGER.info("dicom_association_released")
