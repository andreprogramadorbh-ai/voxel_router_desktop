"""Ponto de entrada do executável VOXELRouter."""

from __future__ import annotations

import uvicorn

from app.api.server import create_app
from app.config.settings import Settings
from app.core.logging import configure_logging


def run() -> None:
    settings = Settings()
    configure_logging(settings.paths)
    uvicorn.run(
        create_app(start_engine=True),
        host=str(settings.get("api", "host", default="127.0.0.1")),
        port=int(settings.get("api", "port", default=8765)),
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    run()
