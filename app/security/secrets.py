"""Armazenamento de segredos local; DPAPI no Windows, apenas modo de desenvolvimento fora dele."""

from __future__ import annotations

import base64
import os
import platform
from cryptography.fernet import Fernet

from app.config.settings import AppPaths


class SecretStoreError(RuntimeError):
    pass


class WindowsSecretStore:
    """Protege segredos de cloud fora do banco de dados e do frontend."""

    def __init__(self, paths: AppPaths | None = None) -> None:
        self.paths = paths or AppPaths.from_environment()
        self.paths.ensure()
        self.path = self.paths.config / "secrets.dat"

    def put(self, name: str, value: str) -> None:
        if not name or not value:
            raise ValueError("Nome e valor do segredo são obrigatórios")
        data = self._load_all()
        data[name] = value
        self._save_all(data)

    def get(self, name: str) -> str | None:
        return self._load_all().get(name)

    def delete(self, name: str) -> None:
        data = self._load_all()
        data.pop(name, None)
        self._save_all(data)

    def _load_all(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        raw = self.path.read_bytes()
        try:
            decoded = self._unprotect(raw)
            import json
            value = json.loads(decoded.decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception as exc:
            raise SecretStoreError("Não foi possível abrir o armazenamento seguro local") from exc

    def _save_all(self, data: dict[str, str]) -> None:
        import json
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        protected = self._protect(payload)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(protected)
        os.replace(temporary, self.path)

    @staticmethod
    def _protect(payload: bytes) -> bytes:
        if platform.system() == "Windows":
            try:
                import win32crypt  # type: ignore[import-not-found]
                # Serviços Windows executam fora do perfil do usuário que iniciou o instalador.
                # O escopo de máquina permite a leitura pelo serviço; a ACL do instalador protege o arquivo.
                flags = getattr(win32crypt, "CRYPTPROTECT_LOCAL_MACHINE", 4)
                return win32crypt.CryptProtectData(payload, "VOXEL Router", None, None, None, flags)[1]
            except ImportError as exc:
                raise SecretStoreError("DPAPI indisponível; instale o componente de serviço Windows") from exc
        if os.getenv("VOXEL_ROUTER_DEV_SECRET_KEY"):
            key = os.environ["VOXEL_ROUTER_DEV_SECRET_KEY"].encode("ascii")
            return Fernet(key).encrypt(payload)
        raise SecretStoreError("Fora do Windows, defina VOXEL_ROUTER_DEV_SECRET_KEY apenas para desenvolvimento")

    @staticmethod
    def _unprotect(payload: bytes) -> bytes:
        if platform.system() == "Windows":
            try:
                import win32crypt  # type: ignore[import-not-found]
                return win32crypt.CryptUnprotectData(payload, None, None, None, 0)[1]
            except ImportError as exc:
                raise SecretStoreError("DPAPI indisponível; instale o componente de serviço Windows") from exc
        if os.getenv("VOXEL_ROUTER_DEV_SECRET_KEY"):
            key = os.environ["VOXEL_ROUTER_DEV_SECRET_KEY"].encode("ascii")
            return Fernet(key).decrypt(payload)
        raise SecretStoreError("Fora do Windows, defina VOXEL_ROUTER_DEV_SECRET_KEY apenas para desenvolvimento")


def create_development_key() -> str:
    """Gera chave Fernet para testes locais; não deve ser usada em instalações de produção."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
