"""Raw uploaded price-sheet retention, so the operator can download the catalog
file they uploaded *as-is* (byte-for-byte), not a lossy re-export.

The CSV import parses the sheet into normalized/annualized `MicrosoftSku` rows
(latest-active-row per natural key, derived columns, dropped columns), so the
database can't reproduce the original file. We therefore keep the raw bytes of
the most recent upload on the persistent volume alongside a small pointer with
its original filename. The price-sync path already retains its fetched sheets
(`pricesync/storage.py`); this covers the manual-upload path.

Atomic write (temp + rename) so a failed write never corrupts the last good file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Optional

from ..config import settings

_SUBDIR = "uploaded_catalog"
_FILE = "catalog.csv"
_POINTER = "latest.json"


def _dir() -> str:
    return os.path.join(settings.data_dir, _SUBDIR)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_name(name: str) -> str:
    """A display/download filename with no path parts or control chars."""
    base = os.path.basename(name or "").strip()
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    if not base or base in (".", ".."):
        return "catalog.csv"
    return base if base.lower().endswith(".csv") else f"{base}.csv"


def store_upload(content: bytes, original_name: str, catalog_version: str = "") -> dict:
    """Persist the raw uploaded bytes as the current downloadable catalog file,
    remembering the original filename. Returns the stored metadata."""
    d = _dir()
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, _FILE)
    tmp = f"{dest}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, dest)
    meta = {
        "file_name": _safe_name(original_name),
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "catalog_version": catalog_version or "",
        "stored_at": _now_iso(),
    }
    ptmp = os.path.join(d, f"{_POINTER}.tmp")
    with open(ptmp, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(ptmp, os.path.join(d, _POINTER))
    return meta


def latest_upload() -> Optional[tuple[str, dict]]:
    """(absolute_path, metadata) for the stored uploaded file, or None when no
    upload has been retained (e.g. a catalog loaded before this feature existed,
    or an ephemeral disk that lost it)."""
    d = _dir()
    dest = os.path.join(d, _FILE)
    ptr = os.path.join(d, _POINTER)
    if not (os.path.exists(dest) and os.path.exists(ptr)):
        return None
    try:
        with open(ptr, encoding="utf-8") as fh:
            meta = json.load(fh)
    except (json.JSONDecodeError, OSError):
        meta = {"file_name": _FILE}
    return dest, meta
