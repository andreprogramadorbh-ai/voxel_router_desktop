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
from pynetdicom.sop_class import Verification

from app.core.database import Database
from app.core.logging import get_logger

LOGGER = get_logger(__name__)


class CloudConnectorError(RuntimeError):
    """Falha de envio identificada por código curto.

    `permanent=True` indica rejeição definitiva (destino recusou o objeto, objeto local ausente ou
    corrompido, destino inválido): o item vai para ERROR. Demais falhas são de conectividade e o
    item permanece em RETRY indefinidamente.
    """

    def __init__(self, code: str, permanent: bool = False) -> None:
        super().__init__(code)
        self.permanent = permanent


# Status C-STORE aceitos (sucesso e avisos de coerção/elementos descartados).
CSTORE_ACCEPTED = frozenset({0x0000, 0xB000, 0xB006, 0xB007})


def cstore_failure(status_code: int) -> CloudConnectorError:
    """Classifica um status de falha C-STORE devolvido pelo destino."""
    # 0xA7xx (Out of Resources) e 0x0213 (Resource limitation) indicam falta de recurso
    # temporária no destino: tentar de novo, como se espera de qualquer SCU.
    transient = (status_code & 0xFF00) == 0xA700 or status_code == 0x0213
    return CloudConnectorError(f"CSTORE_{status_code:04X}", permanent=not transient)


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


def outbound_ae_title(settings: Any) -> str:
    """Calling AE usado para o VOXEL: por instalação e separado do AE local do SCP. Vazio = não configurado."""
    return str(settings.get("dicom", "outbound_ae_title", default="") or "").strip()


def echo_target(calling_ae_title: str, destination: Any) -> str:
    """Identidade que o C-ECHO comprovou: mudar qualquer parte exige novo teste antes de enviar."""
    return "|".join(
        str(part).strip()
        for part in (calling_ae_title, destination["ae_title"] or "", destination["host"] or "", destination["port"] or "", int(bool(destination["tls_enabled"])))
    )


def echo_verified(calling_ae_title: str, destination: Any) -> bool:
    return destination["echo_status"] == "SUCCESS" and destination["echo_target"] == echo_target(calling_ae_title, destination)


class DicomCloudConnector(CloudConnector):
    """Envia todas as instâncias persistidas de um estudo a um destino C-STORE."""

    def __init__(
        self,
        database: Database,
        storage_root: Path,
        destination_id: int,
        calling_ae_title: str,
        association_timeout_seconds: int = 15,
        network_timeout_seconds: int = 30,
    ) -> None:
        self.database = database
        self.storage_root = storage_root / "objects"
        self.destination_id = destination_id
        self.calling_ae_title = calling_ae_title
        self.association_timeout_seconds = association_timeout_seconds
        self.network_timeout_seconds = network_timeout_seconds
        self._destination: dict[str, Any] | None = None

    async def connect(self) -> None:
        row = self._load()
        if not row["enabled"]:
            # Destino pausado pelo operador: o estudo aguarda em RETRY até ser reabilitado.
            raise CloudConnectorError("DESTINATION_DISABLED")
        if not echo_verified(self.calling_ae_title, row):
            # Ex.: Calling AE alterado direto no router.json. Aguarda em RETRY até novo C-ECHO.
            raise CloudConnectorError("DESTINATION_NOT_VERIFIED")
        self._destination = dict(row)

    async def test_echo(self) -> None:
        """C-ECHO de verificação, inclusive com o destino pausado (é o que libera habilitá-lo)."""
        self._destination = dict(self._load())
        await asyncio.to_thread(self._echo)

    def _load(self) -> Any:
        row = self.database.query_one(
            "SELECT id, name, kind, ae_title, host, port, tls_enabled, enabled, echo_status, echo_target FROM destinations WHERE id = ?",
            (self.destination_id,),
        )
        if row is None:
            raise CloudConnectorError("DESTINATION_INVALID", permanent=True)
        if row["kind"] != "DICOM" or not row["host"] or not row["port"] or not row["ae_title"]:
            raise CloudConnectorError("DESTINATION_INVALID", permanent=True)
        if not self.calling_ae_title.strip():
            raise CloudConnectorError("OUTBOUND_AE_NOT_CONFIGURED")
        return row

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
        ae = self._new_ae()
        ae.add_requested_context(Verification)
        assoc = self._associate(ae, destination)
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
            raise CloudConnectorError("STUDY_EMPTY", permanent=True)
        ae = self._new_ae()
        for row in rows:
            ae.add_requested_context(str(row["sop_class_uid"]))
        assoc = self._associate(ae, destination)
        sent_bytes = 0
        sent_instances = 0
        try:
            for row in rows:
                path = self.storage_root / str(row["sha256"])[:2] / f"{row['sha256']}.dcm"
                if not path.is_file():
                    raise CloudConnectorError("LOCAL_OBJECT_MISSING", permanent=True)
                try:
                    dataset = dcmread(path)
                except Exception as exc:
                    raise CloudConnectorError("LOCAL_OBJECT_CORRUPT", permanent=True) from exc
                try:
                    status = assoc.send_c_store(dataset)
                except (OSError, RuntimeError) as exc:
                    raise CloudConnectorError("ASSOCIATION_LOST") from exc
                # Sem status (None ou Dataset vazio): associação abortada ou timeout DIMSE.
                if not status or "Status" not in status:
                    raise CloudConnectorError("NO_STATUS")
                if status.Status not in CSTORE_ACCEPTED:
                    raise cstore_failure(int(status.Status))
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
            raise CloudConnectorError("DICOM_TLS_CERTIFICATE_MISSING", permanent=True)
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(ca_path))
        context.load_cert_chain(str(cert_path), str(key_path))
        return {"tls_args": (context, str(destination["host"]))}

    def _new_ae(self) -> AE:
        ae = AE(ae_title=self.calling_ae_title)
        # Sem timeout explícito, um túnel fora do ar prende a associação até o timeout do SO.
        ae.connection_timeout = self.association_timeout_seconds
        ae.acse_timeout = self.association_timeout_seconds
        ae.dimse_timeout = self.network_timeout_seconds
        ae.network_timeout = self.network_timeout_seconds
        return ae

    def _associate(self, ae: AE, destination: dict[str, Any]) -> Any:
        assoc = ae.associate(destination["host"], int(destination["port"]), ae_title=destination["ae_title"], **self._tls_options(destination))
        if assoc.is_established:
            return assoc
        # Rejeição, abort ou ausência de resposta não provam que o estudo é inaceitável:
        # tratamos como conectividade (RETRY) e deixamos o código visível ao operador.
        raise CloudConnectorError("DESTINATION_REJECTED" if assoc.is_rejected else "DESTINATION_UNAVAILABLE")

    def _require_destination(self) -> dict[str, Any]:
        if self._destination is None:
            raise CloudConnectorError("DESTINATION_UNAVAILABLE")
        return self._destination
