"""Sheet storage on the persistent volume (PRD §6.3 / §8).

Atomic writes (temp + rename) so a failed fetch never corrupts the last good
sheet. Metadata sidecar JSON per sheet, a latest.json pointer, SHA-256 of the
stored file, and retention of the newest RETENTION_COUNT sheets.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Optional

from .config import PRICE_SHEET_ENDPOINT, PriceSyncConfig

LATEST_POINTER = "latest.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write_text(path: str, text: str) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def sheet_filename(view: str, market: str, data_month: str) -> str:
    yyyymm = data_month.replace("-", "")
    return f"pricesheet_{view}_{market}_{yyyymm}.csv"


def commit_sheet(
    cfg: PriceSyncConfig,
    staged_csv_path: str,
    data_month: str,
    compressed_on_wire: bool,
    mfa_compliant: Optional[bool],
) -> dict:
    """Move a staged CSV into place atomically and write its metadata + latest.json.

    `staged_csv_path` is a fully-written temp file (already decompressed to CSV).
    Returns the metadata dict.
    """
    os.makedirs(cfg.data_dir, exist_ok=True)
    file_name = sheet_filename(cfg.pricesheet_view, cfg.market, data_month)
    dest = os.path.join(cfg.data_dir, file_name)

    # Atomic publish of the sheet itself.
    os.replace(staged_csv_path, dest)

    metadata = {
        "market": cfg.market,
        "pricesheet_view": cfg.pricesheet_view,
        "data_month": data_month,
        "fetched_at": _now_iso(),
        "source_endpoint": PRICE_SHEET_ENDPOINT.format(
            market=cfg.market, view=cfg.pricesheet_view
        ),
        "file_name": file_name,
        "file_bytes": os.path.getsize(dest),
        "sha256": sha256_file(dest),
        "compressed_on_wire": compressed_on_wire,
        "mfa_compliant": mfa_compliant,
    }

    sidecar = os.path.join(cfg.data_dir, f"{file_name}.json")
    _atomic_write_text(sidecar, json.dumps(metadata, indent=2))
    _atomic_write_text(
        os.path.join(cfg.data_dir, LATEST_POINTER), json.dumps(metadata, indent=2)
    )

    _enforce_retention(cfg)
    return metadata


def read_latest(cfg: PriceSyncConfig) -> Optional[dict]:
    path = os.path.join(cfg.data_dir, LATEST_POINTER)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def latest_csv_path(cfg: PriceSyncConfig) -> Optional[str]:
    meta = read_latest(cfg)
    if not meta:
        return None
    path = os.path.join(cfg.data_dir, meta["file_name"])
    return path if os.path.exists(path) else None


def _sheet_files(cfg: PriceSyncConfig) -> list[str]:
    if not os.path.isdir(cfg.data_dir):
        return []
    files = [
        f for f in os.listdir(cfg.data_dir)
        if f.startswith("pricesheet_") and f.endswith(".csv")
    ]
    # Newest first by mtime.
    files.sort(
        key=lambda f: os.path.getmtime(os.path.join(cfg.data_dir, f)), reverse=True
    )
    return files


def _enforce_retention(cfg: PriceSyncConfig) -> None:
    keep = max(cfg.retention_count, 1)
    for stale_file in _sheet_files(cfg)[keep:]:
        for p in (
            os.path.join(cfg.data_dir, stale_file),
            os.path.join(cfg.data_dir, f"{stale_file}.json"),
        ):
            try:
                os.remove(p)
            except OSError:
                pass
