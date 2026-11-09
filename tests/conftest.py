"""Shared pytest fixtures.

Qt runs on the ``offscreen`` platform so the test-suite needs no display and
never opens windows on the developer's desktop.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import pytest

from src.app.bootstrap import bootstrap, shutdown
from src.app.config import AppConfig
from src.app.container import AppContainer
from src.app.paths import PROJECT_ROOT, AppPaths
from src.database import models  # noqa: F401
from src.database.base import Base
from src.database.session import Database
from src.networking.credentials import InMemoryCredentialStore


@pytest.fixture
def app_paths(tmp_path: Path) -> AppPaths:
    """Paths rooted in a temporary directory."""
    paths = AppPaths.from_data_dir(tmp_path / "data", root=PROJECT_ROOT)
    paths.ensure_directories()
    return paths


@pytest.fixture
def database() -> Iterator[Database]:
    """Empty in-memory database with the full schema."""
    db = Database.in_memory()
    Base.metadata.create_all(db.engine)
    yield db
    db.dispose()


@pytest.fixture
def container(app_paths: AppPaths) -> Iterator[AppContainer]:
    """Fully bootstrapped service container backed by temporary storage."""
    config = AppConfig(data_dir=app_paths.data_dir, target_port=18766)
    instance = bootstrap(config=config, paths=app_paths, credentials=InMemoryCredentialStore())
    yield instance
    shutdown(instance)


@pytest.fixture
def fake_google() -> Iterator[Any]:
    """Google-shaped pages on the loopback interface (see tests/helpers/fake_google_page.py).

    The search step of a journey browses this instead of google.com, so the whole
    suite runs offline: no key, no quota, and nothing asked of Google.
    """
    from tests.helpers.fake_google_page import FakeGooglePages

    server = FakeGooglePages()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def search_container(app_paths: AppPaths, fake_google: Any) -> Iterator[AppContainer]:
    """Application whose search step browses the local double instead of Google.

    This is the whole test seam: the origin a journey may open is a configuration
    value, so a journey runs against pages on this machine while every other rule -
    the allowlist, the fence, the redirect check - stays exactly as it ships.
    """
    from src.services.safety_service import SearchScope

    config = AppConfig(data_dir=app_paths.data_dir, target_port=18766)
    instance = bootstrap(
        config=config,
        paths=app_paths,
        credentials=InMemoryCredentialStore(),
        search_scope=SearchScope(**fake_google.scope()),
    )
    yield instance
    shutdown(instance)


@pytest.fixture(autouse=True)
def _qt_application(qapp: object) -> object:
    """Every test may create widgets; make sure the shared QApplication exists first."""
    return qapp
