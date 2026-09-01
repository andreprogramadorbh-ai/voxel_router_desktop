from __future__ import annotations

from io import BytesIO

from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

from app.dicom.ingest import DicomIngestor


def make_dataset(modality: str = "CT") -> Dataset:
    dataset = Dataset()
    dataset.file_meta = FileMetaDataset()
    dataset.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    dataset.file_meta.MediaStorageSOPClassUID = CTImageStorage
    dataset.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    dataset.SOPClassUID = CTImageStorage
    dataset.SOPInstanceUID = dataset.file_meta.MediaStorageSOPInstanceUID
    dataset.StudyInstanceUID = generate_uid()
    dataset.SeriesInstanceUID = generate_uid()
    dataset.PatientID = "PID-001"
    dataset.PatientName = "PACIENTE^TESTE"
    dataset.Modality = modality
    dataset.Rows = 1
    dataset.Columns = 1
    dataset.BitsAllocated = 8
    dataset.PixelRepresentation = 0
    dataset.PixelData = b"\x00"
    return dataset


def bytes_for(dataset: Dataset) -> bytes:
    buffer = BytesIO()
    dataset.save_as(buffer, enforce_file_format=True)
    return buffer.getvalue()


def test_ingest_persists_checksum_and_rejects_duplicate(database, paths):
    ingestor = DicomIngestor(database, paths)
    dataset = make_dataset()
    raw = bytes_for(dataset)

    first = ingestor.receive_dataset(dataset, raw)
    duplicate = ingestor.receive_dataset(dataset, raw)

    assert first["accepted"] is True
    assert first["duplicate"] is False
    assert duplicate["duplicate"] is True
    stored = database.query_one("SELECT COUNT(*) AS count FROM instances")
    assert stored["count"] == 1
    assert ingestor.instance_path(first["sha256"]).is_file()
    assert ingestor.instance_path(first["sha256"]).read_bytes() == raw


def test_modalities_in_study_comes_from_distinct_series(database, paths):
    ingestor = DicomIngestor(database, paths)
    first = make_dataset("CT")
    second = make_dataset("MR")
    second.StudyInstanceUID = first.StudyInstanceUID

    ingestor.receive_dataset(first, bytes_for(first))
    ingestor.receive_dataset(second, bytes_for(second))

    row = database.query_one("SELECT modalities_in_study, series_count, instance_count FROM studies WHERE study_instance_uid = ?", (first.StudyInstanceUID,))
    assert row["modalities_in_study"] == "CT\\MR"
    assert row["series_count"] == 2
    assert row["instance_count"] == 2
