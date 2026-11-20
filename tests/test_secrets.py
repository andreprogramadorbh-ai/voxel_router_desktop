from __future__ import annotations

import platform
import sys
import types

import pytest

from app.security import secrets as secrets_module
from app.security.secrets import WindowsSecretStore

SECRET_VALUE = "valor-sintetico-de-teste"


def _fake_win32crypt() -> types.ModuleType:
    """Reproduz o contrato do pywin32: Protect -> bytes; Unprotect -> (descrição, dados)."""
    module = types.ModuleType("win32crypt")
    module.CRYPTPROTECT_LOCAL_MACHINE = 4
    module.calls = []

    def protect(data, description, entropy, reserved, prompt, flags):
        module.calls.append(("protect", description, flags))
        return b"FAKE-DPAPI:" + bytes(reversed(data))

    def unprotect(data, entropy, reserved, prompt, flags):
        module.calls.append(("unprotect", flags))
        assert data.startswith(b"FAKE-DPAPI:")
        return ("VOXEL Router", bytes(reversed(data[len(b"FAKE-DPAPI:"):])))

    module.CryptProtectData = protect
    module.CryptUnprotectData = unprotect
    return module


def test_secret_store_windows_path_with_simulated_dpapi(paths, monkeypatch):
    fake = _fake_win32crypt()
    monkeypatch.setitem(sys.modules, "win32crypt", fake)
    monkeypatch.setattr(secrets_module.platform, "system", lambda: "Windows")
    monkeypatch.delenv("VOXEL_ROUTER_DEV_SECRET_KEY", raising=False)

    store = WindowsSecretStore(paths)
    store.put("orthanc_password", SECRET_VALUE)

    raw = store.path.read_bytes()
    assert raw.startswith(b"FAKE-DPAPI:")
    assert SECRET_VALUE.encode() not in raw
    assert WindowsSecretStore(paths).get("orthanc_password") == SECRET_VALUE
    assert ("protect", "VOXEL Router", 4) in fake.calls


@pytest.mark.skipif(platform.system() != "Windows", reason="DPAPI real só existe no Windows")
def test_secret_store_real_dpapi_roundtrip(paths, monkeypatch):
    monkeypatch.delenv("VOXEL_ROUTER_DEV_SECRET_KEY", raising=False)

    store = WindowsSecretStore(paths)
    store.put("orthanc_password", SECRET_VALUE)

    assert SECRET_VALUE.encode() not in store.path.read_bytes()
    assert WindowsSecretStore(paths).get("orthanc_password") == SECRET_VALUE
    store.delete("orthanc_password")
    assert WindowsSecretStore(paths).get("orthanc_password") is None
