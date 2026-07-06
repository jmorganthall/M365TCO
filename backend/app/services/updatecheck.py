"""Update-available check: is the running build the latest published?

Best-effort and **fail-silent** — no network, a rate-limit, or a parse error just
yields "no update known", never an error. The result is cached (settings TTL) so
we never hammer GitHub. GHCR package-version listing needs auth even for public
images, so we use the repo's commits/releases — which the published image tags
mirror — as the robust proxy for "latest":

- a versioned deploy (built from a `v*` tag) compares against the latest release;
- a `:latest`/branch deploy compares its build sha against the default branch head.
"""

from __future__ import annotations

import re
import time

import httpx

from ..config import settings

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")
_GITHUB_API = "https://api.github.com"

# Simple module-level cache: {"at": epoch, "value": <payload dict>}.
_cache: dict = {"at": 0.0, "value": None}


def running() -> dict:
    """The running build's provenance (baked in at image publish time). `version`
    is exposed only when it is a real semver (a v* tag build); a branch/:latest
    build identifies by sha instead."""
    sha = settings.build_sha or ""
    raw_version = settings.build_version or ""
    version = raw_version if is_versioned(raw_version) else ""
    return {
        "sha": sha,
        "short_sha": sha[:7],
        "version": version,
        "ref": settings.build_ref or "",
        "known": bool(sha or version),
    }


def _semver_tuple(v: str):
    m = _SEMVER.match(v or "")
    return tuple(int(x) for x in m.groups()) if m else None


def is_versioned(version: str) -> bool:
    return _semver_tuple(version) is not None


def evaluate(
    running_sha: str,
    running_version: str,
    *,
    latest_release_tag: str | None = None,
    head_sha: str | None = None,
    default_branch: str = "",
    repo: str = "",
) -> dict | None:
    """Pure comparison → an `update` dict or None. Kept free of I/O so it is unit
    testable. A versioned running build compares against the latest release tag;
    otherwise the running sha is compared against the default-branch head sha."""
    if is_versioned(running_version):
        cur, new = _semver_tuple(running_version), _semver_tuple(latest_release_tag or "")
        if cur and new and new > cur:
            return {
                "available": True,
                "kind": "release",
                "latest": (latest_release_tag or "").lstrip("v"),
                "url": f"https://github.com/{repo}/releases/latest" if repo else None,
            }
        return None
    # Branch / :latest build — compare commit shas.
    if running_sha and head_sha and running_sha != head_sha \
            and not head_sha.startswith(running_sha) and not running_sha.startswith(head_sha):
        return {
            "available": True,
            "kind": "commit",
            "latest": head_sha[:7],
            "url": (f"https://github.com/{repo}/commits/{default_branch}"
                    if repo and default_branch else None),
        }
    return None


def _get(client: httpx.Client, path: str):
    resp = client.get(f"{_GITHUB_API}{path}", timeout=6)
    resp.raise_for_status()
    return resp.json()


def _fetch_update(run: dict) -> dict | None:
    """Do the network lookup for `run` and return an update dict or None. Any
    failure raises; the caller swallows it (fail-silent)."""
    repo = settings.update_repo
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "m365tco-updatecheck"}
    with httpx.Client(headers=headers) as client:
        if is_versioned(run["version"]):
            rel = _get(client, f"/repos/{repo}/releases/latest")
            return evaluate(run["sha"], run["version"],
                            latest_release_tag=rel.get("tag_name"), repo=repo)
        meta = _get(client, f"/repos/{repo}")
        branch = meta.get("default_branch", "")
        commit = _get(client, f"/repos/{repo}/commits/{branch}")
        return evaluate(run["sha"], run["version"],
                        head_sha=commit.get("sha", ""), default_branch=branch, repo=repo)


def check(force: bool = False) -> dict:
    """{"running": {...}, "update": {...}|null}. Cached; fail-silent on any error."""
    run = running()
    if not run["known"]:
        return {"running": run, "update": None}  # dev/local build — can't tell

    now = time.time()
    if not force and _cache["value"] is not None \
            and now - _cache["at"] < settings.update_check_ttl_seconds:
        return {"running": run, "update": _cache["value"]}

    try:
        update = _fetch_update(run)
    except Exception:
        update = None  # network down / rate-limited / parse error — stay silent
    _cache["at"] = now
    _cache["value"] = update
    return {"running": run, "update": update}
