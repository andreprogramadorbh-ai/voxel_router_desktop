from __future__ import annotations

import socket
from datetime import UTC, datetime
from io import BytesIO

import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

from app.cloud.connectors import (
    CloudConnector,
    CloudConnectorError,
    DicomCloudConnector,
    TransferResult,
    cstore_failure,
    echo_target,
)
from app.dicom.ingest import DicomIngestor
from app.queue.manager import MAX_RETRY_DELAY_SECONDS, QueueManager
from app.transfer.manager import TransferManager


def sample_dataset() -> Dataset:
    dataset = Dataset()
    dataset.file_meta = FileMetaDataset()
    dataset.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    dataset.file_meta.MediaStorageSOPClassUID = CTImageStorage
    dataset.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    dataset.SOPClassUID = CTImageStorage
    dataset.SOPInstanceUID = dataset.file_meta.MediaStorageSOPInstanceUID
    dataset.StudyInstanceUID = generate_uid()
    dataset.SeriesInstanceUID = generate_uid()
    dataset.Modality = "CT"
    dataset.PatientID = "PID-QUEUE"
    dataset.Rows = 1
    dataset.Columns = 1
    dataset.BitsAllocated = 8
    dataset.PixelRepresentation = 0
    dataset.PixelData = b"\x00"
    return dataset


def ingest(ingestor: DicomIngestor) -> str:
    dataset = sample_dataset()
    output = BytesIO()
    dataset.save_as(output, enforce_file_format=True)
    ingestor.receive_dataset(dataset, output.getvalue())
    return str(dataset.StudyInstanceUID)


def create_destination(database) -> int:
    with database.transaction() as connection:
        cursor = connection.execute("INSERT INTO destinations(name, kind, ae_title, host, port) VALUES ('Teste DICOM', 'DICOM', 'DESTINO', '127.0.0.1', 11113)")
        return int(cursor.lastrowid)


def queued_item(database, paths, queue: QueueManager) -> int:
    """Estudo sintético pronto e enfileirado para um destino de teste."""
    study_uid = ingest(DicomIngestor(database, paths))
    create_destination(database)
    with database.transaction() as connection:
        connection.execute("UPDATE studies SET last_instance_at = datetime('now', '-60 seconds') WHERE study_instance_uid = ?", (study_uid,))
    assert queue.mark_complete_studies_ready(30) == 1
    assert queue.enqueue_ready_studies() == 1
    return int(database.query_one("SELECT id FROM queue")["id"])


def make_due(database, queue_id: int) -> None:
    """Simula a passagem do tempo até a próxima tentativa."""
    with database.transaction() as connection:
        connection.execute("UPDATE queue SET next_attempt_at = datetime('now', '-1 second') WHERE id = ?", (queue_id,))


def fail_network(database, queue: QueueManager, queue_id: int, times: int) -> None:
    for _ in range(times):
        make_due(database, queue_id)
        item = queue.claim_next()
        assert item is not None
        assert queue.fail(item, "DESTINATION_UNAVAILABLE", "Destino indisponível") == "RETRY"


def queue_row(database, queue_id: int):
    return database.query_one(
        "SELECT q.status, q.attempt_count, q.last_error_code, s.status AS study_status "
        "FROM queue q JOIN studies s ON s.id = q.study_id WHERE q.id = ?",
        (queue_id,),
    )


class FakeConnector(CloudConnector):
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def connect(self) -> None:
        return None

    async def authenticate(self) -> None:
        return None

    async def health_check(self) -> str:
        return "CONNECTED"

    async def send_study(self, study_id: int) -> TransferResult:
        if self.error is not None:
            raise self.error
        return TransferResult(10, 1, "Teste DICOM", "ok")

    async def get_status(self) -> str:
        return "CONNECTED"

    async def disconnect(self) -> None:
        return None


def test_queue_survives_restart_and_returns_sending_to_retry(database, paths):
    queue = QueueManager(database, [1, 2, 3, 4])
    queue_id = queued_item(database, paths, queue)
    item = queue.claim_next()
    assert item is not None
    assert item.status == "SENDING"

    assert queue.recover_after_restart() == 1
    assert queue_row(database, queue_id)["status"] == "RETRY"


