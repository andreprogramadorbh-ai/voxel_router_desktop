"""Cliente explícito da REST API do Orthanc e controle opcional do serviço Windows."""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_logger

LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class OrthancHealth:
    status: str
    detail: str | None = None


class OrthancClient:
    def __init__(self, base_url: str, timeout_seconds: int = 10, username: str | None = None, password: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds
        self.auth = (username, password) if username and password else None

    async def health(self) -> OrthancHealth:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, auth=self.auth) as client:
                response = await client.get(f"{self.base_url}/system")
                response.raise_for_status()
                system = response.json()
            return OrthancHealth("ONLINE", str(system.get("Version", "Orthanc disponível")))
        except httpx.HTTPError as exc:
            return OrthancHealth("OFFLINE", type(exc).__name__)

    async def store_instance(self, data: bytes) -> str | None:
        async with httpx.AsyncClient(timeout=self.timeout, auth=self.auth) as client:
            response = await client.post(f"{self.base_url}/instances", content=data, headers={"Content-Type": "application/dicom"})
            response.raise_for_status()
            payload = response.json()
        return payload.get("ID")

    def store_instance_sync(self, data: bytes) -> str | None:
        """Encaminha o objeto ao Orthanc durante o callback síncrono de C-STORE."""
        with httpx.Client(timeout=self.timeout, auth=self.auth) as client:
            response = client.post(f"{self.base_url}/instances", content=data, headers={"Content-Type": "application/dicom"})
            response.raise_for_status()
            return response.json().get("ID")

    async def study(self, orthanc_study_id: str) -> dict[str, Any]:
        return await self._get(f"/studies/{orthanc_study_id}")

    async def series(self, orthanc_series_id: str) -> dict[str, Any]:
        return await self._get(f"/series/{orthanc_series_id}")

    async def instance(self, orthanc_instance_id: str) -> dict[str, Any]:
        return await self._get(f"/instances/{orthanc_instance_id}")

    async def storage_statistics(self) -> dict[str, Any]:
        return await self._get("/statistics")

    async def _get(self, path: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout, auth=self.auth) as client:
            response = await client.get(f"{self.base_url}{path}")
            response.raise_for_status()
            return response.json()


class OrthancServiceController:
    """Controle do serviço exclusivamente em Windows; chamadas falham de forma segura nos demais SOs."""

    def __init__(self, service_name: str = "VOXELOrthanc") -> None:
        self.service_name = service_name

    def status(self) -> str:
        if platform.system() != "Windows":
            return "UNSUPPORTED"
        result = subprocess.run(["sc.exe", "query", self.service_name], capture_output=True, text=True, check=False, timeout=15)
        output = result.stdout.upper()
        if "RUNNING" in output:
            return "ONLINE"
        if "START_PENDING" in output:
            return "STARTING"
        return "OFFLINE"

    def start(self) -> None:
        self._control("start")

    def stop(self) -> None:
        self._control("stop")

    def restart(self) -> None:
        self.stop()
        self.start()

    def _control(self, action: str) -> None:
        if platform.system() != "Windows":
            raise RuntimeError("Controle de serviço Orthanc só está disponível no Windows")
        result = subprocess.run(["sc.exe", action, self.service_name], capture_output=True, text=True, check=False, timeout=30)
        if result.returncode != 0:
            LOGGER.warning("orthanc_service_control_failed", action=action, returncode=result.returncode)
            raise RuntimeError("Não foi possível controlar o serviço Orthanc")
