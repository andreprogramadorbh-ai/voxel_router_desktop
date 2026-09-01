"""Configuração persistente do processo Orthanc independente do VOXEL Router."""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from app.config.settings import Settings
from app.security.secrets import WindowsSecretStore


def configure_orthanc(settings: Settings | None = None) -> Path:
    """Criar ou atualizar orthanc.json com ports e storage próprios do Orthanc."""
    current = settings or Settings()
    secret_store = WindowsSecretStore(current.paths)
    username = "voxel-router-internal"
    password = secret_store.get("orthanc.internal.password")
    if password is None:
        password = secrets.token_urlsafe(32)
        secret_store.put("orthanc.internal.password", password)
    config = {
        "Name": "VOXEL Orthanc",
        "StorageDirectory": str(current.paths.orthanc_storage),
        "IndexDirectory": str(current.paths.orthanc_database),
        "DicomAet": str(current.get("orthanc", "ae_title", default="VOXEL_ORTHANC")),
        "DicomPort": int(current.get("orthanc", "dicom_port", default=4243)),
        "HttpPort": int(current.get("orthanc", "http_port", default=8042)),
        "HttpListenEnabled": True,
        "RemoteAccessAllowed": False,
        "AuthenticationEnabled": True,
        "RegisteredUsers": {username: password},
        "DicomCheckCalledAet": True,
        "DicomAlwaysAllowEcho": True,
        "DicomAlwaysAllowStore": False,
        "StrictAetComparison": True,
        "LimitFindResults": 100,
        "LimitFindInstances": 1000,
        "MaximumStorageSize": 0,
        "DicomTlsEnabled": bool(current.get("dicom", "tls_enabled", default=False)),
        "LogLevel": "warning",
    }
    target = current.paths.config / "orthanc.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target
