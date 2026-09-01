"""Persistência e transições de tarefas Non-DICOM, sem reutilizar a fila DICOM."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.database import Database
from app.core.logging import get_logger
from app.non_dicom.models import NonDicomSubmission
from app.non_dicom.storage import NonDicomStorage

LOGGER = get_logger(__name__)


class NonDicomManager:
    def __init__(self, database: Database, storage: NonDicomStorage, retry_delays: list[int], max_attempts: int) -> None:
        self.database = database
        self.storage = storage
        self.retry_delays = retry_delays or [30]
        self.max_attempts = max(1, max_attempts)

    def recover_after_restart(self) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """UPDATE non_dicom_submissions SET status='RETRYING', next_attempt_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP, last_error=COALESCE(last_error, 'RECOVERED_AFTER_RESTART')
                   WHERE status IN ('VALIDATING','PROCESSING')"""
            )
            count = cursor.rowcount
        if count:
            LOGGER.warning("non_dicom_recovered_after_restart", submission_count=count)
        return count

    def register_xml(self, xml_path: Path) -> tuple[str | None, bool]:
        raw_xml = xml_path.read_bytes()
        digest = hashlib.sha256(raw_xml).hexdigest()
        with self.database.transaction() as connection:
            duplicate = connection.execute("SELECT id FROM non_dicom_submissions WHERE xml_sha256 = ?", (digest,)).fetchone()
            if duplicate:
                return str(duplicate["id"]), True
        return None, False

    def create(self, submission: NonDicomSubmission, xml_path: Path, file_path: Path) -> str:
        submission_id = str(uuid.uuid4())
        digest = hashlib.sha256(xml_path.read_bytes()).hexdigest()
        stored_xml = self.storage.move_to_processing(xml_path)
        data = json.dumps(submission.metadata(), ensure_ascii=False)
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO non_dicom_submissions(
                    id, source_format, source_xml_path, xml_sha256, patient_id, patient_name, accession_number,
                    file_name, file_path, mime_type, modality, document_name, document_type, is_report,
                    metadata_json, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')""",
                (
                    submission_id, submission.source_format, str(stored_xml), digest, submission.patient_id,
                    submission.patient_name, submission.accession_number, file_path.name, str(file_path),
                    submission.mime_type, submission.modality, submission.document_name, submission.document_type,
                    int(submission.is_report), data,
                ),
            )
            self._event(connection, submission_id, "QUEUED", "PENDING", "Tarefa incluída na fila Non-DICOM")
        LOGGER.info("non_dicom_queued", submission_id=submission_id, status="PENDING")
        return submission_id

    def claim_next(self) -> dict[str, Any] | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                """SELECT * FROM non_dicom_submissions
                   WHERE status IN ('PENDING', 'RETRYING') AND next_attempt_at <= CURRENT_TIMESTAMP
                   ORDER BY created_at, id LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            attempts = int(row["attempt_count"]) + 1
            connection.execute(
                """UPDATE non_dicom_submissions SET status='PROCESSING', attempt_count=?, last_attempt_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (attempts, row["id"]),
            )
            self._event(connection, str(row["id"]), "PROCESSING_STARTED", "PROCESSING", "Processamento iniciado")
            data = dict(row)
            data["attempt_count"] = attempts
            data["status"] = "PROCESSING"
            return data

    def complete(self, item: dict[str, Any], remote_reference: str | None = None) -> None:
        xml_path = Path(str(item["source_xml_path"]))
        completed = self.storage.move_to_completed(xml_path) if xml_path.exists() else xml_path
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE non_dicom_submissions SET status='COMPLETED', source_xml_path=?, completed_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP, last_error=NULL WHERE id=?""",
                (str(completed), item["id"]),
            )
            self._event(connection, str(item["id"]), "COMPLETED", "COMPLETED", remote_reference or "Processamento concluído")
        LOGGER.info("non_dicom_completed", submission_id=item["id"], status="COMPLETED")

    def fail(self, item: dict[str, Any], error: str) -> str:
        message = error[:500]
        attempts = int(item["attempt_count"])
        if attempts >= self.max_attempts:
            status = "FAILED"
            xml_path = Path(str(item["source_xml_path"]))
            failed = self.storage.move_to_failed(xml_path) if xml_path.exists() else xml_path
            next_attempt = None
        else:
            status = "RETRYING"
            failed = Path(str(item["source_xml_path"]))
            delay = self.retry_delays[min(attempts - 1, len(self.retry_delays) - 1)]
            next_attempt = (datetime.now(UTC) + timedelta(seconds=delay)).strftime("%Y-%m-%d %H:%M:%S")
        with self.database.transaction() as connection:
            if next_attempt:
                connection.execute("UPDATE non_dicom_submissions SET status=?, next_attempt_at=?, last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, next_attempt, message, item["id"]))
            else:
                connection.execute("UPDATE non_dicom_submissions SET status=?, source_xml_path=?, last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, str(failed), message, item["id"]))
            self._event(connection, str(item["id"]), "PROCESSING_FAILED", status, message)
        LOGGER.warning("non_dicom_failed", submission_id=item["id"], status=status)
        return status

    def retry_now(self, submission_id: str) -> None:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT source_xml_path, status FROM non_dicom_submissions WHERE id = ?", (submission_id,)).fetchone()
            if row is None:
                raise KeyError("Tarefa Non-DICOM não encontrada")
            source = Path(str(row["source_xml_path"]))
            pending = self.storage.move_to_processing(source) if row["status"] == "FAILED" and source.exists() else source
            connection.execute("UPDATE non_dicom_submissions SET status='PENDING', source_xml_path=?, next_attempt_at=CURRENT_TIMESTAMP, last_error=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?", (str(pending), submission_id))
            self._event(connection, submission_id, "MANUAL_RETRY", "PENDING", "Reprocessamento manual solicitado")

    def retry_all_failed(self) -> int:
        rows = self.database.query_all("SELECT id FROM non_dicom_submissions WHERE status='FAILED'")
        for row in rows:
            self.retry_now(str(row["id"]))
        return len(rows)

    def list(self, history: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        statement = (
            "SELECT * FROM non_dicom_submissions WHERE status IN ('COMPLETED','FAILED') "
            "ORDER BY updated_at DESC LIMIT ?"
            if history
            else "SELECT * FROM non_dicom_submissions WHERE status NOT IN ('COMPLETED') "
            "ORDER BY updated_at DESC LIMIT ?"
        )
        rows = self.database.query_all(statement, (min(max(limit, 1), 1000),))
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, int]:
        rows = self.database.query_all("SELECT status, COUNT(*) AS count FROM non_dicom_submissions GROUP BY status")
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        return {
            "received": counts.get("RECEIVED", 0), "pending": counts.get("PENDING", 0),
            "processing": counts.get("PROCESSING", 0) + counts.get("VALIDATING", 0),
            "completed": counts.get("COMPLETED", 0), "failed": counts.get("FAILED", 0),
            "retry": counts.get("RETRYING", 0),
        }

    @staticmethod
    def _event(connection: Any, submission_id: str, event: str, status: str, message: str) -> None:
        connection.execute("INSERT INTO non_dicom_events(submission_id, event, status, message) VALUES (?, ?, ?, ?)", (submission_id, event, status, message[:500]))
