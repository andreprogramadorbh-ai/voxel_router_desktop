"""Entrada de linha de comando para gerar orthanc.json sem segredos fixos."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.orthanc.configuration import configure_orthanc


def main() -> None:
    print(configure_orthanc())


if __name__ == "__main__":
    main()
