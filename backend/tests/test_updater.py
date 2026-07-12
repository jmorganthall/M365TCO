"""One-click update trigger: status gating + Watchtower HTTP-API call."""

import httpx

from app.config import settings
from app.services import secrets, updater


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


def _set_token(value):
    store = secrets.get_store()
    if value is None:
        store.delete(secrets.WATCHTOWER_API_TOKEN)
    else:
        store.set(secrets.WATCHTOWER_API_TOKEN, value)


def test_status_not_configured_without_url_or_token(monkeypatch):
    monkeypatch.setattr(settings, "watchtower_url", "")
    _set_token(None)
    st = updater.status()
    assert st == {"configured": False, "url_set": False, "token_set": False}

    monkeypatch.setattr(settings, "watchtower_url", "http://watchtower:8080")
    st = updater.status()
    assert st["url_set"] is True and st["token_set"] is False and st["configured"] is False


def test_trigger_reports_missing_config(monkeypatch):
    monkeypatch.setattr(settings, "watchtower_url", "")
    _set_token(None)
    assert updater.trigger()["ok"] is False  # no URL

    monkeypatch.setattr(settings, "watchtower_url", "http://watchtower:8080")
    r = updater.trigger()
    assert r["ok"] is False and "token" in r["detail"].lower()  # no token


def test_trigger_success_calls_watchtower(monkeypatch):
    monkeypatch.setattr(settings, "watchtower_url", "http://watchtower:8080/")  # trailing slash
    _set_token("s3cr3t")
    seen = {}

    def fake_post(url, headers=None, timeout=None):
        seen["url"] = url
        seen["auth"] = headers.get("Authorization")
        return _Resp(200)

    monkeypatch.setattr(updater.httpx, "post", fake_post)
    r = updater.trigger()
    assert r["ok"] is True
    assert seen["url"] == "http://watchtower:8080/v1/update"  # normalized, no double slash
    assert seen["auth"] == "Bearer s3cr3t"
    st = updater.status()
    assert st["configured"] is True


def test_trigger_reports_auth_and_transport_errors(monkeypatch):
    monkeypatch.setattr(settings, "watchtower_url", "http://watchtower:8080")
    _set_token("wrong")

    monkeypatch.setattr(updater.httpx, "post", lambda *a, **k: _Resp(401))
    assert updater.trigger()["ok"] is False and "401" in updater.trigger()["detail"]

    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(updater.httpx, "post", boom)
    r = updater.trigger()
    assert r["ok"] is False and "reach Watchtower" in r["detail"]
    _set_token(None)
