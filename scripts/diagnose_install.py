"""Diagnóstico pós-instalação não destrutivo do VOXEL Router e Orthanc."""

from __future__ import annotations

import asyncio
import json
import platform
import socket
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.settings import Settings
from app.orthanc.client import OrthancServiceController
from app.security.secrets import SecretStoreError, WindowsSecretStore


@dataclass(frozen=True)
class DiagnosticCheck:
    name: str
    status: str
    detail: str
    required: bool = True


def tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def http_status(url: str, auth: tuple[str, str] | None = None) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=5, auth=auth) as client:
            response = await client.get(url)
            response.raise_for_status()
        return True, str(response.status_code)
    except httpx.HTTPError as exc:
        return False, type(exc).__name__


async def diagnose(settings: Settings | None = None) -> list[DiagnosticCheck]:
    current = settings or Settings()
    paths = current.paths
    orthanc_url = str(current.get("orthanc", "url", default="http://127.0.0.1:8042")).rstrip("/")
    router_url = f"http://{current.get('api', 'host', default='127.0.0.1')}:{int(current.get('api', 'port', default=8765))}"
    checks = [
        DiagnosticCheck("Router instalado", "OK" if (Path(sys.executable).parent / "VOXELRouter.exe").exists() or not getattr(sys, "frozen", False) else "FALHA", "Binário VOXELRouter.exe"),
        DiagnosticCheck("Orthanc instalado", "OK" if (Path(sys.executable).parent / "orthanc" / "Orthanc.exe").exists() or not getattr(sys, "frozen", False) else "FALHA", "Binário Orthanc.exe"),
        DiagnosticCheck("Storage Orthanc", "OK" if paths.orthanc_storage.is_dir() and paths.orthanc_database.is_dir() else "FALHA", str(paths.orthanc_root)),
        DiagnosticCheck("Porta Router DICOM 4242", "OK" if tcp_open("127.0.0.1", int(current.get("dicom", "port", default=4242))) else "FALHA", "TCP 4242"),
        DiagnosticCheck("Porta Orthanc DICOM 4243", "OK" if tcp_open("127.0.0.1", int(current.get("orthanc", "dicom_port", default=4243))) else "FALHA", "TCP 4243"),
        DiagnosticCheck("Porta Orthanc REST 8042", "OK" if tcp_open("127.0.0.1", int(current.get("orthanc", "http_port", default=8042))) else "FALHA", "TCP 8042 localhost"),
        DiagnosticCheck("Porta Router HTTP 8765", "OK" if tcp_open("127.0.0.1", int(current.get("api", "port", default=8765))) else "FALHA", "TCP 8765 localhost"),
    ]
    if platform.system() == "Windows":
        for name, service in (("Serviço VOXEL Router", "VOXELRouter"), ("Serviço VOXEL Orthanc", "VOXELOrthanc")):
            status = OrthancServiceController(service).status()
            checks.append(DiagnosticCheck(name, "OK" if status == "ONLINE" else "FALHA", status))

    router_ok, router_detail = await http_status(f"{router_url}/health")
    checks.append(DiagnosticCheck("Health check Router", "OK" if router_ok else "FALHA", router_detail))

    orthanc_auth = None
    try:
        password = WindowsSecretStore(paths).get("orthanc.internal.password")
        if password:
            orthanc_auth = ("voxel-router-internal", password)
    except SecretStoreError:
        pass
    orthanc_ok, orthanc_detail = await http_status(f"{orthanc_url}/system", orthanc_auth)
    checks.append(DiagnosticCheck("Health check Orthanc", "OK" if orthanc_ok else "FALHA", orthanc_detail))
    return checks


def main() -> None:
    checks = asyncio.run(diagnose())
    payload = {"checks": [asdict(check) for check in checks]}
    settings = Settings()
    report = settings.paths.logs / "install-diagnostic.json"
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    if any(check.required and check.status != "OK" for check in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
