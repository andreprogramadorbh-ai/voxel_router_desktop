from __future__ import annotations

from io import BytesIO

from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

from app.dicom.ingest import DicomIngestor
from app.queue.manager import QueueManager


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


def test_queue_survives_restart_and_returns_sending_to_retry(database, paths):
    ingestor = DicomIngestor(database, paths)
    study_uid = ingest(ingestor)
    create_destination(database)
    queue = QueueManager(database, [1, 2, 3, 4], max_attempts=4)
    with database.transaction() as connection:
        connection.execute("UPDATE studies SET last_instance_at = datetime('now', '-60 seconds') WHERE study_instance_uid = ?", (study_uid,))
    assert queue.mark_complete_studies_ready(30) == 1
    assert queue.enqueue_ready_studies() == 1
    item = queue.claim_next()
    assert item is not None
    assert item.status == "SENDING"

    assert queue.recover_after_restart() == 1
    row = database.query_one("SELECT status FROM queue WHERE id = ?", (item.id,))
    assert row["status"] == "RETRY"


def test_retry_is_limited_and_keeps_error_history(database, paths):
    ingestor = DicomIngestor(database, paths)
    study_uid = ingest(ingestor)
    create_destination(database)
    queue = QueueManager(database, [1], max_attempts=1)
    with database.transaction() as connection:
        connection.execute("UPDATE studies SET last_instance_at = datetime('now', '-60 seconds') WHERE study_instance_uid = ?", (study_uid,))
    queue.mark_complete_studies_ready(30)
    queue.enqueue_ready_studies()
    item = queue.claim_next()
    assert item is not None

    status = queue.fail(item, "CLOUD_UNAVAILABLE", "Cloud indisponível")

    assert status == "ERROR"
    attempt = database.query_one("SELECT success, error_code FROM queue_attempts WHERE queue_id = ?", (item.id,))
    assert attempt["success"] == 0
    assert attempt["error_code"] == "CLOUD_UNAVAILABLE"
