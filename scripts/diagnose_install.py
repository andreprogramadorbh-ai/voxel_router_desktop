"""Diagnóstico pós-instalação não destrutivo do VOXEL Router e Orthanc."""

from __future__ import annotations

import argparse
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

from app.config.settings import Settings  # noqa: E402
from app.orthanc.client import OrthancServiceController  # noqa: E402
from app.security.secrets import SecretStoreError, WindowsSecretStore  # noqa: E402

STARTUP_ATTEMPTS = 15
STARTUP_DELAY_SECONDS = 1.0


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


async def wait_for_tcp(host: str, port: int) -> bool:
    for attempt in range(STARTUP_ATTEMPTS):
        if tcp_open(host, port):
            return True
        if attempt < STARTUP_ATTEMPTS - 1:
            await asyncio.sleep(STARTUP_DELAY_SECONDS)
    return False


async def http_status(url: str, auth: tuple[str, str] | None = None) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=5, auth=auth) as client:
            response = await client.get(url)
            response.raise_for_status()
        return True, str(response.status_code)
    except httpx.HTTPError as exc:
        return False, type(exc).__name__


async def wait_for_http(url: str, auth: tuple[str, str] | None = None) -> tuple[bool, str]:
    latest_detail = "NoAttempt"
    for attempt in range(STARTUP_ATTEMPTS):
        healthy, latest_detail = await http_status(url, auth)
        if healthy:
            return healthy, latest_detail
        if attempt < STARTUP_ATTEMPTS - 1:
            await asyncio.sleep(STARTUP_DELAY_SECONDS)
    return False, latest_detail


def installed_binary(name: str, relative_path: str) -> DiagnosticCheck:
    if not getattr(sys, "frozen", False):
        return DiagnosticCheck(name, "OK", "Validação em código-fonte")
    binary = Path(sys.executable).resolve().parent / relative_path
    return DiagnosticCheck(name, "OK" if binary.is_file() else "FALHA", str(binary))


def service_check(name: str, service: str) -> DiagnosticCheck | None:
    if platform.system() != "Windows":
        return None
    service_status = OrthancServiceController(service).status()
    return DiagnosticCheck(name, "OK" if service_status == "ONLINE" else "FALHA", service_status)


async def diagnose(settings: Settings | None = None, component: str = "final") -> list[DiagnosticCheck]:
    """Executar checks da etapa solicitada; o modo final combina Orthanc e Router."""
    if component not in {"orthanc", "router", "final"}:
        raise ValueError(f"Componente de diagnóstico inválido: {component}")

    current = settings or Settings()
    paths = current.paths
    router_port = int(current.get("api", "port", default=8765))
    router_dicom_port = int(current.get("dicom", "port", default=4242))
    orthanc_dicom_port = int(current.get("orthanc", "dicom_port", default=4243))
    orthanc_http_port = int(current.get("orthanc", "http_port", default=8042))
    orthanc_url = str(current.get("orthanc", "url", default=f"http://127.0.0.1:{orthanc_http_port}")).rstrip("/")
    router_url = f"http://{current.get('api', 'host', default='127.0.0.1')}:{router_port}"
    checks: list[DiagnosticCheck] = []

    if component in {"orthanc", "final"}:
        checks.extend(
            [
                installed_binary("Orthanc instalado", "orthanc/Orthanc.exe"),
                DiagnosticCheck(
                    "Storage Orthanc",
                    "OK" if paths.orthanc_storage.is_dir() and paths.orthanc_database.is_dir() else "FALHA",
                    str(paths.orthanc_root),
                ),
            ]
        )
        service = service_check("Serviço VOXEL Orthanc", "VOXELOrthanc")
        if service is not None:
            checks.append(service)
        orthanc_dicom_open, orthanc_rest_open = await asyncio.gather(
            wait_for_tcp("127.0.0.1", orthanc_dicom_port),
            wait_for_tcp("127.0.0.1", orthanc_http_port),
        )
        checks.extend(
            [
                DiagnosticCheck(
                    f"Porta Orthanc DICOM {orthanc_dicom_port}",
                    "OK" if orthanc_dicom_open else "FALHA",
                    f"TCP {orthanc_dicom_port}",
                ),
                DiagnosticCheck(
                    f"Porta Orthanc REST {orthanc_http_port}",
                    "OK" if orthanc_rest_open else "FALHA",
                    f"TCP {orthanc_http_port} localhost",
                ),
            ]
        )
        orthanc_auth = None
        try:
            password = WindowsSecretStore(paths).get("orthanc.internal.password")
            if password:
                orthanc_auth = ("voxel-router-internal", password)
        except SecretStoreError:
            pass
        orthanc_ok, orthanc_detail = await wait_for_http(f"{orthanc_url}/system", orthanc_auth)
        checks.append(DiagnosticCheck("Health check Orthanc", "OK" if orthanc_ok else "FALHA", orthanc_detail))

    if component in {"router", "final"}:
        checks.append(installed_binary("Router instalado", "VOXELRouter.exe"))
        service = service_check("Serviço VOXEL Router", "VOXELRouter")
        if service is not None:
            checks.append(service)
        router_dicom_open, router_http_open = await asyncio.gather(
            wait_for_tcp("127.0.0.1", router_dicom_port),
            wait_for_tcp("127.0.0.1", router_port),
        )
        checks.extend(
            [
                DiagnosticCheck(
                    f"Porta Router DICOM {router_dicom_port}",
                    "OK" if router_dicom_open else "FALHA",
                    f"TCP {router_dicom_port}",
                ),
                DiagnosticCheck(
                    f"Porta Router HTTP {router_port}",
                    "OK" if router_http_open else "FALHA",
                    f"TCP {router_port} localhost",
                ),
            ]
        )
        router_ok, router_detail = await wait_for_http(f"{router_url}/health")
        checks.append(DiagnosticCheck("Health check Router", "OK" if router_ok else "FALHA", router_detail))
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnóstico de instalação VOXEL Router")
    parser.add_argument("--component", choices=("orthanc", "router", "final"), default="final")
    args = parser.parse_args()
    checks = asyncio.run(diagnose(component=args.component))
    payload = {"component": args.component, "checks": [asdict(check) for check in checks]}
    settings = Settings()
    report = settings.paths.logs / "install-diagnostic.json"
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    if any(check.required and check.status != "OK" for check in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
