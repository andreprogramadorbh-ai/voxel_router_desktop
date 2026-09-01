"""Parsers estritos nos requisitos mínimos e tolerantes a campos Philips futuros."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from pathlib import Path

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

from app.non_dicom.models import NonDicomSubmission


class NonDicomParseError(ValueError):
    """XML Philips inválido ou sem campo necessário para criar uma tarefa."""


@dataclass(frozen=True)
class ParsedXml:
    submission: NonDicomSubmission
    raw_xml: bytes


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].strip()


def _fields(parent: ET.Element) -> dict[str, str]:
    return {_tag(child): (child.text or "").strip() for child in list(parent)}


def _required(fields: dict[str, str], *names: str) -> None:
    missing = [name for name in names if not fields.get(name)]
    if missing:
        raise NonDicomParseError(f"Campos obrigatórios ausentes: {', '.join(missing)}")


def _bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "sim"}


def _name(*parts: str | None) -> str | None:
    value = " ".join(part.strip() for part in parts if part and part.strip())
    return value or None


class PhilipsSubmissionParser:
    """Parser do XML `<submission><document>` especificado pela Philips."""

    known = {
        "task_patient_id", "task_patient_humanname_family", "task_patient_humanname_given",
        "task_patient_humanname_middle", "task_document_name", "task_document_date", "task_image_date",
        "task_file_path", "task_file_name", "task_accession_number", "task_document_mimetype",
        "task_patient_birthday", "task_patient_gender", "task_site_id", "task_patient_issuer",
        "task_author_id", "task_author_humanname_family", "task_author_humanname_given",
        "task_author_humanname_middle", "task_modalities", "task_delete_file", "task_document_type",
    }

    def parse(self, raw_xml: bytes) -> ParsedXml:
        try:
            root = ET.fromstring(raw_xml)
        except (ET.ParseError, DefusedXmlException) as exc:
            raise NonDicomParseError("XML de submissão inválido") from exc
        if _tag(root) != "submission":
            raise NonDicomParseError("Raiz XML deve ser submission")
        document = next((element for element in list(root) if _tag(element) == "document"), None)
        if document is None:
            raise NonDicomParseError("Elemento document ausente")
        fields = _fields(document)
        _required(fields, "task_patient_id", "task_accession_number", "task_file_path", "task_file_name", "task_document_mimetype")
        submission = NonDicomSubmission(
            source_format="PHILIPS_SUBMISSION",
            patient_id=fields["task_patient_id"],
            accession_number=fields["task_accession_number"],
            file_name=fields["task_file_name"],
            file_path=fields["task_file_path"],
            mime_type=fields["task_document_mimetype"],
            modality=fields.get("task_modalities") or None,
            patient_name=_name(fields.get("task_patient_humanname_family"), fields.get("task_patient_humanname_given"), fields.get("task_patient_humanname_middle")),
            document_name=fields.get("task_document_name") or None,
            document_date=fields.get("task_document_date") or None,
            image_date=fields.get("task_image_date") or None,
            patient_birthday=fields.get("task_patient_birthday") or None,
            patient_gender=fields.get("task_patient_gender") or None,
            site_id=fields.get("task_site_id") or None,
            patient_issuer=fields.get("task_patient_issuer") or None,
            author_id=fields.get("task_author_id") or None,
            document_type=fields.get("task_document_type") or None,
            delete_file=_bool(fields.get("task_delete_file")),
            extra_fields={key: value for key, value in fields.items() if key not in self.known},
        )
        return ParsedXml(submission, raw_xml)


class PhilipsWttItemParser:
    """Parser independente de WTT_ITEM com documento PDF embedded em REPORT_BASE64."""

    known = {
        "SITE", "PATIENT_ID", "PATIENT_NAME", "PATIENT_BIRTHDATE", "PATIENT_SEX", "ACCESSION_NUMBER",
        "MODALITY", "REQUESTED_PROCEDURE_DESCRIPTION", "REQUESTED_PROCEDURE_ID", "DATE", "TIME",
        "REPORT_TYPE", "REPORT_BASE64",
    }

    def parse(self, raw_xml: bytes) -> ParsedXml:
        try:
            root = ET.fromstring(raw_xml)
        except (ET.ParseError, DefusedXmlException) as exc:
            raise NonDicomParseError("XML WTT_ITEM inválido") from exc
        if _tag(root) != "WTT_ITEM":
            raise NonDicomParseError("Raiz XML deve ser WTT_ITEM")
        fields = _fields(root)
        _required(fields, "PATIENT_ID", "ACCESSION_NUMBER", "REPORT_BASE64")
        try:
            base64.b64decode(fields["REPORT_BASE64"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise NonDicomParseError("REPORT_BASE64 inválido") from exc
        filename = f"report-{fields['ACCESSION_NUMBER']}.pdf"
        submission = NonDicomSubmission(
            source_format="PHILIPS_WTT_ITEM",
            patient_id=fields["PATIENT_ID"],
            accession_number=fields["ACCESSION_NUMBER"],
            file_name=filename,
            file_path=None,
            mime_type="application/pdf",
            modality=fields.get("MODALITY") or None,
            patient_name=fields.get("PATIENT_NAME") or None,
            document_name=fields.get("REQUESTED_PROCEDURE_DESCRIPTION") or fields.get("REPORT_TYPE") or None,
            document_date=fields.get("DATE") or None,
            image_date=fields.get("DATE") or None,
            patient_birthday=fields.get("PATIENT_BIRTHDATE") or None,
            patient_gender=fields.get("PATIENT_SEX") or None,
            site_id=fields.get("SITE") or None,
            document_type=fields.get("REPORT_TYPE") or None,
            report_base64=fields["REPORT_BASE64"],
            extra_fields={key: value for key, value in fields.items() if key not in self.known},
        )
        return ParsedXml(submission, raw_xml)


def parser_for(raw_xml: bytes):
    try:
        root = ET.fromstring(raw_xml)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise NonDicomParseError("XML inválido") from exc
    root_name = _tag(root)
    if root_name == "submission":
        return PhilipsSubmissionParser()
    if root_name == "WTT_ITEM":
        return PhilipsWttItemParser()
    raise NonDicomParseError("Formato XML Non-DICOM não suportado")


def parse_xml(raw_xml: bytes) -> ParsedXml:
    return parser_for(raw_xml).parse(raw_xml)


def safe_file_name(value: str) -> str:
    name = Path(value).name.replace("\x00", "").strip()
    if not name or name in {".", ".."}:
        raise NonDicomParseError("Nome de arquivo inválido")
    return "".join(char if char.isalnum() or char in {".", "-", "_"} else "_" for char in name)[:180]
