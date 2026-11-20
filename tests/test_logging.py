from __future__ import annotations

import logging

import pytest
from pydicom.uid import generate_uid

from app.core.logging import configure_logging, get_logger, redact_value

STUDY_UID = generate_uid()
SOP_UID = generate_uid()
PATIENT_NAME = "PACIENTE^SINTETICO"


@pytest.fixture
def log_file(paths):
    root = logging.getLogger()
    previous_handlers, previous_level = root.handlers[:], root.level
    configure_logging(paths)
    yield paths.logs / "router.jsonl"
    for handler in root.handlers:
        handler.close()
    root.handlers[:] = previous_handlers
    root.setLevel(previous_level)


def test_log_with_uid_and_patient_fields_is_sanitized(log_file):
    get_logger("teste").info(
        "dicom_received",
        study_instance_uid=STUDY_UID,
        sop_instance_uid=SOP_UID,
        patient_name=PATIENT_NAME,
        detail=f"falha ao enviar instância {SOP_UID}",
        size_bytes=123,
    )
    # Logs de bibliotecas (stdlib logging) passam pelo mesmo filtro.
    logging.getLogger("pynetdicom").warning("Store request for %s", STUDY_UID)
    for handler in logging.getLogger().handlers:
        handler.flush()

    content = log_file.read_text(encoding="utf-8")
    assert "dicom_received" in content
    assert '"size_bytes": 123' in content
    for forbidden in (STUDY_UID, SOP_UID, PATIENT_NAME, "SINTETICO"):
        assert forbidden not in content
    assert "[UID]" in content


@pytest.mark.parametrize(
    "key",
    ["study_instance_uid", "SeriesInstance_UID", "uid", "patient_id", "PatientName", "nome_paciente", "patient_birth_date", "birthdate", "patient_name"],
)
def test_sensitive_keys_are_redacted(key):
    assert redact_value({key: "valor"}) == {key: "[REDACTED]"}


@pytest.mark.parametrize("key", ["hostname", "username", "destination_name", "calling_ae", "called_ae", "name"])
def test_operational_keys_are_kept(key):
    assert redact_value({key: "valor"}) == {key: "valor"}


def test_uid_in_message_is_masked_but_ips_and_versions_are_kept():
    text = "estudo 1.2.840.113619.2.55.3 enviado a 10.8.0.1 com Orthanc 1.13.0 e IP 1.2.3.4"
    assert redact_value(text) == "estudo [UID] enviado a 10.8.0.1 com Orthanc 1.13.0 e IP 1.2.3.4"


def test_non_sensitive_fields_are_kept():
    event = {"event": "queue_item_failed", "queue_id": 7, "error_code": "DESTINATION_UNAVAILABLE", "nested": [{"study_uid": "x"}]}
    assert redact_value(event) == {"event": "queue_item_failed", "queue_id": 7, "error_code": "DESTINATION_UNAVAILABLE", "nested": [{"study_uid": "[REDACTED]"}]}
