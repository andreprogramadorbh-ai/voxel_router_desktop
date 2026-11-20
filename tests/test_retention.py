"""Fase 5: retenção local, série tardia e proteção de disco."""

from __future__ import annotations

from io import BytesIO

from pydicom.uid import generate_uid

from app.core.engine import RouterEngine
from app.dicom import scp as scp_module
from app.dicom.ingest import DicomIngestor
from app.queue.manager import QueueManager
from tests.test_queue import create_destination, sample_dataset


class FakeOrthanc:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.deleted: list[str] = []

    def delete_instance_sync(self, orthanc_instance_id: str) -> bool:
        self.deleted.append(orthanc_instance_id)
        return self.ok


def receive(ingestor: DicomIngestor, study_uid: str | None = None, orthanc_id: str | None = "orthanc-1") -> str:
    dataset = sample_dataset()
    if study_uid:
        dataset.StudyInstanceUID = study_uid
        dataset.SeriesInstanceUID = generate_uid()
    buffer = BytesIO()
    dataset.save_as(buffer, enforce_file_format=True)
    ingestor.receive_dataset(dataset, buffer.getvalue(), orthanc_id)
    return str(dataset.StudyInstanceUID)


def engine_with(settings, database, orthanc: FakeOrthanc, auto_delete: bool = True) -> RouterEngine:
    settings.update("orthanc", {"enabled": False})
    settings.update("storage", {"auto_delete": auto_delete, "retention_hours": 24})
    engine = RouterEngine(settings, database)
    engine.orthanc = orthanc
    return engine


def set_status(database, status: str, validated_days_ago: int = 10) -> None:
    with database.transaction() as connection:
        connection.execute("UPDATE studies SET status = ?, validated_at = datetime('now', ?)", (status, f"-{validated_days_ago} days"))


def object_files(paths) -> list:
    return [path for path in (paths.storage / "objects").rglob("*.dcm")]


def test_only_old_validated_studies_are_removed_from_router_and_orthanc(settings, database, paths):
    orthanc = FakeOrthanc()
    engine = engine_with(settings, database, orthanc)
    receive(engine.ingestor)
    set_status(database, "VALIDATED")

    assert engine.apply_retention_policy() == 1
    assert orthanc.deleted == ["orthanc-1"]
    assert database.query_one("SELECT COUNT(*) AS n FROM instances")["n"] == 0
    assert object_files(paths) == []


def test_recent_validated_study_is_kept(settings, database, paths):
    engine = engine_with(settings, database, FakeOrthanc())
    receive(engine.ingestor)
    set_status(database, "VALIDATED", validated_days_ago=0)

    assert engine.apply_retention_policy() == 0
    assert len(object_files(paths)) == 1


def test_pending_or_failed_studies_are_never_removed(settings, database, paths):
    orthanc = FakeOrthanc()
    engine = engine_with(settings, database, orthanc)
    receive(engine.ingestor)
    for status in ("RETRY", "PAUSED", "ERROR", "QUEUED", "SENDING", "RECEIVED"):
        set_status(database, status)
        assert engine.apply_retention_policy() == 0
    assert orthanc.deleted == []
    assert len(object_files(paths)) == 1


def test_orthanc_delete_failure_keeps_router_copy(settings, database, paths):
    engine = engine_with(settings, database, FakeOrthanc(ok=False))
    receive(engine.ingestor)
    set_status(database, "VALIDATED")

    assert engine.apply_retention_policy() == 0
    assert database.query_one("SELECT COUNT(*) AS n FROM instances")["n"] == 1
    assert len(object_files(paths)) == 1


def test_retention_is_off_by_default(settings, database, paths):
    orthanc = FakeOrthanc()
    engine = engine_with(settings, database, orthanc, auto_delete=False)
    receive(engine.ingestor)
    set_status(database, "VALIDATED")

    assert engine.apply_retention_policy() == 0
    assert orthanc.deleted == []


def test_late_series_reopens_delivered_study_and_requeues_it(database, paths):
    ingestor = DicomIngestor(database, paths)
    queue = QueueManager(database)
    destination_id = create_destination(database)
    with database.transaction() as connection:
        connection.execute("UPDATE destinations SET enabled = 1 WHERE id = ?", (destination_id,))
    study_uid = receive(ingestor)
    queue.mark_complete_studies_ready(0)
    queue.enqueue_ready_studies()
    item = queue.claim_next()
    queue.complete(item, bytes_sent=10, instances_sent=1)
    assert database.query_one("SELECT status FROM studies")["status"] == "VALIDATED"

    receive(ingestor, study_uid=study_uid)
    assert database.query_one("SELECT status FROM studies")["status"] == "RECEIVED"
    with database.transaction() as connection:
        connection.execute("UPDATE studies SET last_instance_at = datetime('now', '-60 seconds')")
    queue.mark_complete_studies_ready(30)
    assert queue.enqueue_ready_studies() == 1
    assert database.query_one("SELECT status, attempt_count FROM queue")["status"] == "QUEUED"


def test_instance_arriving_during_send_is_not_lost(database, paths):
    ingestor = DicomIngestor(database, paths)
    queue = QueueManager(database)
    destination_id = create_destination(database)
    with database.transaction() as connection:
        connection.execute("UPDATE destinations SET enabled = 1 WHERE id = ?", (destination_id,))
    study_uid = receive(ingestor)
    queue.mark_complete_studies_ready(0)
    queue.enqueue_ready_studies()
    item = queue.claim_next()
    receive(ingestor, study_uid=study_uid)  # chega enquanto o envio de 1 instância está em curso
    queue.complete(item, bytes_sent=10, instances_sent=1)

    assert database.query_one("SELECT status FROM studies")["status"] == "RECEIVED"


def test_disk_emergency_refuses_c_store_with_out_of_resources(settings, database):
    settings.update("orthanc", {"enabled": False})
    engine = RouterEngine(settings, database)
    engine.scp.accept_storage = lambda: False

    class Requestor:
        ae_title = "MODALIDADE"
        address = "127.0.0.1"

    class Assoc:
        requestor = Requestor()

    class Event:
        assoc = Assoc()

    engine.scp.access.is_allowed = lambda *_: True
    assert engine.scp._handle_store(Event()) == scp_module.STATUS_OUT_OF_RESOURCES


def test_storage_health_reports_whether_c_store_is_accepted(settings, database, monkeypatch):
    settings.update("orthanc", {"enabled": False})
    engine = RouterEngine(settings, database)
    settings.update("storage", {"emergency_percent": 0})
    assert engine.health.storage()["accepting_c_store"] is False
    assert engine.storage_accepts_new_objects() is False
    settings.update("storage", {"emergency_percent": 101})
    assert engine.health.storage()["accepting_c_store"] is True
