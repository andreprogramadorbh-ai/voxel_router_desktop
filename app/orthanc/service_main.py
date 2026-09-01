"""Hospedagem do Orthanc como serviço Windows separado do processo VOXEL Router."""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

from app.config.settings import Settings
from app.core.logging import configure_logging, get_logger

LOGGER = get_logger(__name__)
SERVICE_NAME = "VOXELOrthanc"
SERVICE_DISPLAY_NAME = "VOXEL Orthanc"


def installed_orthanc_path() -> Path:
    """Resolve o binário Orthanc instalado ao lado do host de serviço empacotado."""
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parents[2]
    return root / "orthanc" / "Orthanc.exe"


def orthanc_command(settings: Settings) -> list[str]:
    binary = installed_orthanc_path()
    config = settings.paths.config / "orthanc.json"
    if not binary.is_file():
        raise FileNotFoundError(f"Binário Orthanc não encontrado: {binary}")
    if not config.is_file():
        raise FileNotFoundError(f"Configuração Orthanc não encontrada: {config}")
    return [str(binary), str(config)]


def start_orthanc(settings: Settings) -> subprocess.Popen[bytes]:
    command = orthanc_command(settings)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    LOGGER.info("orthanc_process_starting", executable=command[0], config=command[1])
    return subprocess.Popen(command, creationflags=creationflags, close_fds=platform.system() != "Windows")


def stop_orthanc(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        LOGGER.warning("orthanc_process_force_kill")
        process.kill()
        process.wait(timeout=10)


def run() -> None:
    """Ponto de entrada para SCM, configuração do instalador e diagnóstico controlado."""
    configure_logging()
    if "--configure" in sys.argv:
        from app.orthanc.configuration import configure_orthanc

        print(configure_orthanc())
        return
    if platform.system() == "Windows":
        try:
            import win32serviceutil  # type: ignore[import-not-found]

            win32serviceutil.HandleCommandLine(VoxelOrthancService)
            return
        except ImportError as exc:
            raise RuntimeError("pywin32 é obrigatório para hospedar o serviço VOXEL Orthanc") from exc

    process = start_orthanc(Settings())
    try:
        process.wait()
    finally:
        stop_orthanc(process)


if platform.system() == "Windows":
    import win32event  # type: ignore[import-not-found]
    import win32service  # type: ignore[import-not-found]
    import win32serviceutil  # type: ignore[import-not-found]

    class VoxelOrthancService(win32serviceutil.ServiceFramework):  # type: ignore[misc]
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY_NAME
        _svc_description_ = "Serviço independente de armazenamento DICOM Orthanc do VOXEL Router."

        def __init__(self, args: list[str]) -> None:
            super().__init__(args)
            self.stop_handle = win32event.CreateEvent(None, 0, 0, None)
            self.process: subprocess.Popen[bytes] | None = None

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self.stop_handle)

        def SvcDoRun(self) -> None:
            configure_logging()
            try:
                self.process = start_orthanc(Settings())
                while win32event.WaitForSingleObject(self.stop_handle, 1000) == win32event.WAIT_TIMEOUT:
                    if self.process.poll() is not None:
                        LOGGER.error("orthanc_process_exited", returncode=self.process.returncode)
                        break
            finally:
                stop_orthanc(self.process)
else:
    VoxelOrthancService = None  # type: ignore[assignment,misc]


if __name__ == "__main__":
    run()
