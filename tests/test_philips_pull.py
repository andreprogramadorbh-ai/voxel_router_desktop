from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from app.non_dicom.philips_pull import PhilipsPullDelivery


def test_submission_document_contains_required_philips_fields() -> None:
    xml = PhilipsPullDelivery._submission_xml(
        {"patient_id": "SYNTHETIC", "accession_number": "ACC-001", "modality": "CR", "site_id": "SITE-TEST"},
        "report-1-v1.pdf",
    )
    root = ET.fromstring(xml)
    document = root.find("document")
    assert document is not None
    assert document.findtext("task_patient_id") == "SYNTHETIC"
    assert document.findtext("task_accession_number") == "ACC-001"
    assert document.findtext("task_document_type") == "11502-2"
    assert document.findtext("task_file_path") == "report-1-v1.pdf"


def test_submission_document_requires_minimum_identifiers() -> None:
    try:
        PhilipsPullDelivery._submission_xml({"patient_id": "SYNTHETIC"}, "report.pdf")
    except Exception as exc:
        assert "insuficientes" in str(exc)
    else:
        raise AssertionError("missing accession must be rejected")


class _FakeClient:
    class config:
        site_id = "SITE-SYNTHETIC"

    configured = True

    def __init__(self) -> None:
        self.statuses: list[tuple[str, str]] = []

    async def philips_claim(self):
        return {"job": {"id": "42", "lease_token": "a" * 48, "report_version": 3, "metadata": {"patient_id": "SYNTHETIC", "accession_number": "ACC-SYNTHETIC", "modality": "CR"}}}

    async def philips_document(self, job_id: str, lease_token: str) -> bytes:
        assert job_id == "42" and lease_token == "a" * 48
        return b"%PDF-1.4 synthetic document " + (b"x" * 128)

    async def philips_status(self, job_id: str, lease_token: str, status: str, reference=None, error_category=None):
        assert job_id == "42" and lease_token == "a" * 48
        self.statuses.append((status, reference or error_category or ""))
        return {"ok": True}


@pytest.mark.asyncio
async def test_pull_stages_submission_and_reconciles_completed_file(tmp_path, database) -> None:
    input_dir, completed_dir, failed_dir = (tmp_path / "input", tmp_path / "completed", tmp_path / "failed")
    client = _FakeClient()
    delivery = PhilipsPullDelivery(database, {"philips_pull_enabled": True, "philips_input_path": str(input_dir), "philips_completed_path": str(completed_dir), "philips_failed_path": str(failed_dir)}, client)
    assert await delivery.claim_and_stage() is True
    pdf = input_dir / "report-42-v3.pdf"
    xml = input_dir / "voxel-42.xml"
    assert pdf.read_bytes().startswith(b"%PDF")
    assert ET.parse(xml).getroot().tag == "submission"
    assert client.statuses == [("package_submitted", "voxel-42")]
    completed_dir.mkdir(parents=True, exist_ok=True)
    (completed_dir / xml.name).write_bytes(xml.read_bytes())
    assert await delivery.reconcile_receiver_outcomes() == 1
    assert client.statuses[-1] == ("receiver_completed", "voxel-42")
