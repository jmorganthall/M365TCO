"""Deployment-safety defaults: no CORS unless configured, and SQLite connections
that wait for the single writer instead of failing fast."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.db import _make_engine
from app.main import _cors_options


def test_cors_origins_default_is_empty(monkeypatch):
    monkeypatch.delenv("TCO_CORS_ORIGINS", raising=False)
    assert Settings(_env_file=None).cors_origins == ""
    assert _cors_options("") is None
    assert _cors_options(" , ") is None


def test_app_sends_no_cors_headers_by_default(client):
    origin = "https://elsewhere.example"
    r = client.get("/api/admin/ai/status", headers={"Origin": origin})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers
    assert "access-control-allow-credentials" not in r.headers

    preflight = client.options("/api/engagements", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in preflight.headers


def _mini_app(cors_origins):
    app = FastAPI()
    app.add_middleware(CORSMiddleware, **_cors_options(cors_origins))

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return TestClient(app)


def test_wildcard_cors_never_allows_credentials():
    assert _cors_options("*")["allow_credentials"] is False
    r = _mini_app("*").get("/ping", headers={"Origin": "https://elsewhere.example"})
    assert r.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in r.headers


def test_explicit_cors_origin_is_echoed():
    opts = _cors_options("http://localhost:5173, https://ui.example")
    assert opts["allow_origins"] == ["http://localhost:5173", "https://ui.example"]
    assert opts["allow_credentials"] is True
    client = _mini_app("http://localhost:5173, https://ui.example")
    r = client.get("/ping", headers={"Origin": "https://ui.example"})
    assert r.headers["access-control-allow-origin"] == "https://ui.example"
    r = client.get("/ping", headers={"Origin": "https://elsewhere.example"})
    assert "access-control-allow-origin" not in r.headers


def test_file_sqlite_engine_uses_wal_and_busy_timeout(tmp_path):
    eng = _make_engine(f"sqlite:///{tmp_path}/pragma.db")
    try:
        with eng.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 30000
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 0
    finally:
        eng.dispose()


def test_memory_sqlite_engine_skips_wal():
    eng = _make_engine("sqlite:///:memory:")
    try:
        with eng.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "memory"
            assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 30000
    finally:
        eng.dispose()
