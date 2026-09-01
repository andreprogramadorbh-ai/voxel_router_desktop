"""Diretórios persistentes e resolução segura de arquivos Non-DICOM."""

from __future__ import annotations

import base64
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.non_dicom.models import NonDicomSubmission
from app.non_dicom.parsers import safe_file_name


class NonDicomStorageError(ValueError):
    """Arquivo não autorizado, ausente ou fora da política configurada."""


@dataclass(frozen=True)
class NonDicomPaths:
    root: Path
    input: Path
    processing: Path
    completed: Path
    failed: Path
    retry: Path
    files: Path
    logs: Path

    @classmethod
    def from_root(cls, root: str | Path) -> NonDicomPaths:
        current = Path(root).expanduser().resolve()
        return cls(current, current / "input", current / "processing", current / "completed", current / "failed", current / "retry", current / "files", current / "logs")

    def ensure(self) -> None:
        for directory in (self.root, self.input, self.processing, self.completed, self.failed, self.retry, self.files, self.logs):
            directory.mkdir(parents=True, exist_ok=True)


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


class NonDicomStorage:
    def __init__(self, paths: NonDicomPaths, input_mode: str, allowed_local_roots: list[str]) -> None:
        self.paths = paths
        self.input_mode = input_mode
        self.allowed_local_roots = [Path(item).expanduser().resolve() for item in allowed_local_roots if item]
        self.paths.ensure()

    def resolve_submission_file(self, submission: NonDicomSubmission) -> Path:
        if submission.source_format == "PHILIPS_WTT_ITEM":
            return self._write_embedded(submission)
        if not submission.file_path:
            raise NonDicomStorageError("Caminho de arquivo ausente")
        declared = Path(submission.file_path)
        if self.input_mode == "VOXEL_MANAGED_FILE":
            if ".." in declared.parts:
                raise NonDicomStorageError("Caminho gerenciado inválido")
            resolved = declared.resolve() if declared.is_absolute() else (self.paths.files / declared).resolve()
            if not is_within(resolved, self.paths.files):
                raise NonDicomStorageError("Path traversal bloqueado")
        elif self.input_mode == "LOCAL_PATH":
            if not declared.is_absolute():
                raise NonDicomStorageError("LOCAL_PATH exige caminho absoluto")
            resolved = declared.resolve()
            if not self.allowed_local_roots or not any(is_within(resolved, root) for root in self.allowed_local_roots):
                raise NonDicomStorageError("Caminho local fora das raízes autorizadas")
        else:
            raise NonDicomStorageError("Modo de arquivo Non-DICOM inválido")
        if not resolved.is_file():
            raise NonDicomStorageError("Arquivo referenciado não encontrado")
        return resolved

    def _write_embedded(self, submission: NonDicomSubmission) -> Path:
        if not submission.report_base64:
            raise NonDicomStorageError("REPORT_BASE64 ausente")
        directory = self.paths.files / str(uuid.uuid4())
        directory.mkdir(parents=True, exist_ok=False)
        target = directory / safe_file_name(submission.file_name)
        try:
            target.write_bytes(base64.b64decode(submission.report_base64, validate=True))
        except Exception as exc:
            shutil.rmtree(directory, ignore_errors=True)
            raise NonDicomStorageError("Não foi possível materializar REPORT_BASE64") from exc
        return target

    def move_xml(self, source: Path, destination: Path) -> Path:
        target = destination / source.name
        if target.exists():
            target = destination / f"{source.stem}-{uuid.uuid4().hex[:8]}{source.suffix}"
        source.replace(target)
        return target

    def move_to_processing(self, source: Path) -> Path:
        return self.move_xml(source, self.paths.processing)

    def move_to_completed(self, source: Path) -> Path:
        return self.move_xml(source, self.paths.completed)

    def move_to_failed(self, source: Path) -> Path:
        return self.move_xml(source, self.paths.failed)

    def move_to_retry(self, source: Path) -> Path:
        return self.move_xml(source, self.paths.retry)

    def delete_managed_file(self, file_path: Path) -> None:
        if is_within(file_path, self.paths.files):
            file_path.unlink(missing_ok=True)
