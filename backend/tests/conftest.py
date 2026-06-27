"""Test configuration: point the app at a throwaway SQLite DB + data dir BEFORE
any app module is imported, so the engine/table wiring binds to the temp DB."""

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="tco-tests-")
os.environ["TCO_DATABASE_URL"] = f"sqlite:///{_TMP}/tco.db"
os.environ["TCO_DATA_DIR"] = _TMP
os.environ["TCO_MASTER_SECRET"] = "test-secret"


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.db import init_db
    from app.main import app

    init_db()
    return TestClient(app)
