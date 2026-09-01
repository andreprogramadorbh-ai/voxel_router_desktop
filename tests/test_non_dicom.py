from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.non_dicom.parsers import NonDicomParseError, parse_xml
from app.non_dicom.worker import NonDicomWorker


def submission_xml(**overrides: str) -> bytes:
    fields = {
        "task_patient_id": "PATIENT-001",
        "task_patient_humanname_family": "SILVA",
        "task_patient_humanname_given": "TESTE",
        "task_document_name": "Laudo",
        "task_file_path": "sample/report.pdf",
        "task_file_name": "report.pdf",
        "task_accession_number": "ACC-001",
        "task_document_mimetype": "application/pdf",
        "task_modalities": "OT",
        "task_delete_file": "true",
        "task_document_type": "11502-2",
    }
    fields.update(overrides)
    body = "".join(f"<{key}>{value}</{key}>" for key, value in fields.items())
    return f'<?xml version="1.0" encoding="UTF-8"?><submission><document>{body}</document></submission>'.encode()


def wtt_xml(payload: bytes = b"%PDF-1.4") -> bytes:
    encoded = base64.b64encode(payload).decode()
    return f"""<WTT_ITEM><SITE>UNIT</SITE><PATIENT_ID>P-2</PATIENT_ID><PATIENT_NAME>SILVA^TESTE</PATIENT_NAME><PATIENT_BIRTHDATE>19900101</PATIENT_BIRTHDATE><PATIENT_SEX>F</PATIENT_SEX><ACCESSION_NUMBER>ACC-2</ACCESSION_NUMBER><MODALITY>OT</MODALITY><REQUESTED_PROCEDURE_DESCRIPTION>RELATORIO</REQUESTED_PROCEDURE_DESCRIPTION><REQUESTED_PROCEDURE_ID>PROC-2</REQUESTED_PROCEDURE_ID><DATE>20260901</DATE><TIME>120000</TIME><REPORT_TYPE>KEY_IMAGES</REPORT_TYPE><REPORT_BASE64>{encoded}</REPORT_BASE64></WTT_ITEM>""".encode()


def worker(settings, database) -> NonDicomWorker:
    return NonDicomWorker(settings, database)


def add_managed_pdf(instance: NonDicomWorker, content: bytes = b"%PDF-1.4 test") -> Path:
    target = instance.paths.files / "sample" / "report.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def queue_one(instance: NonDicomWorker, raw_xml: bytes | None = None) -> str:
    add_managed_pdf(instance)
    xml_path = instance.paths.input / "submission.xml"
    xml_path.write_bytes(raw_xml or submission_xml())
    submission_id = instance.ingest_file(xml_path)
    assert submission_id is not None
    return submission_id


def test_philips_submission_valid_maps_all_required_fields():
    submission = parse_xml(submission_xml()).submission
    assert submission.source_format == "PHILIPS_SUBMISSION"
    assert submission.patient_id == "PATIENT-001"
    assert submission.accession_number == "ACC-001"
    assert submission.is_report is True
    assert submission.modality == "OT"


@pytest.mark.parametrize("changes", [{"task_patient_id": ""}, {"task_accession_number": ""}])
def test_submission_rejects_missing_patient_or_accession(changes):
    with pytest.raises(NonDicomParseError):
        parse_xml(submission_xml(**changes))


def test_invalid_xml_is_rejected():
    with pytest.raises(NonDicomParseError):
        parse_xml(b"<submission><document>")


def test_wtt_item_materializes_embedded_report(settings, database):
    instance = worker(settings, database)
    xml_path = instance.paths.input / "embedded.xml"
    xml_path.write_bytes(wtt_xml())
    submission_id = instance.ingest_file(xml_path)
    item = database.query_one("SELECT * FROM non_dicom_submissions WHERE id=?", (submission_id,))
    assert item is not None
    assert item["source_format"] == "PHILIPS_WTT_ITEM"
    assert Path(item["file_path"]).read_bytes().startswith(b"%PDF")


def test_missing_file_and_invalid_mime_move_xml_to_failed(settings, database):
    instance = worker(settings, database)
    missing = instance.paths.input / "missing.xml"
    missing.write_bytes(submission_xml())
    assert instance.ingest_file(missing) is None
    assert any(instance.paths.failed.glob("missing*.xml"))
    add_managed_pdf(instance)
    invalid = instance.paths.input / "mime.xml"
    invalid.write_bytes(submission_xml(task_document_mimetype="image/unsupported"))
    assert instance.ingest_file(invalid) is None
    assert any(instance.paths.failed.glob("mime*.xml"))


