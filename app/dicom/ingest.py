"""Ingestão DICOM confiável com checksum, deduplicação e estado persistente."""

from __future__ import annotations

import hashlib
import io
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydicom.dataset import Dataset
from pydicom.uid import UID

from app.config.settings import AppPaths
from app.core.database import Database
from app.core.logging import get_logger

LOGGER = get_logger(__name__)
UID_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+)+$")


class DicomValidationError(ValueError):
    pass


class DicomIngestor:
    def __init__(self, database: Database, paths: AppPaths | None = None) -> None:
        self.database = database
        self.paths = paths or AppPaths.from_environment()
        self.objects_path = self.paths.storage / "objects"
        self.objects_path.mkdir(parents=True, exist_ok=True)

    def receive_dataset(self, dataset: Dataset, raw_bytes: bytes | None = None, orthanc_instance_id: str | None = None) -> dict[str, Any]:
        metadata = self._metadata(dataset)
        if raw_bytes is None:
            buffer = io.BytesIO()
            dataset.save_as(buffer, enforce_file_format=True)
            raw_bytes = buffer.getvalue()
        return self.receive_bytes(metadata, raw_bytes, orthanc_instance_id)

    def receive_bytes(self, metadata: dict[str, str | None], raw_bytes: bytes, orthanc_instance_id: str | None = None) -> dict[str, Any]:
        self._validate_metadata(metadata)
        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        size = len(raw_bytes)
        sop_uid = str(metadata["sop_instance_uid"])
        study_uid = str(metadata["study_instance_uid"])
        series_uid = str(metadata["series_instance_uid"])

        with self.database.transaction() as connection:
            duplicate = connection.execute("SELECT id, study_id, sha256 FROM instances WHERE sop_instance_uid = ?", (sop_uid,)).fetchone()
            if duplicate:
                LOGGER.info("dicom_duplicate", sop_instance_uid=sop_uid, study_instance_uid=study_uid, same_checksum=duplicate["sha256"] == sha256)
                connection.execute(
                    "INSERT INTO system_events(category, severity, code, message, details) VALUES (?, ?, ?, ?, ?)",
                    ("DICOM", "WARNING", "DICOM_DUPLICATE", "Instância DICOM duplicada recebida", f"sop={sop_uid};same_checksum={duplicate['sha256'] == sha256}"),
                )
                return {"accepted": True, "duplicate": True, "study_instance_uid": study_uid, "sop_instance_uid": sop_uid, "sha256": duplicate["sha256"]}

            study = connection.execute("SELECT id FROM studies WHERE study_instance_uid = ?", (study_uid,)).fetchone()
            if study is None:
                cursor = connection.execute(
                    """INSERT INTO studies(study_instance_uid, patient_id, patient_name, accession_number, modalities_in_study, study_description, study_date, study_time, institution_name, received_at, last_instance_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RECEIVED')""",
                    (study_uid, metadata.get("patient_id"), metadata.get("patient_name"), metadata.get("accession_number"), metadata.get("modality"), metadata.get("study_description"), metadata.get("study_date"), metadata.get("study_time"), metadata.get("institution_name"), now, now),
                )
                study_id = int(cursor.lastrowid)
            else:
                study_id = int(study["id"])
                connection.execute("UPDATE studies SET last_instance_at = ?, status = CASE WHEN status IN ('SENT','VALIDATED') THEN status ELSE 'RECEIVED' END WHERE id = ?", (now, study_id))

            series = connection.execute("SELECT id FROM series WHERE series_instance_uid = ?", (series_uid,)).fetchone()
            if series is None:
                cursor = connection.execute("INSERT INTO series(study_id, series_instance_uid, modality) VALUES (?, ?, ?)", (study_id, series_uid, metadata.get("modality")))
                series_id = int(cursor.lastrowid)
            else:
                series_id = int(series["id"])

            connection.execute(
                """INSERT INTO instances(study_id, series_id, sop_instance_uid, sop_class_uid, sha256, size_bytes, orthanc_instance_id, received_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (study_id, series_id, sop_uid, metadata.get("sop_class_uid"), sha256, size, orthanc_instance_id, now),
            )
            connection.execute("UPDATE series SET instance_count = instance_count + 1, total_bytes = total_bytes + ? WHERE id = ?", (size, series_id))
            modalities = "\\".join(row["modality"] for row in connection.execute("SELECT DISTINCT modality FROM series WHERE study_id = ? AND modality IS NOT NULL ORDER BY id", (study_id,)))
            series_count = connection.execute("SELECT COUNT(*) AS count FROM series WHERE study_id = ?", (study_id,)).fetchone()["count"]
            connection.execute(
                "UPDATE studies SET instance_count = instance_count + 1, series_count = ?, total_bytes = total_bytes + ?, modalities_in_study = ?, last_instance_at = ? WHERE id = ?",
                (series_count, size, modalities, now, study_id),
            )

        self._store_object(sha256, raw_bytes)
        LOGGER.info("dicom_received", study_instance_uid=study_uid, sop_instance_uid=sop_uid, size_bytes=size, sha256=sha256)
        return {"accepted": True, "duplicate": False, "study_instance_uid": study_uid, "sop_instance_uid": sop_uid, "sha256": sha256, "size_bytes": size}

    def attach_orthanc_instance(self, sop_instance_uid: str, orthanc_instance_id: str) -> None:
        """Registra o identificador retornado pelo Orthanc depois de um C-STORE confirmado."""
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE instances SET orthanc_instance_id = ? WHERE sop_instance_uid = ?",
                (orthanc_instance_id, sop_instance_uid),
            )

    def instance_path(self, sha256: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise ValueError("Checksum inválido")
        return self.objects_path / sha256[:2] / f"{sha256}.dcm"

    def _store_object(self, sha256: str, raw_bytes: bytes) -> None:
        target = self.instance_path(sha256)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return
        temporary = target.with_suffix(".part")
        temporary.write_bytes(raw_bytes)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != sha256:
            temporary.unlink(missing_ok=True)
            raise DicomValidationError("Checksum de armazenamento não confere")
        temporary.replace(target)

    @staticmethod
    def _metadata(dataset: Dataset) -> dict[str, str | None]:
        file_meta = getattr(dataset, "file_meta", Dataset())
        return {
            "study_instance_uid": str(dataset.get("StudyInstanceUID", "")),
            "series_instance_uid": str(dataset.get("SeriesInstanceUID", "")),
            "sop_instance_uid": str(dataset.get("SOPInstanceUID", "")),
            "sop_class_uid": str(dataset.get("SOPClassUID", file_meta.get("MediaStorageSOPClassUID", ""))),
            "patient_id": str(dataset.get("PatientID", "")) or None,
            "patient_name": str(dataset.get("PatientName", "")) or None,
            "accession_number": str(dataset.get("AccessionNumber", "")) or None,
            "modality": str(dataset.get("Modality", "")) or None,
            "study_description": str(dataset.get("StudyDescription", "")) or None,
            "study_date": str(dataset.get("StudyDate", "")) or None,
            "study_time": str(dataset.get("StudyTime", "")) or None,
            "institution_name": str(dataset.get("InstitutionName", "")) or None,
        }

    @staticmethod
    def _validate_metadata(metadata: dict[str, str | None]) -> None:
        for key in ("study_instance_uid", "series_instance_uid", "sop_instance_uid"):
            value = metadata.get(key) or ""
            if not UID_PATTERN.fullmatch(value) or not UID(value).is_valid:
                raise DicomValidationError(f"UID DICOM inválido: {key}")
        if not metadata.get("sop_class_uid"):
            raise DicomValidationError("SOP Class UID é obrigatório")
