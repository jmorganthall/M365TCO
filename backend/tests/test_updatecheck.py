"""Update-available check — the pure comparison logic + the fail-silent endpoint.

The live GitHub lookup isn't exercised (no network in tests); we test the pure
`evaluate` core and that the endpoint stays silent on a dev/unversioned build.
"""

from app.services import updatecheck


def test_evaluate_release_newer_and_same():
    # Running v1.2.0, latest release v1.3.0 → update.
    up = updatecheck.evaluate("", "1.2.0", latest_release_tag="v1.3.0", repo="o/r")
    assert up and up["available"] and up["kind"] == "release" and up["latest"] == "1.3.0"
    assert up["url"] == "https://github.com/o/r/releases/latest"
    # Same version → no update.
    assert updatecheck.evaluate("", "1.3.0", latest_release_tag="v1.3.0", repo="o/r") is None
    # Older "latest" (shouldn't happen, but must not false-positive).
    assert updatecheck.evaluate("", "1.3.0", latest_release_tag="v1.2.9", repo="o/r") is None


def test_evaluate_commit_update_only_when_trunk_ahead():
    # Branch/:latest build (no semver): trunk ahead by 3 → update.
    up = updatecheck.evaluate("aaaaaaaaaaaa", "", target_sha="bbbbbbbbbbbb",
                              ahead_by=3, target_branch="main", repo="o/r")
    assert up and up["kind"] == "commit" and up["latest"] == "bbbbbbb"
    assert up["url"] == "https://github.com/o/r/commits/main"
    # Trunk not ahead (identical, or the build is newer/on a stale branch) → no
    # update, even though the compared sha differs. This is the stale-default-
    # branch false-positive the fix kills.
    assert updatecheck.evaluate("aaaaaaaaaaaa", "", target_sha="0ld0000",
                                ahead_by=0, target_branch="main", repo="o/r") is None


def test_evaluate_no_update_without_sha():
    # Missing running sha (dev build) → never a commit update.
    assert updatecheck.evaluate("", "", target_sha="bbbbbbb", ahead_by=5, repo="o/r") is None


def test_check_silent_on_dev_build(monkeypatch):
    # No baked build provenance → update check disabled, no network attempted.
    monkeypatch.setattr(updatecheck.settings, "build_sha", "")
    monkeypatch.setattr(updatecheck.settings, "build_version", "")
    result = updatecheck.check()
    assert result["running"]["known"] is False
    assert result["update"] is None


def test_version_endpoint_is_silent_on_dev(client, monkeypatch):
    monkeypatch.setattr(updatecheck.settings, "build_sha", "")
    monkeypatch.setattr(updatecheck.settings, "build_version", "")
    r = client.get("/api/version")
    assert r.status_code == 200
    assert r.json()["update"] is None and r.json()["running"]["known"] is False