def test_ten_network_failures_stay_in_retry(database, paths):
    queue = QueueManager(database)
    queue_id = queued_item(database, paths, queue)

    fail_network(database, queue, queue_id, 10)

    row = queue_row(database, queue_id)
    assert (row["status"], row["study_status"], row["attempt_count"]) == ("RETRY", "RETRY", 10)
    failed = database.query_one("SELECT COUNT(*) AS n FROM queue_attempts WHERE queue_id = ? AND success = 0", (queue_id,))
    assert failed["n"] == 10
    # Falha de rede não entra na tabela de erros que exigem ação do operador.
    assert database.query_one("SELECT COUNT(*) AS n FROM errors")["n"] == 0


def test_recovery_after_restart_keeps_retry_after_many_failures(database, paths):
    queue = QueueManager(database)
    queue_id = queued_item(database, paths, queue)
    fail_network(database, queue, queue_id, 10)
    make_due(database, queue_id)
    assert queue.claim_next() is not None  # processo cai durante o envio (SENDING)

    restarted = QueueManager(database)
    assert restarted.recover_after_restart() == 1
    assert queue_row(database, queue_id)["status"] == "RETRY"

    item = restarted.claim_next()
    assert item is not None
    assert item.attempt_count == 12
    assert restarted.fail(item, "DESTINATION_UNAVAILABLE", "Destino indisponível") == "RETRY"


def test_backoff_sequence_and_delay_never_exceeds_15_minutes(database, paths):
    queue = QueueManager(database)
    assert [queue.retry_delay(n) for n in range(1, 8)] == [30, 120, 300, 900, 900, 900, 900]

    misconfigured = QueueManager(database, [30, 120, 300, 900, 3600, 86400])
    assert max(misconfigured.retry_delay(n) for n in range(1, 200)) == MAX_RETRY_DELAY_SECONDS

    queue_id = queued_item(database, paths, misconfigured)
    fail_network(database, misconfigured, queue_id, 6)
    next_attempt = database.query_one("SELECT next_attempt_at FROM queue WHERE id = ?", (queue_id,))["next_attempt_at"]
    wait = datetime.strptime(next_attempt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC) - datetime.now(UTC)
    assert wait.total_seconds() <= MAX_RETRY_DELAY_SECONDS


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (cstore_failure(0xA900), "ERROR", "CSTORE_A900"),
        (cstore_failure(0xC000), "ERROR", "CSTORE_C000"),
        (CloudConnectorError("LOCAL_OBJECT_MISSING", permanent=True), "ERROR", "LOCAL_OBJECT_MISSING"),
        (cstore_failure(0xA700), "RETRY", "CSTORE_A700"),
        (CloudConnectorError("DESTINATION_UNAVAILABLE"), "RETRY", "DESTINATION_UNAVAILABLE"),
        (TimeoutError("timed out"), "RETRY", "TRANSFER_UNEXPECTED"),
        (ConnectionResetError("reset"), "RETRY", "TRANSFER_UNEXPECTED"),
    ],
)
async def test_transfer_classifies_rejection_vs_connectivity(database, paths, settings, error, expected_status, expected_code):
    queue = QueueManager(database)
    queue_id = queued_item(database, paths, queue)
    transfer = TransferManager(database, settings, queue, connector_factory=lambda _: FakeConnector(error))

    assert await transfer.process_once()

    row = queue_row(database, queue_id)
    assert (row["status"], row["study_status"], row["last_error_code"]) == (expected_status, expected_status, expected_code)
    errors = database.query_one("SELECT COUNT(*) AS n FROM errors WHERE code = ?", (expected_code,))["n"]
    assert errors == (1 if expected_status == "ERROR" else 0)


async def test_unreachable_destination_is_connectivity_failure(database, paths):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    with database.transaction() as connection:
        destination_id = connection.execute(
            "INSERT INTO destinations(name, kind, ae_title, host, port) VALUES ('Fechado', 'DICOM', 'DESTINO', '127.0.0.1', ?)",
            (closed_port,),
        ).lastrowid
        # Destino já verificado por C-ECHO (Fase 3): a falha aqui deve ser só de rede.
        row = connection.execute("SELECT * FROM destinations WHERE id = ?", (destination_id,)).fetchone()
        connection.execute("UPDATE destinations SET echo_status = 'SUCCESS', echo_target = ? WHERE id = ?", (echo_target("VOXEL_ROUTER", row), destination_id))
    ingest(DicomIngestor(database, paths))
    study_id = database.query_one("SELECT id FROM studies")["id"]
    connector = DicomCloudConnector(database, paths.storage, destination_id, "VOXEL_ROUTER", association_timeout_seconds=3)

    await connector.connect()
    with pytest.raises(CloudConnectorError) as raised:
        await connector.send_study(study_id)

    assert str(raised.value) == "DESTINATION_UNAVAILABLE"
    assert raised.value.permanent is False
    assert await connector.health_check() == "DISCONNECTED"