def test_path_traversal_is_blocked(settings, database):
    instance = worker(settings, database)
    xml_path = instance.paths.input / "traversal.xml"
    xml_path.write_bytes(submission_xml(task_file_path="../../Windows/system.ini"))
    assert instance.ingest_file(xml_path) is None
    assert any(instance.paths.failed.glob("traversal*.xml"))


def test_duplicate_xml_is_not_enqueued_twice(settings, database):
    instance = worker(settings, database)
    add_managed_pdf(instance)
    first = instance.paths.input / "one.xml"
    second = instance.paths.input / "two.xml"
    raw = submission_xml()
    first.write_bytes(raw)
    second.write_bytes(raw)
    assert instance.ingest_file(first)
    assert instance.ingest_file(second)
    assert database.query_one("SELECT COUNT(*) AS c FROM non_dicom_submissions")["c"] == 1
    assert any(instance.paths.completed.glob("two*.xml"))


class AcceptedClient:
    configured = True

    async def upload(self, *args, **kwargs):
        return {"reference": "REMOTE-1"}

    async def acknowledge(self, *args, **kwargs):
        return {}

    async def status(self):
        return {"status": "ok"}


class FailingClient(AcceptedClient):
    async def upload(self, *args, **kwargs):
        raise RuntimeError("endpoint indisponível")


@pytest.mark.asyncio
async def test_processing_success_moves_xml_to_completed(settings, database, monkeypatch):
    instance = worker(settings, database)
    queue_one(instance)
    monkeypatch.setattr(instance, "_client", lambda: AcceptedClient())
    assert await instance.process_once() is True
    row = database.query_one("SELECT status FROM non_dicom_submissions")
    assert row["status"] == "COMPLETED"
    assert any(instance.paths.completed.glob("submission*.xml"))


@pytest.mark.asyncio
async def test_processing_error_retries_then_fails(settings, database, monkeypatch):
    instance = worker(settings, database)
    submission_id = queue_one(instance)
    instance.manager.max_attempts = 1
    monkeypatch.setattr(instance, "_client", lambda: FailingClient())
    assert await instance.process_once() is False
    row = database.query_one("SELECT status FROM non_dicom_submissions WHERE id=?", (submission_id,))
    assert row["status"] == "FAILED"
    instance.manager.retry_now(submission_id)
    assert database.query_one("SELECT status FROM non_dicom_submissions WHERE id=?", (submission_id,))["status"] == "PENDING"


def test_polling_discovers_new_xml(settings, database):
    instance = worker(settings, database)
    add_managed_pdf(instance)
    (instance.paths.input / "poll.xml").write_bytes(submission_xml())
    assert instance.scan_input() == 1
    assert instance.manager.stats()["pending"] == 1


@pytest.mark.asyncio
async def test_unconfigured_or_unavailable_cloud_is_reported(settings, database):
    instance = worker(settings, database)
    assert (await instance.test_connection())["status"] == "DISCONNECTED"
    settings.update("non_dicom", {"voxel_pacs_url": "https://127.0.0.1:1"})
    assert (await instance.test_connection())["status"] == "DISCONNECTED"


class PendingCloudClient(AcceptedClient):
    async def pending(self):
        return {"items": [{"id": "remote-1", "metadata": {"task_patient_id": "PULL-1", "task_accession_number": "ACC-PULL", "task_file_name": "pull.pdf", "task_document_mimetype": "application/pdf", "task_modalities": "OT"}}]}

    async def document(self, document_id: str):
        assert document_id == "remote-1"
        return b"%PDF-1.4 remote"


@pytest.mark.asyncio
async def test_cloud_pending_creates_managed_file_and_xml(settings, database, monkeypatch):
    instance = worker(settings, database)
    monkeypatch.setattr(instance, "_client", lambda: PendingCloudClient())
    assert await instance.sync_pending_from_cloud() == 1
    assert len(list(instance.paths.input.glob("*.xml"))) == 1
    assert instance.scan_input() == 1
    assert instance.manager.stats()["pending"] == 1
    assert instance.status()["voxel_pacs"] == "CONNECTED"


def test_external_xml_entity_is_rejected_safely():
    malicious = b'<!DOCTYPE submission [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><submission><document><task_patient_id>&xxe;</task_patient_id></document></submission>'
    with pytest.raises(NonDicomParseError):
        parse_xml(malicious)
