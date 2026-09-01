"""Hospedagem da API e Engine como serviço Windows denominado VOXEL Router."""

from __future__ import annotations

import asyncio
import platform
import threading

import uvicorn

from app.api.server import create_app
from app.config.settings import Settings
from app.core.logging import configure_logging, get_logger

LOGGER = get_logger(__name__)
SERVICE_NAME = "VOXELRouter"
SERVICE_DISPLAY_NAME = "VOXEL Router"


async def _run_router_service(stop_event: threading.Event) -> None:
    """Executa a API local e sua Engine no único processo de serviço do Router."""
    settings = Settings()
    configure_logging(settings.paths)
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(start_engine=True),
            host=str(settings.get("api", "host", default="127.0.0.1")),
            port=int(settings.get("api", "port", default=8765)),
            log_config=None,
            access_log=False,
        )
    )
    task = asyncio.create_task(server.serve())
    try:
        while not stop_event.is_set() and not task.done():
            await asyncio.sleep(0.5)
    finally:
        server.should_exit = True
        await task


def run() -> None:
    if platform.system() == "Windows":
        try:
            import win32serviceutil  # type: ignore[import-not-found]

            win32serviceutil.HandleCommandLine(VoxelRouterService)
            return
        except ImportError:
            LOGGER.warning("pywin32_unavailable_running_foreground")
    stop_event = threading.Event()
    try:
        asyncio.run(_run_router_service(stop_event))
    except KeyboardInterrupt:
        stop_event.set()


if platform.system() == "Windows":
    import win32event  # type: ignore[import-not-found]
    import win32service  # type: ignore[import-not-found]
    import win32serviceutil  # type: ignore[import-not-found]

    class VoxelRouterService(win32serviceutil.ServiceFramework):  # type: ignore[misc]
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY_NAME
        _svc_description_ = "Serviço local de recepção, fila, transmissão DICOM e administração do VOXEL Router."

        def __init__(self, args: list[str]) -> None:
            super().__init__(args)
            self.stop_event = threading.Event()
            self.stop_handle = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self.stop_event.set()
            win32event.SetEvent(self.stop_handle)

        def SvcDoRun(self) -> None:
            asyncio.run(_run_router_service(self.stop_event))
else:
    VoxelRouterService = None  # type: ignore[assignment,misc]


if __name__ == "__main__":
    run()
