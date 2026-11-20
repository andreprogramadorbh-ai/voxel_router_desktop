"""Controle de acesso do SCP: só modalidades cadastradas (Calling AE + IP/CIDR) podem enviar C-STORE."""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import Database
from app.core.logging import get_logger

LOGGER = get_logger(__name__)

LEARNING_SETTING_KEY = "dicom.learning_mode_until"
LEARNING_MAX_HOURS = 24
# Faixas mais amplas que isso liberariam a rede inteira da clínica (ou mais) para um único AE.
MIN_IPV4_PREFIX = 16
MIN_IPV6_PREFIX = 64
AE_TITLE_MAX_LENGTH = 16
# "Últimas recusas": agregadas por (AE, IP, Called AE, motivo); só as mais recentes são mantidas.
MAX_RECORDED_ATTEMPTS = 50
_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def normalize_ae_title(value: str) -> str:
    """Valida AE Title DICOM (1–16 caracteres, sem barra invertida nem controle; espaços nas pontas descartados)."""
    title = str(value or "").strip()
    if not 1 <= len(title) <= AE_TITLE_MAX_LENGTH:
        raise ValueError("AE Title deve ter de 1 a 16 caracteres")
    if "\\" in title or any(ord(char) < 32 or ord(char) > 126 for char in title):
        raise ValueError("AE Title contém caracteres inválidos")
    return title


def normalize_host_network(value: str) -> str:
    """Aceita IP único ou CIDR; recusa nomes de host e faixas amplas demais."""
    text = str(value or "").strip()
    try:
        network = ipaddress.ip_network(text, strict=False)
    except ValueError as exc:
        raise ValueError("Informe o IP da modalidade (ex.: 192.168.0.50) ou uma faixa CIDR (ex.: 192.168.0.0/24)") from exc
    minimum = MIN_IPV4_PREFIX if network.version == 4 else MIN_IPV6_PREFIX
    if network.prefixlen < minimum:
        raise ValueError(f"Faixa CIDR ampla demais; use /{minimum} ou mais específica")
    if network.num_addresses == 1:
        return str(network.network_address)
    return str(network)


def _source_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        address = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


class DicomAccessControl:
    def __init__(self, database: Database) -> None:
        self.database = database

    # --- Autorização -------------------------------------------------------------------------
    def is_allowed(self, calling_ae: str, source_ip: str) -> bool:
        """True somente se existir modalidade habilitada com o mesmo AE e o IP dentro do host/CIDR cadastrado."""
        address = _source_address(source_ip)
        ae_title = str(calling_ae or "").strip()
        if address is None or not ae_title:
            return False
        rows = self.database.query_all("SELECT host FROM dicom_nodes WHERE enabled = 1 AND TRIM(ae_title) = ?", (ae_title,))
        for row in rows:
            try:
                if address in ipaddress.ip_network(str(row["host"]).strip(), strict=False):
                    return True
            except ValueError:
                # Cadastro antigo com nome de host: não autoriza (o SCP só conhece o IP de origem).
                continue
        return False

    def node_counts(self) -> dict[str, int]:
        row = self.database.query_one("SELECT COUNT(*) AS total, COALESCE(SUM(enabled), 0) AS enabled FROM dicom_nodes")
        return {"total": int(row["total"]), "enabled": int(row["enabled"])}

    # --- Modo aprendizado ----------------------------------------------------------------------
    def learning_until(self) -> str | None:
        row = self.database.query_one("SELECT value FROM settings WHERE key = ?", (LEARNING_SETTING_KEY,))
        if row is None:
            return None
        try:
            until = datetime.strptime(str(row["value"]), _TIMESTAMP_FORMAT).replace(tzinfo=UTC)
        except ValueError:
            return None
        return str(row["value"]) if until > datetime.now(UTC) else None

    def learning_active(self) -> bool:
        return self.learning_until() is not None

    def enable_learning(self, hours: int) -> str:
        if not 1 <= int(hours) <= LEARNING_MAX_HOURS:
            raise ValueError(f"O modo aprendizado aceita de 1 a {LEARNING_MAX_HOURS} horas")
        until = (datetime.now(UTC) + timedelta(hours=int(hours))).strftime(_TIMESTAMP_FORMAT)
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
                (LEARNING_SETTING_KEY, until),
            )
        LOGGER.info("dicom_learning_mode_enabled", until=until)
        return until

    def disable_learning(self) -> None:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM settings WHERE key = ?", (LEARNING_SETTING_KEY,))
        LOGGER.info("dicom_learning_mode_disabled")

    def record_attempt(self, calling_ae: str, called_ae: str, source_ip: str, reason: str) -> bool:
        """Registra a tentativa de forma agregada. Nunca libera envio.

        Recusas (UNKNOWN_NODE, CALLED_AE) são sempre registradas; C-ECHO de origem desconhecida
        (ECHO_ONLY, que é aceito) só com o modo aprendizado ativo. Mantém só as últimas 50.
        """
        if reason == "ECHO_ONLY" and not self.learning_active():
            return False
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO dicom_access_attempts(calling_ae, called_ae, source_ip, reason) VALUES (?, ?, ?, ?)
                   ON CONFLICT(calling_ae, called_ae, source_ip, reason)
                   DO UPDATE SET attempt_count = attempt_count + 1, last_seen_at = CURRENT_TIMESTAMP""",
                (str(calling_ae).strip()[:64], str(called_ae).strip()[:64], str(source_ip)[:64], reason),
            )
            connection.execute(
                """DELETE FROM dicom_access_attempts WHERE id NOT IN (
                       SELECT id FROM dicom_access_attempts ORDER BY last_seen_at DESC, id DESC LIMIT ?)""",
                (MAX_RECORDED_ATTEMPTS,),
            )
        return True

    def list_attempts(self) -> list[dict[str, Any]]:
        rows = self.database.query_all("SELECT * FROM dicom_access_attempts ORDER BY last_seen_at DESC, id DESC LIMIT ?", (MAX_RECORDED_ATTEMPTS,))
        return [dict(row) for row in rows]
