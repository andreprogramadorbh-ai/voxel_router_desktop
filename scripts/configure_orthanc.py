"""Gera orthanc.json local sem credenciais fixas no repositório."""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.settings import Settings
from app.security.secrets import WindowsSecretStore


def main() -> None:
    settings = Settings()
    secret_store = WindowsSecretStore(settings.paths)
    username = "voxel-router-internal"
    password = secret_store.get("orthanc.internal.password")
    if password is None:
        password = secrets.token_urlsafe(32)
        secret_store.put("orthanc.internal.password", password)
    config = {
        "Name": "VOXEL Orthanc",
        "StorageDirectory": str(settings.paths.storage / "orthanc"),
        "IndexDirectory": str(settings.paths.database / "orthanc-index"),
        "DicomAet": str(settings.get("dicom", "ae_title", default="VOXEL_ROUTER")),
        "DicomPort": int(settings.get("dicom", "port", default=4242)),
        "HttpPort": 8042,
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
        "DicomTlsEnabled": bool(settings.get("dicom", "tls_enabled", default=False)),
        "LogLevel": "warning",
    }
    target = settings.paths.config / "orthanc.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    print(target)


if __name__ == "__main__":
    main()
