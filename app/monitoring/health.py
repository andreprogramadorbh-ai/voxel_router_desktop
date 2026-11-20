"""Health checks não destrutivos para dashboard e endpoints internos."""

from __future__ import annotations

import asyncio
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psutil

from app.cloud.connectors import echo_verified, outbound_ae_title
from app.config.settings import Settings
from app.dicom.scp import DicomScp
from app.orthanc.client import OrthancClient
from app.queue.manager import QueueManager

# Sem verificação ativa (Fase 6), "recente" é o que justifica mostrar a conexão como saudável.
CLOUD_RECENT_HOURS = 24


def storage_status(settings: Settings) -> dict[str, Any]:
    """Uso do disco onde ficam os objetos do Router, com o nível de alerta configurado."""
    usage = psutil.disk_usage(Path(settings.paths.storage).anchor or "/")
    percent = float(usage.percent)
    if percent >= float(settings.get("storage", "emergency_percent", default=95)):
        status = "EMERGENCY"
    elif percent >= float(settings.get("storage", "critical_percent", default=85)):
        status = "CRITICAL"
    elif percent >= float(settings.get("storage", "warning_percent", default=70)):
        status = "WARNING"
    else:
        status = "OK"
    return {"status": status, "percent": percent, "total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free}


class HealthMonitor:
    def __init__(self, settings: Settings, orthanc: OrthancClient, scp: DicomScp, non_dicom: Any | None = None, queue: QueueManager | None = None, vpn: Any | None = None) -> None:
        self.settings = settings
        self.vpn = vpn
        self.orthanc = orthanc
        self.scp = scp
        self.non_dicom = non_dicom
        self.queue = queue

    async def snapshot(self) -> dict[str, Any]:
        orthanc = await self.orthanc.health()
        storage = self.storage()
        return {
            "router": {"status": "ONLINE"},
            # OrthancHealth é obtido por requisição REST real a /system, nunca por estado simulado.
            "orthanc": {
                "status": orthanc.status,
                "detail": orthanc.detail,
                "http_port": int(self.settings.get("orthanc", "http_port", default=8042)),
            },
            "cloud": self.cloud(),
            "dicom": {"status": "ONLINE" if self.scp.running else "OFFLINE", "ae_title": self.scp.ae_title, "port": self.scp.port, **self.dicom_access()},
            "storage": storage,
            # Atraso de envio: pendência mais antiga, alerta configurável e último envio confirmado.
            "queue": self.queue.backlog_status() if self.queue is not None else None,
            "non_dicom": self.non_dicom.status() if self.non_dicom is not None else {"service": "OFFLINE", "directory": "ERROR", "processor": "STOPPED", "voxel_pacs": "DISCONNECTED", "stats": {}},
            "network": await self.network(),
            # Túnel VOXEL VPN: estado do serviço do WireGuard e idade do último handshake.
            "vpn": await asyncio.to_thread(self.vpn.status) if self.vpn is not None else None,
        }

    def cloud(self) -> dict[str, Any]:
        """Estado do envio ao VOXEL a partir de fatos registrados (C-ECHO e envios), sem teste ativo.

        ONLINE só com destino DICOM habilitado, C-ECHO válido para a configuração atual e sucesso
        (C-ECHO ou envio) nas últimas CLOUD_RECENT_HOURS horas, sem falha de envio depois dele.
        """
        result: dict[str, Any] = {"status": "DISCONNECTED", "detail": "Nenhum destino DICOM habilitado com C-ECHO válido", "last_success_at": None}
        if self.queue is None:
            return result
        database = self.queue.database
        calling_ae = outbound_ae_title(self.settings)
        rows = database.query_all("SELECT * FROM destinations WHERE kind = 'DICOM' AND enabled = 1")
        active = [row for row in rows if calling_ae and echo_verified(calling_ae, row)]
        if not active:
            return result
        ids = [int(row["id"]) for row in active]
        marks = ",".join("?" * len(ids))
        sent = database.query_one(
            f"SELECT MAX(CASE WHEN t.status = 'SUCCESS' THEN t.completed_at END) AS ok, MAX(CASE WHEN t.status = 'FAILED' THEN t.completed_at END) AS failed FROM transfers t JOIN queue q ON q.id = t.queue_id WHERE q.destination_id IN ({marks})",
            tuple(ids),
        )
        # Datas do SQLite ("AAAA-MM-DD HH:MM:SS", UTC) comparam corretamente como texto.
        last_success = max([str(row["echo_at"]) for row in active if row["echo_at"]] + ([str(sent["ok"])] if sent["ok"] else []), default=None)
        result["last_success_at"] = last_success
        recent = last_success is not None and datetime.strptime(last_success, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC) >= datetime.now(UTC) - timedelta(hours=CLOUD_RECENT_HOURS)
        if not recent:
            result.update(status="WARNING", detail=f"Sem C-ECHO ou envio com sucesso nas últimas {CLOUD_RECENT_HOURS} h")
        elif sent["failed"] and str(sent["failed"]) > last_success:
            result.update(status="WARNING", detail="Último envio falhou depois do último sucesso")
        else:
            result.update(status="ONLINE", detail="Destino habilitado com sucesso recente")
        return result

    def dicom_access(self) -> dict[str, Any]:
        """Sem modalidade habilitada, todo C-STORE é recusado: o dashboard precisa deixar isso explícito."""
        access = getattr(self.scp, "access", None)
        if access is None:
            return {}
        return {"enabled_modalities": access.node_counts()["enabled"], "learning_mode_until": access.learning_until()}

    async def network(self) -> dict[str, str]:
        dns = "OK"
        internet = "OK"
        try:
            await asyncio.to_thread(socket.gethostbyname, "one.one.one.one")
        except OSError:
            dns = "OFFLINE"
            internet = "OFFLINE"
        return {"local_network": "OK" if psutil.net_if_stats() else "OFFLINE", "dns": dns, "internet": internet}

    def storage(self) -> dict[str, Any]:
        status = storage_status(self.settings)
        # Em EMERGENCY o SCP recusa C-STORE (0xA700) para a modalidade reenviar depois.
        status["accepting_c_store"] = status["status"] != "EMERGENCY"
        return status
