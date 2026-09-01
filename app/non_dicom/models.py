"""Modelos de domínio do módulo Non-DICOM, independentes do fluxo DICOM."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SourceFormat = Literal["PHILIPS_SUBMISSION", "PHILIPS_WTT_ITEM"]


@dataclass(frozen=True)
class NonDicomSubmission:
    source_format: SourceFormat
    patient_id: str
    accession_number: str
    file_name: str
    file_path: str | None
    mime_type: str
    modality: str | None = None
    patient_name: str | None = None
    document_name: str | None = None
    document_date: str | None = None
    image_date: str | None = None
    patient_birthday: str | None = None
    patient_gender: str | None = None
    site_id: str | None = None
    patient_issuer: str | None = None
    author_id: str | None = None
    document_type: str | None = None
    delete_file: bool = False
    report_base64: str | None = None
    extra_fields: dict[str, str] = field(default_factory=dict)

    @property
    def is_report(self) -> bool:
        return self.document_type == "11502-2"

    def metadata(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("report_base64", None)
        return data
