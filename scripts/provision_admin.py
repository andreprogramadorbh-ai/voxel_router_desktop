"""Provisiona a conta administrativa local sem senha padrão embutida."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.auth.service import AuthenticationService
from app.config.settings import Settings
from app.core.database import Database
from app.core.logging import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="Provisionamento local do VOXEL Router")
    parser.add_argument("--username", default="voxeladmin", help="Usuário administrativo (padrão: voxeladmin)")
    parser.add_argument("--non-interactive", action="store_true", help="Lê senha de VOXEL_ROUTER_BOOTSTRAP_PASSWORD")
    args = parser.parse_args()
    settings = Settings()
    configure_logging(settings.paths)
    database = Database(settings.paths)
    database.initialize()
    auth = AuthenticationService(database, int(settings.get("api", "session_minutes", default=30)))
    if auth.has_administrator():
        print("Administrador já provisionado; nenhuma alteração foi feita.", file=sys.stderr)
        return 2
    password = os.getenv("VOXEL_ROUTER_BOOTSTRAP_PASSWORD") if args.non_interactive else getpass.getpass("Defina a senha inicial do administrador: ")
    if not password:
        print("Senha ausente; provisionamento cancelado.", file=sys.stderr)
        return 2
    confirmation = password if args.non_interactive else getpass.getpass("Confirme a senha: ")
    if password != confirmation:
        print("As senhas não coincidem; provisionamento cancelado.", file=sys.stderr)
        return 2
    try:
        auth.provision_administrator(args.username, password)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    print("Administrador provisionado. A troca de senha será obrigatória no primeiro login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
