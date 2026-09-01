"""Hospedagem da Engine como serviço Windows denominado VOXEL Router Engine."""

from __future__ import annotations

import asyncio
import platform
import threading

from app.config.settings import Settings
from app.core.engine import RouterEngine
from app.core.logging import configure_logging, get_logger

LOGGER = get_logger(__name__)


async def _run_engine(stop_event: threading.Event) -> None:
    engine = RouterEngine()
    task = asyncio.create_task(engine.run())
    while not stop_event.is_set():
        await asyncio.sleep(0.5)
    await engine.stop()
    await task


def run() -> None:
    settings = Settings()
    configure_logging(settings.paths)
    if platform.system() == "Windows":
        try:
            import win32serviceutil  # type: ignore[import-not-found]
            win32serviceutil.HandleCommandLine(VoxelRouterService)
            return
        except ImportError:
            LOGGER.warning("pywin32_unavailable_running_foreground")
    stop_event = threading.Event()
    try:
        asyncio.run(_run_engine(stop_event))
    except KeyboardInterrupt:
        stop_event.set()


if platform.system() == "Windows":
    import win32event  # type: ignore[import-not-found]
    import win32service  # type: ignore[import-not-found]
    import win32serviceutil  # type: ignore[import-not-found]

    class VoxelRouterService(win32serviceutil.ServiceFramework):  # type: ignore[misc]
        _svc_name_ = "VOXELRouterEngine"
        _svc_display_name_ = "VOXEL Router Engine"
        _svc_description_ = "Serviço de recepção, fila e transmissão DICOM do VOXEL Router."

        def __init__(self, args: list[str]) -> None:
            super().__init__(args)
            self.stop_event = threading.Event()
            self.stop_handle = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self.stop_event.set()
            win32event.SetEvent(self.stop_handle)

        def SvcDoRun(self) -> None:
            settings = Settings()
            configure_logging(settings.paths)
            asyncio.run(_run_engine(self.stop_event))


if __name__ == "__main__":
    run()
