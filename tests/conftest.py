from __future__ import annotations

import pytest

from app.config.settings import AppPaths, Settings
from app.core.database import Database


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setenv("VOXEL_ROUTER_DATA_DIR", str(tmp_path / "router-data"))
    return AppPaths.from_environment()


@pytest.fixture
def settings(paths):
    return Settings(paths)


@pytest.fixture
def database(paths):
    instance = Database(paths)
    instance.initialize()
    return instance
