from __future__ import annotations

import socket
import time

from fastapi.testclient import TestClient

from app.api.server import create_app
from app.core.engine import RouterEngine
from app.dicom.ingest import DicomIngestor
from tests.test_queue import ingest


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def test_started_app_runs_reconcile_and_transfer_loops(settings, database, paths):
    """Regressão: serviço/app.main subiam só a API; estudos recebidos nunca entravam na fila."""
    settings.update("orthanc", {"enabled": False})
    settings.update("dicom", {"port": available_port(), "study_quiet_window_seconds": 0})
    ingest(DicomIngestor(database, paths))
    with database.transaction() as connection:
        connection.execute("UPDATE studies SET last_instance_at = datetime('now', '-60 seconds')")
    engine = RouterEngine(settings, database)

    with TestClient(create_app(engine, start_engine=True)):
        assert engine._tasks and not any(task.done() for task in engine._tasks)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if database.query_one("SELECT status FROM studies")["status"] == "READY_TO_SEND":
                break
            time.sleep(0.2)
        # Sem destino habilitado o estudo fica pronto e aguardando, mas o loop precisa tê-lo processado.
        assert database.query_one("SELECT status FROM studies")["status"] == "READY_TO_SEND"

    assert engine._tasks == []
