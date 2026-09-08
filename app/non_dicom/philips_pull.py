"""Saída Philips/VUE por pull: o Router grava PDF e XML de forma atômica em armazenamento local configurado."""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from app.core.database import Database
from app.core.logging import get_logger
from app.non_dicom.cloud import NonDicomCloudClient
from app.non_dicom.parsers import safe_file_name

LOGGER = get_logger(__name__)


class PhilipsPullError(RuntimeError):
    """Falha sanitizada de preparação local Philips."""


class PhilipsPullDelivery:
    def __init__(self, database: Database, config: dict[str, Any], client: NonDicomCloudClient) -> None:
        self.database = database
        self.config = config
        self.client = client

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("philips_pull_enabled", False)) and self.client.configured

    def _directory(self, key: str) -> Path:
        raw = str(self.config.get(key, "")).strip()
        if not raw:
            raise PhilipsPullError("Diretório Philips não configurado")
        path = Path(raw)
        if not path.is_absolute():
            raise PhilipsPullError("Diretório Philips deve ser absoluto")
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def claim_and_stage(self) -> bool:
        if not self.enabled:
            return False
        response = await self.client.philips_claim()
        job = response.get("job") if isinstance(response, dict) else None
        if not isinstance(job, dict):
            return False
        job_id = str(job.get("id") or "")
        lease_token = str(job.get("lease_token") or "")
        metadata = dict(job.get("metadata") or {}) if isinstance(job.get("metadata"), dict) else {}
        metadata.setdefault("site_id", self.client.config.site_id)
        if not job_id.isdigit() or len(lease_token) < 32 or not metadata:
            raise PhilipsPullError("Resposta de claim Philips inválida")
        content = await self.client.philips_document(job_id, lease_token)
        if len(content) < 100 or not content.startswith(b"%PDF"):
            raise PhilipsPullError("Documento Philips não é PDF válido")
        input_dir = self._directory("philips_input_path")
        file_name = safe_file_name(f"report-{job_id}-v{int(job.get('report_version') or 0)}.pdf")
        pdf_path = input_dir / file_name
        xml_name = safe_file_name(f"voxel-{job_id}.xml")
        xml_path = input_dir / xml_name
        self._atomic_write(pdf_path, content)
        self._atomic_write(xml_path, self._submission_xml(metadata, file_name))
        await self.client.philips_status(job_id, lease_token, "package_submitted", reference=f"voxel-{job_id}")
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO philips_pull_deliveries(remote_job_id, lease_token, xml_file_name, status)
                   VALUES (?, ?, ?, 'PACKAGE_SUBMITTED')
                   ON CONFLICT(remote_job_id) DO UPDATE SET lease_token=excluded.lease_token, xml_file_name=excluded.xml_file_name, status='PACKAGE_SUBMITTED', updated_at=CURRENT_TIMESTAMP""",
                (job_id, lease_token, xml_name),
            )
        LOGGER.info("philips_pull_package_submitted", job_id=job_id)
        return True

    async def reconcile_receiver_outcomes(self) -> int:
        if not self.enabled:
            return 0
        completed = self._directory("philips_completed_path")
        failed = self._directory("philips_failed_path")
        rows = self.database.query_all("SELECT remote_job_id, lease_token, xml_file_name FROM philips_pull_deliveries WHERE status='PACKAGE_SUBMITTED'")
        changed = 0
        for row in rows:
            job_id, lease, xml_name = str(row["remote_job_id"]), str(row["lease_token"]), str(row["xml_file_name"])
            if (completed / xml_name).is_file():
                await self.client.philips_status(job_id, lease, "receiver_completed", reference=f"voxel-{job_id}")
                status = "RECEIVER_COMPLETED"
            elif (failed / xml_name).is_file():
                await self.client.philips_status(job_id, lease, "receiver_failed", error_category="receiver_failed")
                status = "RECEIVER_FAILED"
            else:
                continue
            with self.database.transaction() as connection:
                connection.execute("UPDATE philips_pull_deliveries SET status=?, updated_at=CURRENT_TIMESTAMP WHERE remote_job_id=?", (status, job_id))
            changed += 1
        return changed

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)

    @staticmethod
    def _submission_xml(metadata: dict[str, Any], file_name: str) -> bytes:
        patient_id = str(metadata.get("patient_id") or "")
        accession = str(metadata.get("accession_number") or "")
        if not patient_id or not accession:
            raise PhilipsPullError("Metadados Philips insuficientes")
        root = ET.Element("submission")
        document = ET.SubElement(root, "document")
        values = {
            "task_patient_id": patient_id,
            "task_patient_name": str(metadata.get("patient_name") or ""),
            "task_patient_birth_date": str(metadata.get("patient_birth_date") or ""),
            "task_patient_sex": str(metadata.get("patient_sex") or ""),
            "task_accession_number": accession,
            "task_modality": str(metadata.get("modality") or ""),
            "task_site_id": str(metadata.get("site_id") or ""),
            "task_file_path": file_name,
            "task_file_name": file_name,
            "task_document_mimetype": "application/pdf",
            "task_document_name": "Radiology report",
            "task_document_type": "11502-2",
        }
        for key, value in values.items():
            if value:
                ET.SubElement(document, key).text = value
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)
