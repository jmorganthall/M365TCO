"""Update-available check: is the running build the latest published?

Best-effort and **fail-silent** — no network, a rate-limit, or a parse error just
yields "no update known", never an error. The result is cached (settings TTL) so
we never hammer GitHub. GHCR package-version listing needs auth even for public
images, so we use the repo's commits/releases — which the published image tags
mirror — as the robust proxy for "latest":

- a versioned deploy (built from a `v*` tag) compares against the latest release;
- a `:latest`/branch deploy asks GitHub whether the tracked trunk (`update_branch`,
  = `main`, the branch that publishes `:latest`) is strictly AHEAD of its build
  commit. We deliberately do NOT compare against the repo's *default branch* by
  sha-inequality: the default branch can be a stale side branch, so "different
  sha" would flag an OLDER commit as an update. Only "the trunk has commits this
  build doesn't" counts.
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
    target_sha: str | None = None,
    ahead_by: int = 0,
    target_branch: str = "",
    repo: str = "",
) -> dict | None:
    """Pure comparison → an `update` dict or None. Kept free of I/O so it is unit
    testable. A versioned running build compares against the latest release tag;
    otherwise an update exists only when the tracked trunk is strictly AHEAD of
    the running commit (`ahead_by > 0`) — a merely different or older sha is not
    an update."""
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
    # Branch / :latest build — update only when the trunk is ahead of this build.
    if running_sha and target_sha and ahead_by and ahead_by > 0:
        return {
            "available": True,
            "kind": "commit",
            "latest": target_sha[:7],
            "url": (f"https://github.com/{repo}/commits/{target_branch}"
                    if repo and target_branch else None),
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
        # Ask GitHub how the tracked trunk relates to the running commit. compare
        # BASE...HEAD reports `ahead_by` = commits HEAD (the branch) has beyond
        # BASE (this build); >0 means a real update, 0 means up to date or newer.
        branch = settings.update_branch or "main"
        cmp = _get(client, f"/repos/{repo}/compare/{run['sha']}...{branch}")
        ahead_by = int(cmp.get("ahead_by", 0) or 0)
        commits = cmp.get("commits") or []
        target_sha = commits[-1]["sha"] if ahead_by > 0 and commits else run["sha"]
        return evaluate(run["sha"], run["version"],
                        target_sha=target_sha, ahead_by=ahead_by,
                        target_branch=branch, repo=repo)


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
