"""Fila persistente de transmissão, independente de memória e recuperável após reinício."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import Database
from app.core.logging import get_logger

LOGGER = get_logger(__name__)

DEFAULT_RETRY_DELAYS_SECONDS = (30, 120, 300, 900)
# Teto do backoff: nenhuma espera entre tentativas passa de 15 minutos.
MAX_RETRY_DELAY_SECONDS = 900
# Falhas que continuam em RETRY, mas alertam já na primeira ocorrência: não são queda de rede.
IMMEDIATE_ALERTS = {
    "DESTINATION_REJECTED": "O VOXEL recusou a conexão — verifique o Calling AE cadastrado na célula.",
    "TRANSFER_UNEXPECTED": "Falha interna do Router — contate o suporte.",
    "DESTINATION_NOT_VERIFIED": "Destino sem C-ECHO válido para a configuração atual — use Testar C-ECHO em Destinos.",
    "OUTBOUND_AE_NOT_CONFIGURED": "AE de saída (Calling AE) não configurado — defina-o em DICOM local.",
}
# Item já entregue (SENT, ou estudo VALIDATED) ou cancelado não pode ser pausado: retomá-lo reenviaria o estudo.
_PAUSABLE = "status NOT IN ('SENT', 'CANCELLED', 'PAUSED') AND study_id NOT IN (SELECT id FROM studies WHERE status = 'VALIDATED')"


class QueueStateError(ValueError):
    """Transição de fila não permitida no estado atual do item."""


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
    def __init__(self, database: Database, retry_delays_seconds: list[int] | None = None, pending_alert_minutes: int = 60) -> None:
        """Falhas de conectividade ficam em RETRY sem limite de tentativas; só rejeição definitiva vai a ERROR."""
        self.database = database
        delays = [min(max(int(delay), 1), MAX_RETRY_DELAY_SECONDS) for delay in (retry_delays_seconds or [])]
        self.retry_delays_seconds = delays or list(DEFAULT_RETRY_DELAYS_SECONDS)
        self.pending_alert_minutes = max(1, int(pending_alert_minutes))

    def retry_delay(self, attempt_count: int) -> int:
        """Backoff por tentativa (30 s, 2 min, 5 min, 15 min) e depois 15 min fixo."""
        index = min(max(attempt_count, 1) - 1, len(self.retry_delays_seconds) - 1)
        return self.retry_delays_seconds[index]

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
                    # Item já entregue volta à fila quando o estudo recebeu instâncias depois do envio.
                    cursor = connection.execute(
                        """INSERT INTO queue(study_id, destination_id, priority, status) VALUES (?, ?, ?, 'QUEUED')
                           ON CONFLICT(study_id, destination_id) DO UPDATE SET status = 'QUEUED', attempt_count = 0,
                           next_attempt_at = CURRENT_TIMESTAMP, completed_at = NULL, locked_at = NULL, updated_at = CURRENT_TIMESTAMP
                           WHERE queue.status = 'SENT'""",
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
            # Instância chegada durante o envio: o estudo tem mais objetos do que os enviados e volta a RECEIVED.
            connection.execute(
                """UPDATE studies SET status = CASE WHEN (SELECT COUNT(*) FROM instances WHERE study_id = ?) > ? THEN 'RECEIVED' ELSE 'VALIDATED' END,
                   sent_at = CURRENT_TIMESTAMP, validated_at = CURRENT_TIMESTAMP, last_error_code = NULL, last_error_message = NULL WHERE id = ?""",
                (item.study_id, instances_sent, item.study_id),
            )
            connection.execute("UPDATE queue SET status = 'SENT', completed_at = CURRENT_TIMESTAMP, locked_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (item.id,))
            connection.execute("UPDATE queue_attempts SET finished_at = CURRENT_TIMESTAMP, success = 1 WHERE queue_id = ? AND attempt_number = ?", (item.id, item.attempt_count))
            connection.execute("INSERT INTO transfers(queue_id, completed_at, bytes_sent, instances_sent, status, remote_reference, validation_reference) VALUES (?, CURRENT_TIMESTAMP, ?, ?, 'SUCCESS', ?, ?)", (item.id, bytes_sent, instances_sent, remote_reference, validation_reference))
        LOGGER.info("queue_item_completed", queue_id=item.id, bytes_sent=bytes_sent)

    def fail(self, item: QueueItem, error_code: str, error_message: str, permanent: bool = False) -> str:
        """Conectividade (permanent=False) -> RETRY sem limite; rejeição definitiva -> ERROR."""
        sanitized = error_message[:500]
        with self.database.transaction() as connection:
            if permanent:
                status = "ERROR"
                next_attempt = None
            else:
                status = "RETRY"
                delay = self.retry_delay(item.attempt_count)
                next_attempt = (datetime.now(UTC) + timedelta(seconds=delay)).strftime("%Y-%m-%d %H:%M:%S")
            if next_attempt:
                connection.execute("UPDATE queue SET status = ?, next_attempt_at = ?, locked_at = NULL, last_error_code = ?, last_error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (status, next_attempt, error_code, sanitized, item.id))
            else:
                connection.execute("UPDATE queue SET status = ?, locked_at = NULL, last_error_code = ?, last_error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (status, error_code, sanitized, item.id))
            connection.execute("UPDATE studies SET status = ?, last_error_code = ?, last_error_message = ? WHERE id = ?", (status, error_code, sanitized, item.study_id))
            connection.execute("UPDATE queue_attempts SET finished_at = CURRENT_TIMESTAMP, success = 0, error_code = ?, error_message = ? WHERE queue_id = ? AND attempt_number = ?", (error_code, sanitized, item.id, item.attempt_count))
            connection.execute("INSERT INTO transfers(queue_id, completed_at, status) VALUES (?, CURRENT_TIMESTAMP, 'FAILED')", (item.id,))
            if permanent:
                # A tabela errors registra o que exige ação do operador; falhas de rede ficam em queue_attempts.
                connection.execute("INSERT INTO errors(code, category, message, study_id) VALUES (?, 'TRANSFER', ?, ?)", (error_code, sanitized, item.study_id))
        LOGGER.warning("queue_item_failed", queue_id=item.id, error_code=error_code, next_status=status, attempt=item.attempt_count)
        return status

    def retry_now(self, queue_id: int) -> None:
        self._transition(queue_id, "RETRY", "UPDATE queue SET status = 'RETRY', next_attempt_at = CURRENT_TIMESTAMP, locked_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?")

    def pause(self, queue_id: int) -> None:
        self._transition(queue_id, "PAUSED", f"UPDATE queue SET status = 'PAUSED', locked_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND {_PAUSABLE}", "Item já enviado, validado ou cancelado não pode ser pausado")

    def resume(self, queue_id: int) -> None:
        # Só retoma o que foi pausado: nunca recoloca na fila um item já enviado.
        self._transition(queue_id, "QUEUED", "UPDATE queue SET status = 'QUEUED', next_attempt_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'PAUSED'", "Só é possível retomar um item pausado")

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

    def backlog_status(self) -> dict[str, Any]:
        """Pendência mais antiga (fila ativa ou estudo pronto sem fila) e último envio confirmado."""
        # PAUSED fica de fora: é decisão explícita do operador, não atraso de envio.
        pending = self.database.query_one(
            """SELECT COUNT(*) AS pending_count, MIN(since) AS oldest_pending_at,
                   CAST((julianday('now') - julianday(MIN(since))) * 1440 AS INTEGER) AS oldest_pending_minutes
               FROM (
                   SELECT created_at AS since FROM queue WHERE status IN ('QUEUED', 'RETRY', 'SENDING')
                   UNION ALL
                   SELECT COALESCE(ready_at, last_instance_at) FROM studies WHERE status = 'READY_TO_SEND'
               )"""
        )
        last = self.database.query_one("SELECT MAX(completed_at) AS last_success_at FROM transfers WHERE status = 'SUCCESS'")
        paused = self.database.query_one("SELECT COUNT(*) AS n FROM queue WHERE status = 'PAUSED'")
        # Consultas agregadas sempre devolvem exatamente uma linha.
        minutes = pending["oldest_pending_minutes"]
        delayed = minutes is not None and int(minutes) >= self.pending_alert_minutes
        alerts = self._immediate_alerts()
        if delayed:
            alerts.append({
                "code": "PENDING_DELAY",
                "count": int(pending["pending_count"]),
                "message": f"Envio atrasado há mais de {self.pending_alert_minutes} min. Verifique a conexão com o VOXEL.",
            })
        return {
            "pending_count": int(pending["pending_count"]),
            "paused_count": int(paused["n"]),
            "oldest_pending_at": pending["oldest_pending_at"],
            "oldest_pending_minutes": minutes,
            "alert_threshold_minutes": self.pending_alert_minutes,
            "alert": delayed,
            "alerts": alerts,
            "last_success_at": last["last_success_at"],
        }

    def _immediate_alerts(self) -> list[dict[str, Any]]:
        """Itens ativos cuja última falha não é de rede comum (ver IMMEDIATE_ALERTS)."""
        rows = self.database.query_all(
            """SELECT last_error_code AS code, COUNT(*) AS count FROM queue
               WHERE status IN ('QUEUED', 'RETRY', 'SENDING') AND last_error_code IN ('DESTINATION_REJECTED', 'TRANSFER_UNEXPECTED', 'DESTINATION_NOT_VERIFIED', 'OUTBOUND_AE_NOT_CONFIGURED')
               GROUP BY last_error_code ORDER BY last_error_code"""
        )
        return [{"code": str(row["code"]), "count": int(row["count"]), "message": IMMEDIATE_ALERTS[str(row["code"])]} for row in rows]

    def list_items(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.database.query_all(
            """SELECT q.id, q.priority, q.status, q.attempt_count, q.next_attempt_at, q.last_error_code,
               s.status AS study_status, s.study_instance_uid, s.patient_id, s.patient_name, s.modalities_in_study, s.total_bytes, s.received_at,
               d.name AS destination_name
               FROM queue q JOIN studies s ON s.id = q.study_id JOIN destinations d ON d.id = q.destination_id
               ORDER BY q.priority ASC, q.created_at DESC LIMIT ?""",
            (min(max(limit, 1), 1000),),
        )
        return [dict(row) for row in rows]

    def _transition(self, queue_id: int, state: str, sql: str, refused: str = "") -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(sql, (queue_id,))
            if cursor.rowcount != 1:
                if connection.execute("SELECT 1 FROM queue WHERE id = ?", (queue_id,)).fetchone() is None:
                    raise KeyError("Item de fila não encontrado")
                raise QueueStateError(refused or "Transição de fila não permitida")
        LOGGER.info("queue_item_transition", queue_id=queue_id, state=state)