def test_backlog_alert_and_last_success(database, paths):
    queue = QueueManager(database, pending_alert_minutes=60)
    empty = queue.backlog_status()
    assert (empty["pending_count"], empty["alert"], empty["last_success_at"]) == (0, False, None)

    queue_id = queued_item(database, paths, queue)
    with database.transaction() as connection:
        connection.execute("UPDATE queue SET created_at = datetime('now', '-10 minutes') WHERE id = ?", (queue_id,))
    recent = queue.backlog_status()
    assert recent["pending_count"] == 1
    assert recent["alert"] is False
    assert 9 <= recent["oldest_pending_minutes"] <= 11

    with database.transaction() as connection:
        connection.execute("UPDATE queue SET created_at = datetime('now', '-61 minutes') WHERE id = ?", (queue_id,))
    assert queue.backlog_status()["alert"] is True

    item = queue.claim_next()
    assert item is not None
    queue.complete(item, 10, 1)
    done = queue.backlog_status()
    assert (done["pending_count"], done["alert"]) == (0, False)
    assert done["last_success_at"] is not None


def test_ready_study_without_destination_counts_as_pending(database, paths):
    queue = QueueManager(database, pending_alert_minutes=60)
    ingest(DicomIngestor(database, paths))
    with database.transaction() as connection:
        connection.execute("UPDATE studies SET last_instance_at = datetime('now', '-2 hours')")
    assert queue.mark_complete_studies_ready(30) == 1
    with database.transaction() as connection:
        connection.execute("UPDATE studies SET ready_at = datetime('now', '-90 minutes')")
    assert queue.enqueue_ready_studies() == 0  # nenhum destino habilitado

    status = queue.backlog_status()
    assert status["pending_count"] == 1
    assert status["alert"] is True
    assert [alert["code"] for alert in status["alerts"]] == ["PENDING_DELAY"]


@pytest.mark.parametrize(
    ("error", "code", "message_part"),
    [
        (CloudConnectorError("DESTINATION_REJECTED"), "DESTINATION_REJECTED", "Calling AE"),
        (RuntimeError("bug"), "TRANSFER_UNEXPECTED", "contate o suporte"),
    ],
)
async def test_rejection_and_internal_failure_alert_on_first_occurrence(database, paths, settings, error, code, message_part):
    queue = QueueManager(database, pending_alert_minutes=60)
    queue_id = queued_item(database, paths, queue)
    transfer = TransferManager(database, settings, queue, connector_factory=lambda _: FakeConnector(error))

    assert await transfer.process_once()

    status = queue.backlog_status()
    assert queue_row(database, queue_id)["status"] == "RETRY"
    assert status["alert"] is False  # ainda sem atraso de 60 min
    assert len(status["alerts"]) == 1
    assert status["alerts"][0]["code"] == code
    assert status["alerts"][0]["count"] == 1
    assert message_part in status["alerts"][0]["message"]


async def test_network_failure_does_not_alert_immediately(database, paths, settings):
    queue = QueueManager(database, pending_alert_minutes=60)
    queued_item(database, paths, queue)
    transfer = TransferManager(database, settings, queue, connector_factory=lambda _: FakeConnector(CloudConnectorError("DESTINATION_UNAVAILABLE")))

    assert await transfer.process_once()

    assert queue.backlog_status()["alerts"] == []


def test_paused_items_are_counted_separately_without_delay_alert(database, paths):
    queue = QueueManager(database, pending_alert_minutes=60)
    queue_id = queued_item(database, paths, queue)
    queue.pause(queue_id)
    with database.transaction() as connection:
        connection.execute("UPDATE queue SET created_at = datetime('now', '-5 hours') WHERE id = ?", (queue_id,))

    status = queue.backlog_status()
    assert status["paused_count"] == 1
    assert status["pending_count"] == 0
    assert status["alert"] is False
    assert status["alerts"] == []
