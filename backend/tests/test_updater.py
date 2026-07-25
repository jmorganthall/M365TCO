"""One-click update trigger: status gating + Watchtower HTTP-API call."""

import httpx

from app.config import settings
from app.services import secrets, updater


class _Resp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


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
    assert r["ok"] is True and r["no_op"] is False
    assert seen["url"] == "http://watchtower:8080/v1/update"  # normalized, no double slash
    assert seen["auth"] == "Bearer s3cr3t"
    st = updater.status()
    assert st["configured"] is True


def test_trigger_detects_watchtower_noop(monkeypatch):
    """Watchtower answers 200 even when it recreated nothing. The trigger flags
    that as a no-op (so the UI won't fake a restart) and surfaces its output."""
    monkeypatch.setattr(settings, "watchtower_url", "http://watchtower:8080")
    _set_token("s3cr3t")
    monkeypatch.setattr(
        updater.httpx, "post",
        lambda *a, **k: _Resp(200, text="Session done: Failed=0 Scanned=1 Updated=0"),
    )
    r = updater.trigger()
    assert r["ok"] is True and r["no_op"] is True
    assert "no image to update" in r["detail"].lower()
    assert "Updated=0" in r["detail"]          # Watchtower's own output is surfaced
    _set_token(None)


def test_trigger_success_surfaces_body_and_is_not_noop(monkeypatch):
    """A real update (Updated=1) is not a no-op and promises the restart."""
    monkeypatch.setattr(settings, "watchtower_url", "http://watchtower:8080")
    _set_token("s3cr3t")
    monkeypatch.setattr(
        updater.httpx, "post",
        lambda *a, **k: _Resp(200, text="Session done: Failed=0 Scanned=1 Updated=1"),
    )
    r = updater.trigger()
    assert r["ok"] is True and r["no_op"] is False
    assert "restart shortly" in r["detail"]
    _set_token(None)


def test_trigger_error_includes_watchtower_body(monkeypatch):
    """A 4xx surfaces Watchtower's response body so the cause isn't hidden."""
    monkeypatch.setattr(settings, "watchtower_url", "http://watchtower:8080")
    _set_token("s3cr3t")
    monkeypatch.setattr(
        updater.httpx, "post", lambda *a, **k: _Resp(500, text="internal boom"),
    )
    r = updater.trigger()
    assert r["ok"] is False and "500" in r["detail"] and "boom" in r["detail"]
    _set_token(None)


def test_token_from_env_fallback_and_store_precedence(monkeypatch):
    # No secret-store token, but an operational env token (WATCHTOWER_TOKEN) is set:
    # the update is considered configured and the env token is used.
    _set_token(None)
    monkeypatch.setattr(settings, "watchtower_url", "http://watchtower:8080")
    monkeypatch.setattr(settings, "watchtower_token", "env-token")
    assert updater.status() == {"configured": True, "url_set": True, "token_set": True}

    seen = {}

    def fake_post(url, headers=None, timeout=None):
        seen["auth"] = headers.get("Authorization")
        return _Resp(200)

    monkeypatch.setattr(updater.httpx, "post", fake_post)
    assert updater.trigger()["ok"] is True
    assert seen["auth"] == "Bearer env-token"

    # The encrypted secret store wins when both are present.
    _set_token("store-token")
    assert updater.trigger()["ok"] is True
    assert seen["auth"] == "Bearer store-token"

    _set_token(None)
    monkeypatch.setattr(settings, "watchtower_token", "")


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
