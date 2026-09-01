"""Fila persistente de transmissão, independente de memória e recuperável após reinício."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import Database
from app.core.logging import get_logger

LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class QueueItem:
    id: int
    study_id: int
    destination_id: int
    priority: int
    attempt_count: int
    study_instance_uid: str
    status: str


class QueueManager:
    def __init__(self, database: Database, retry_delays_seconds: list[int], max_attempts: int) -> None:
        self.database = database
        self.retry_delays_seconds = retry_delays_seconds
        self.max_attempts = max_attempts

    def recover_after_restart(self) -> int:
        """Nunca deixa itens presos em SENDING depois de uma queda de processo ou reboot."""
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """UPDATE queue SET status = 'RETRY', locked_at = NULL, next_attempt_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP, last_error_code = COALESCE(last_error_code, 'RECOVERED_AFTER_RESTART')
                   WHERE status = 'SENDING'"""
            )
            count = cursor.rowcount
        if count:
            LOGGER.warning("queue_recovered_after_restart", item_count=count)
        return count

    def mark_complete_studies_ready(self, quiet_window_seconds: int) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """UPDATE studies SET status = 'READY_TO_SEND', ready_at = CURRENT_TIMESTAMP
                   WHERE status IN ('RECEIVED', 'PROCESSING')
                   AND last_instance_at <= datetime('now', ?)
                   AND instance_count > 0""",
                (f"-{max(0, quiet_window_seconds)} seconds",),
            )
            return cursor.rowcount

    def enqueue_ready_studies(self) -> int:
        with self.database.transaction() as connection:
            destinations = connection.execute("SELECT id, priority FROM destinations WHERE enabled = 1").fetchall()
            if not destinations:
                return 0
            studies = connection.execute("SELECT id FROM studies WHERE status = 'READY_TO_SEND'").fetchall()
            added = 0
            for study in studies:
                for destination in destinations:
                    cursor = connection.execute(
                        "INSERT OR IGNORE INTO queue(study_id, destination_id, priority, status) VALUES (?, ?, ?, 'QUEUED')",
                        (study["id"], destination["id"], destination["priority"]),
                    )
                    added += max(cursor.rowcount, 0)
                connection.execute("UPDATE studies SET status = 'QUEUED' WHERE id = ?", (study["id"],))
            return added

    def claim_next(self) -> QueueItem | None:
        """Reivindica um único item em transação IMMEDIATE, seguro contra workers concorrentes."""
        with self.database.transaction() as connection:
            row = connection.execute(
                """SELECT q.id, q.study_id, q.destination_id, q.priority, q.attempt_count, q.status, s.study_instance_uid
                   FROM queue q JOIN studies s ON s.id = q.study_id
                   WHERE q.status IN ('QUEUED', 'RETRY') AND q.next_attempt_at <= CURRENT_TIMESTAMP
                   ORDER BY q.priority ASC, q.created_at ASC, q.id ASC LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            new_attempt = int(row["attempt_count"]) + 1
            connection.execute("UPDATE queue SET status = 'SENDING', attempt_count = ?, locked_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_attempt, row["id"]))
            connection.execute("UPDATE studies SET status = 'SENDING' WHERE id = ?", (row["study_id"],))
            connection.execute("INSERT INTO queue_attempts(queue_id, attempt_number) VALUES (?, ?)", (row["id"], new_attempt))
            return QueueItem(int(row["id"]), int(row["study_id"]), int(row["destination_id"]), int(row["priority"]), new_attempt, str(row["study_instance_uid"]), "SENDING")

    def complete(self, item: QueueItem, bytes_sent: int, instances_sent: int, remote_reference: str | None = None, validation_reference: str | None = None) -> None:
        with self.database.transaction() as connection:
            connection.execute("UPDATE queue SET status = 'SENT', completed_at = CURRENT_TIMESTAMP, locked_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (item.id,))
            connection.execute("UPDATE studies SET status = 'VALIDATED', sent_at = CURRENT_TIMESTAMP, validated_at = CURRENT_TIMESTAMP, last_error_code = NULL, last_error_message = NULL WHERE id = ?", (item.study_id,))
            connection.execute("UPDATE queue_attempts SET finished_at = CURRENT_TIMESTAMP, success = 1 WHERE queue_id = ? AND attempt_number = ?", (item.id, item.attempt_count))
            connection.execute("INSERT INTO transfers(queue_id, completed_at, bytes_sent, instances_sent, status, remote_reference, validation_reference) VALUES (?, CURRENT_TIMESTAMP, ?, ?, 'SUCCESS', ?, ?)", (item.id, bytes_sent, instances_sent, remote_reference, validation_reference))
        LOGGER.info("queue_item_completed", queue_id=item.id, study_instance_uid=item.study_instance_uid, bytes_sent=bytes_sent)

    def fail(self, item: QueueItem, error_code: str, error_message: str) -> str:
        sanitized = error_message[:500]
        with self.database.transaction() as connection:
            if item.attempt_count >= self.max_attempts:
                status = "ERROR"
                next_attempt = None
            else:
                status = "RETRY"
                delay = self.retry_delays_seconds[min(item.attempt_count - 1, len(self.retry_delays_seconds) - 1)]
                next_attempt = (datetime.now(UTC) + timedelta(seconds=delay)).strftime("%Y-%m-%d %H:%M:%S")
            if next_attempt:
                connection.execute("UPDATE queue SET status = ?, next_attempt_at = ?, locked_at = NULL, last_error_code = ?, last_error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (status, next_attempt, error_code, sanitized, item.id))
            else:
                connection.execute("UPDATE queue SET status = ?, locked_at = NULL, last_error_code = ?, last_error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (status, error_code, sanitized, item.id))
            connection.execute("UPDATE studies SET status = ?, last_error_code = ?, last_error_message = ? WHERE id = ?", (status, error_code, sanitized, item.study_id))
            connection.execute("UPDATE queue_attempts SET finished_at = CURRENT_TIMESTAMP, success = 0, error_code = ?, error_message = ? WHERE queue_id = ? AND attempt_number = ?", (error_code, sanitized, item.id, item.attempt_count))
            connection.execute("INSERT INTO transfers(queue_id, completed_at, status) VALUES (?, CURRENT_TIMESTAMP, 'FAILED')", (item.id,))
            connection.execute("INSERT INTO errors(code, category, message, study_id) VALUES (?, 'TRANSFER', ?, ?)", (error_code, sanitized, item.study_id))
        LOGGER.warning("queue_item_failed", queue_id=item.id, error_code=error_code, next_status=status)
        return status

    def retry_now(self, queue_id: int) -> None:
        self._transition(queue_id, "RETRY", "UPDATE queue SET status = 'RETRY', next_attempt_at = CURRENT_TIMESTAMP, locked_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?")

    def pause(self, queue_id: int) -> None:
        self._transition(queue_id, "PAUSED", "UPDATE queue SET status = 'PAUSED', locked_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?")

    def resume(self, queue_id: int) -> None:
        self._transition(queue_id, "QUEUED", "UPDATE queue SET status = 'QUEUED', next_attempt_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?")

    def cancel(self, queue_id: int) -> None:
        self._transition(queue_id, "CANCELLED", "UPDATE queue SET status = 'CANCELLED', locked_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?")

    def set_priority(self, queue_id: int, priority: int) -> None:
        if priority not in (1, 2, 3, 4):
            raise ValueError("Prioridade deve ser CRITICAL (1), HIGH (2), NORMAL (3) ou LOW (4)")
        with self.database.transaction() as connection:
            connection.execute("UPDATE queue SET priority = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (priority, queue_id))

    def stats(self) -> dict[str, int]:
        rows = self.database.query_all("SELECT status, COUNT(*) AS count FROM studies GROUP BY status")
        by_status = {str(row["status"]): int(row["count"]) for row in rows}
        return {
            "received": by_status.get("RECEIVED", 0) + by_status.get("PROCESSING", 0),
            "pending": by_status.get("READY_TO_SEND", 0) + by_status.get("QUEUED", 0),
            "sending": by_status.get("SENDING", 0),
            "sent": by_status.get("SENT", 0) + by_status.get("VALIDATED", 0),
            "errors": by_status.get("ERROR", 0),
            "retry": by_status.get("RETRY", 0),
        }

    def list_items(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.database.query_all(
            """SELECT q.id, q.priority, q.status, q.attempt_count, q.next_attempt_at, q.last_error_code,
               s.study_instance_uid, s.patient_id, s.patient_name, s.modalities_in_study, s.total_bytes, s.received_at,
               d.name AS destination_name
               FROM queue q JOIN studies s ON s.id = q.study_id JOIN destinations d ON d.id = q.destination_id
               ORDER BY q.priority ASC, q.created_at DESC LIMIT ?""",
            (min(max(limit, 1), 1000),),
        )
        return [dict(row) for row in rows]

    def _transition(self, queue_id: int, state: str, sql: str) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(sql, (queue_id,))
            if cursor.rowcount != 1:
                raise KeyError("Item de fila não encontrado")
        LOGGER.info("queue_item_transition", queue_id=queue_id, state=state)
