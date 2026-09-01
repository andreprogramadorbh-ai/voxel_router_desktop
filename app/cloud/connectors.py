"""Contratos de cloud/destino e adaptador DICOM C-STORE desacoplado da Engine."""

from __future__ import annotations

import asyncio
import ssl
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydicom import dcmread
from pynetdicom import AE

from app.core.database import Database
from app.core.logging import get_logger

LOGGER = get_logger(__name__)


class CloudConnectorError(RuntimeError):
    pass


@dataclass(frozen=True)
class TransferResult:
    bytes_sent: int
    instances_sent: int
    remote_reference: str | None = None
    validation_reference: str | None = None


class CloudConnector(ABC):
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def authenticate(self) -> None: ...

    @abstractmethod
    async def health_check(self) -> str: ...

    @abstractmethod
    async def send_study(self, study_id: int) -> TransferResult: ...

    @abstractmethod
    async def get_status(self) -> str: ...

    @abstractmethod
    async def disconnect(self) -> None: ...


class UnavailableCloudConnector(CloudConnector):
    """Conector seguro quando não há destino provisionado: mantém a fila em retry."""

    async def connect(self) -> None:
        raise CloudConnectorError("CLOUD_UNAVAILABLE")

    async def authenticate(self) -> None:
        raise CloudConnectorError("CLOUD_UNAVAILABLE")

    async def health_check(self) -> str:
        return "DISCONNECTED"

    async def send_study(self, study_id: int) -> TransferResult:
        raise CloudConnectorError("VOXEL Cloud indisponível ou não configurada")

    async def get_status(self) -> str:
        return "DISCONNECTED"

    async def disconnect(self) -> None:
        return None


class DicomCloudConnector(CloudConnector):
    """Envia todas as instâncias persistidas de um estudo a um destino C-STORE."""

    def __init__(self, database: Database, storage_root: Path, destination_id: int, calling_ae_title: str) -> None:
        self.database = database
        self.storage_root = storage_root / "objects"
        self.destination_id = destination_id
        self.calling_ae_title = calling_ae_title
        self._destination: dict[str, Any] | None = None

    async def connect(self) -> None:
        row = self.database.query_one("SELECT id, name, kind, ae_title, host, port, tls_enabled, enabled FROM destinations WHERE id = ?", (self.destination_id,))
        if row is None or not row["enabled"]:
            raise CloudConnectorError("DESTINATION_UNAVAILABLE")
        if row["kind"] != "DICOM" or not row["host"] or not row["port"] or not row["ae_title"]:
            raise CloudConnectorError("DESTINATION_INVALID")
        self._destination = dict(row)

    async def authenticate(self) -> None:
        # A autenticação DICOM é feita pela associação e, quando configurado, pela camada TLS.
        if self._destination is None:
            await self.connect()

    async def health_check(self) -> str:
        try:
            await asyncio.to_thread(self._echo)
            return "CONNECTED"
        except CloudConnectorError:
            return "DISCONNECTED"

    async def send_study(self, study_id: int) -> TransferResult:
        if self._destination is None:
            await self.connect()
        return await asyncio.to_thread(self._send_sync, study_id)

    async def get_status(self) -> str:
        return await self.health_check()

    async def disconnect(self) -> None:
        self._destination = None

    def _echo(self) -> None:
        destination = self._require_destination()
        ae = AE(ae_title=self.calling_ae_title)
        assoc = ae.associate(destination["host"], int(destination["port"]), ae_title=destination["ae_title"], **self._tls_options(destination))
        if not assoc.is_established:
            raise CloudConnectorError("DESTINATION_UNAVAILABLE")
        try:
            status = assoc.send_c_echo()
            if not status or status.Status != 0x0000:
                raise CloudConnectorError("DESTINATION_ECHO_FAILED")
        finally:
            assoc.release()

    def _send_sync(self, study_id: int) -> TransferResult:
        destination = self._require_destination()
        rows = self.database.query_all("SELECT sop_class_uid, sha256, size_bytes FROM instances WHERE study_id = ? ORDER BY id", (study_id,))
        if not rows:
            raise CloudConnectorError("STUDY_EMPTY")
        ae = AE(ae_title=self.calling_ae_title)
        for row in rows:
            ae.add_requested_context(str(row["sop_class_uid"]))
        assoc = ae.associate(destination["host"], int(destination["port"]), ae_title=destination["ae_title"], **self._tls_options(destination))
        if not assoc.is_established:
            raise CloudConnectorError("DESTINATION_UNAVAILABLE")
        sent_bytes = 0
        sent_instances = 0
        try:
            for row in rows:
                path = self.storage_root / str(row["sha256"])[:2] / f"{row['sha256']}.dcm"
                if not path.is_file():
                    raise CloudConnectorError("LOCAL_OBJECT_MISSING")
                dataset = dcmread(path)
                status = assoc.send_c_store(dataset)
                if status is None or status.Status not in (0x0000, 0xB000, 0xB006, 0xB007):
                    code = "NO_STATUS" if status is None else f"CSTORE_{status.Status:04X}"
                    raise CloudConnectorError(code)
                sent_bytes += int(row["size_bytes"])
                sent_instances += 1
        finally:
            assoc.release()
        return TransferResult(sent_bytes, sent_instances, remote_reference=str(destination["name"]), validation_reference="C-STORE response accepted")

    @staticmethod
    def _tls_options(destination: dict[str, Any]) -> dict[str, Any]:
        if not destination.get("tls_enabled"):
            return {}
        cert_path = Path(str(destination.get("certificate_path", "")))
        key_path = Path(str(destination.get("private_key_path", "")))
        ca_path = Path(str(destination.get("ca_path", "")))
        if not (cert_path.is_file() and key_path.is_file() and ca_path.is_file()):
            raise CloudConnectorError("DICOM_TLS_CERTIFICATE_MISSING")
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(ca_path))
        context.load_cert_chain(str(cert_path), str(key_path))
        return {"tls_args": (context, str(destination["host"]))}

    def _require_destination(self) -> dict[str, Any]:
        if self._destination is None:
            raise CloudConnectorError("DESTINATION_UNAVAILABLE")
        return self._destination
