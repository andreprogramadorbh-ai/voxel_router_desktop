"""Configuração persistente e diretórios seguros do VOXEL Router."""

from __future__ import annotations

import json
import os
import platform
import secrets
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"


@dataclass(frozen=True)
class AppPaths:
    """Localização de dados operacionais. Nenhum dado clínico fica em Program Files."""

    root: Path
    config: Path
    database: Path
    logs: Path
    storage: Path
    certificates: Path
    cache: Path
    backup: Path

    @classmethod
    def from_environment(cls) -> "AppPaths":
        override = os.getenv("VOXEL_ROUTER_DATA_DIR")
        if override:
            root = Path(override).expanduser()
        elif platform.system() == "Windows":
            root = Path(os.environ.get("PROGRAMDATA", r"C:\\ProgramData")) / "VOXEL" / "Router"
        else:
            root = Path.home() / ".voxel-router"
        return cls(
            root=root,
            config=root / "config",
            database=root / "database",
            logs=root / "logs",
            storage=root / "storage",
            certificates=root / "certificates",
            cache=root / "cache",
            backup=root / "backup",
        )

    @property
    def orthanc_root(self) -> Path:
        return self.root / "orthanc"

    @property
    def orthanc_storage(self) -> Path:
        return self.orthanc_root / "storage"

    @property
    def orthanc_database(self) -> Path:
        return self.orthanc_root / "database"

    def ensure(self) -> None:
        for path in (
            self.root,
            self.config,
            self.database,
            self.logs,
            self.storage,
            self.certificates,
            self.cache,
            self.backup,
            self.orthanc_root,
            self.orthanc_storage,
            self.orthanc_database,
        ):
            path.mkdir(parents=True, exist_ok=True)


class Settings:
    """Configuração JSON local, criada de modo atômico e validada por consumidores."""

    def __init__(self, paths: AppPaths | None = None) -> None:
        self.paths = paths or AppPaths.from_environment()
        self.paths.ensure()
        self.path = self.paths.config / "router.json"
        self._values = self._load()

    def _load_default(self) -> dict[str, Any]:
        with DEFAULT_CONFIG_PATH.open("r", encoding="utf-8") as source:
            return json.load(source)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            values = self._load_default()
            values["system"]["router_id"] = self._router_id()
            self._write(values)
            return values
        with self.path.open("r", encoding="utf-8") as source:
            values = json.load(source)
        if not values.get("system", {}).get("router_id"):
            values.setdefault("system", {})["router_id"] = self._router_id()
            self._write(values)
        return values

    @staticmethod
    def _router_id() -> str:
        return f"VR-{secrets.token_hex(4).upper()}"

    def _write(self, values: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as target:
            json.dump(values, target, ensure_ascii=False, indent=2, sort_keys=True)
            target.write("\n")
        os.replace(temporary, self.path)

    def as_dict(self) -> dict[str, Any]:
        return deepcopy(self._values)

    def section(self, name: str) -> dict[str, Any]:
        value = self._values.get(name, {})
        if not isinstance(value, dict):
            raise ValueError(f"Seção de configuração inválida: {name}")
        return deepcopy(value)

    def get(self, *path: str, default: Any = None) -> Any:
        current: Any = self._values
        for part in path:
            if not isinstance(current, dict):
                return default
            current = current.get(part)
            if current is None:
                return default
        return deepcopy(current)

    def update(self, section: str, values: dict[str, Any]) -> dict[str, Any]:
        if section not in self._values or not isinstance(self._values[section], dict):
            raise KeyError(f"Seção não configurável: {section}")
        self._values[section].update(values)
        self._write(self._values)
        return self.section(section)


def get_settings() -> Settings:
    return Settings()
