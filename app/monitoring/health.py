"""Health checks não destrutivos para dashboard e endpoints internos."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from typing import Any

import psutil

from app.cloud.connectors import CloudConnector, UnavailableCloudConnector
from app.config.settings import Settings
from app.dicom.scp import DicomScp
from app.orthanc.client import OrthancClient


class HealthMonitor:
    def __init__(self, settings: Settings, orthanc: OrthancClient, scp: DicomScp, cloud: CloudConnector | None = None) -> None:
        self.settings = settings
        self.orthanc = orthanc
        self.scp = scp
        self.cloud = cloud or UnavailableCloudConnector()

    async def snapshot(self) -> dict[str, Any]:
        orthanc = await self.orthanc.health()
        cloud_status = await self.cloud.health_check()
        storage = self.storage()
        return {
            "router": {"status": "ONLINE"},
            "orthanc": {"status": orthanc.status, "detail": orthanc.detail},
            "cloud": {"status": cloud_status},
            "dicom": {"status": "LISTENING" if self.scp.running else "OFFLINE", "ae_title": self.scp.ae_title, "port": self.scp.port},
            "storage": storage,
            "network": await self.network(),
        }

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
        usage = psutil.disk_usage(Path(self.settings.paths.storage).anchor or "/")
        percent = float(usage.percent)
        if percent >= float(self.settings.get("storage", "emergency_percent", default=95)):
            status = "EMERGENCY"
        elif percent >= float(self.settings.get("storage", "critical_percent", default=85)):
            status = "CRITICAL"
        elif percent >= float(self.settings.get("storage", "warning_percent", default=70)):
            status = "WARNING"
        else:
            status = "OK"
        return {"status": status, "percent": percent, "total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free}
