"""Read-only before/after export of every engagement's numbers.

Before a change that could move the numbers (a data-model restructure, an
engine change, a catalog refresh), the operator captures a frozen "before" copy
of every engagement on their own instance; after the change they export again
and diff the two. Every changed number is listed with its JSON path, and each
engagement gets a cause hint (input / catalog / calculation change) so a moved
number can be traced to why it moved.

    python -m app.tools.baseline export --out before.json
    python -m app.tools.baseline diff before.json after.json --out report.md

The export is STRICTLY READ-ONLY. It never calls the service helpers that write
while reading (``compute_and_persist``, ``bundles.list_bundles``/``seed_bundles``,
``limits.evaluate``/``seed_license_limits``): engine inputs come from the pure
``services.compute.hydrate`` and outputs from the pure ``tco_engine.compute``.
On top of that, the database connection itself refuses writes:

* a dedicated engine (never the app's global engine/session);
* SQLite files are opened with ``mode=ro`` (the driver refuses any write);
  other databases start the transaction with ``SET TRANSACTION READ ONLY``;
* a ``before_cursor_execute`` guard raises on any write/DDL statement, and
  session guards raise on a flush with pending changes or on any commit;
* the transaction is always rolled back.

The whole export runs in one read transaction, so it is a consistent snapshot.
On SQLite that holds a shared lock for the (short) duration of the export: run
it while nobody is editing.

``diff`` needs only the Python standard library, so a report can be produced
anywhere the two JSON files are.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as _dt
import enum
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
from decimal import Decimal
from pathlib import Path

FORMAT_VERSION = 1

# backend/ (the container's working directory): app/tools/baseline.py -> parents[2].
BACKEND_DIR = Path(__file__).resolve().parents[2]

# Keys that identify an element of a list, in priority order. Lists of objects
# are sorted by (and diffed on) the first key every element carries.
_ID_KEYS = ("scenario_id", "third_party_product_id", "source_row_id", "id")

# Engagement fields recorded for identity. The horizon also drives the headline.
_IDENTITY_FIELDS = (
    "id", "customer_name", "created_at", "updated_at", "modeling_horizon_years", "currency",
)

# Catalog columns left out of the fingerprint: the import-time AI mapper's
# UNRATIFIED SKU -> bundle proposal, which never feeds any number.
_CATALOG_EXCLUDED_COLUMNS = {"microsoft_skus": frozenset({"suggested_bundle_id", "bundle_suggestion_reason"})}

NOTES = [
    "Decimals are plain strings with trailing zeros stripped, so equal numbers always "
    "serialize identically. Enums are their values; sets are sorted lists.",
    "Lists of objects are sorted by their id, except inputs.scenarios, which keeps the "
    "order the engine consumed: a displaced tool's shared credit is rounded to the cent "
    "cumulatively in scenario order.",
    "inputs.current_licenses[].source_row_id is an annotation (the current-licence row the "
    "line was hydrated from); the engine's own licence line carries no id.",
    "headline.readout_gui_html is the Readout tab / HTML readout figure (positive = saving); "
    "headline.readout_xlsx is the spreadsheet's N-month net TCO delta (negative = saving).",
    "outputs are the pure engine result. Opening a readout in the app can additionally "
    "clear a stale forced-elimination override on a tool the move fully displaces anyway; "
    "that clean-up never changes a number, and this read-only export does not perform it.",
]


# --------------------------------------------------------------------------
# Canonical serialization (standard library only)
# --------------------------------------------------------------------------

def canonical_json(obj) -> str:
    """Compact, key-sorted JSON: the form every hash and comparison uses."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def decimal_str(value: Decimal) -> str:
    """Plain (never scientific) notation with trailing zeros stripped, so equal
    numbers always serialize identically ("12.50" and "12.5" are one number)."""
    if not value.is_finite():
        return str(value)
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _sort_list(items: list) -> list:
    """Stable order for a list whose order carries no meaning: objects by their
    id key when every element has a unique one, anything else by its canonical
    JSON."""
    if items and all(isinstance(i, dict) for i in items):
        for key in _ID_KEYS:
            if all(key in i for i in items) and len({str(i[key]) for i in items}) == len(items):
                return sorted(items, key=lambda i: str(i[key]))
    return sorted(items, key=canonical_json)


def to_plain(value):
    """JSON-ready form of an engine dataclass tree (or any value in one)."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, enum.Enum):  # before str: the engine's enums are str-enums
        return to_plain(value.value)
    if isinstance(value, Decimal):
        return decimal_str(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return decimal_str(Decimal(repr(value)))
    if isinstance(value, str):
        return value
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return _sort_list([to_plain(v) for v in value])
    raise TypeError(f"cannot serialize a {type(value).__name__}")


# --------------------------------------------------------------------------
# Read-only database access
# --------------------------------------------------------------------------

class ReadOnlyViolation(RuntimeError):
    """Raised when anything tries to write through the baseline tool's session."""


_LEADING_NOISE = re.compile(r"^(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/|\()+", re.S)
_WRITE_VERBS = frozenset({
    "INSERT", "UPDATE", "DELETE", "REPLACE", "UPSERT", "MERGE",
    "CREATE", "DROP", "ALTER", "TRUNCATE", "RENAME",
    "ATTACH", "DETACH", "VACUUM", "REINDEX", "ANALYZE",
    "GRANT", "REVOKE", "COMMENT", "COPY", "LOCK", "CALL", "DO", "EXEC", "EXECUTE",
    "REFRESH", "CLUSTER", "IMPORT", "LOAD",
})
_DML_WORD = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|REPLACE)\b", re.I)


def check_statement(statement: str) -> None:
    """Raise ReadOnlyViolation for any statement that could write: a write/DDL
    verb, a PRAGMA assignment, or a WITH (CTE) statement carrying DML."""
    body = _LEADING_NOISE.sub("", statement or "", count=1)
    match = re.match(r"[A-Za-z]+", body)
    verb = match.group(0).upper() if match else ""
    if (
        verb in _WRITE_VERBS
        or (verb == "PRAGMA" and "=" in body)
        or (verb == "WITH" and _DML_WORD.search(body))
    ):
        raise ReadOnlyViolation(
            f"blocked a {verb} statement: the baseline tool is read-only"
        )


def _sqlite_file(url) -> str | None:
    """Filesystem path of a SQLite URL's database file, or None when the URL is
    not SQLite. Raises for an in-memory database (nothing to export)."""
    if url.get_backend_name() != "sqlite":
        return None
    database = url.database or ""
    if database.startswith("file:"):
        database = urllib.parse.unquote(database[len("file:"):].split("?", 1)[0])
    if database in ("", ":memory:"):
        raise ValueError("an in-memory SQLite database has nothing to export")
    return os.path.abspath(database)


def _sqlite_readonly_url(url):
    """The same SQLite database, opened through a URI filename with mode=ro so
    the driver itself refuses every write — and never creates a missing file."""
    from sqlalchemy.engine import URL

    path = _sqlite_file(url)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"SQLite database file not found: {path}")
    query = {k: v for k, v in url.query.items() if k not in ("mode", "uri")}
    query.update(mode="ro", uri="true")
    return URL.create(
        url.drivername, database="file:" + urllib.parse.quote(path), query=query
    )


def _configured_database_url() -> str:
    from ..config import settings

    return settings.database_url


@contextlib.contextmanager
def readonly_session(database_url: str | None = None):
    """A session on a DEDICATED, read-only engine; always rolled back."""
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    url = make_url(database_url or _configured_database_url())
    is_sqlite = url.get_backend_name() == "sqlite"
    engine = create_engine(_sqlite_readonly_url(url) if is_sqlite else url)

    @event.listens_for(engine, "before_cursor_execute")
    def _refuse_writes(conn, cursor, statement, parameters, context, executemany):
        check_statement(statement)

    if is_sqlite:
        # One explicit transaction for the whole export (the driver would
        # otherwise run each SELECT on its own), so every engagement and the
        # catalog fingerprint are read from the same snapshot.
        @event.listens_for(engine, "connect")
        def _driver_autocommit(dbapi_connection, connection_record):
            dbapi_connection.isolation_level = None

        @event.listens_for(engine, "begin")
        def _begin(conn):
            conn.exec_driver_sql("BEGIN")

    session = Session(bind=engine, autoflush=False, expire_on_commit=False)

    @event.listens_for(session, "before_flush")
    def _refuse_flush(sess, flush_context, instances):
        if sess.new or sess.dirty or sess.deleted:
            raise ReadOnlyViolation(
                "refused to flush pending changes: the baseline tool is read-only"
            )

    @event.listens_for(session, "before_commit")
    def _refuse_commit(sess):
        raise ReadOnlyViolation("the baseline tool never commits")

    try:
        if not is_sqlite:
            if url.get_backend_name() == "postgresql":
                session.execute(text(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                ))
            else:
                session.execute(text("SET TRANSACTION READ ONLY"))
        yield session
    finally:
        try:
            session.rollback()
        finally:
            session.close()
            engine.dispose()


def _caused_by_guard(exc: BaseException) -> bool:
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, ReadOnlyViolation):
            return True
        seen.add(id(exc))
        exc = getattr(exc, "orig", None) or exc.__cause__ or exc.__context__
    return False


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

def catalog_fingerprint(session) -> dict:
    """sha256 over a canonical, sorted serialization of the global tables that
    engagements depend on, plus the hard-coded bundle alias table. Surrogate
    uuid primary keys are left out and references to bundles / licence limits
    are written as their stable keys, so re-creating identical rows under new
    ids does not change the fingerprint. Per-table digests say WHICH table
    changed."""
    from sqlalchemy import select

    from .. import models
    from ..services import bundles as bundles_service

    ref_keys = {
        "bundles": dict(session.execute(
            select(models.Bundle.__table__.c.id, models.Bundle.__table__.c.key)
        ).all()),
        "license_limits": dict(session.execute(
            select(models.LicenseLimit.__table__.c.id, models.LicenseLimit.__table__.c.key)
        ).all()),
    }
    tables: dict[str, dict] = {}
    for model in (
        models.Bundle, models.AddonEligibility, models.BundleAlias, models.MicrosoftSku,
        models.DefaultOutcome, models.DefaultBundleCoverage,
        models.LicenseLimit, models.LicenseLimitMember,
    ):
        table = model.__table__
        excluded = _CATALOG_EXCLUDED_COLUMNS.get(table.name, frozenset())
        cols = [c for c in table.columns if not c.primary_key and c.name not in excluded]
        rows = []
        for record in session.execute(select(*cols)).mappings():
            row = {}
            for col in cols:
                value = record[col.name]
                fk = next(iter(col.foreign_keys), None)
                if fk is not None and value is not None:
                    target = fk.column.table.name
                    value = ref_keys.get(target, {}).get(value, f"<missing {target} row>")
                row[col.name] = to_plain(value)
            rows.append(row)
        rows.sort(key=canonical_json)
        body = {"columns": sorted(c.name for c in cols), "rows": rows}
        tables[table.name] = {"rows": len(rows), "sha256": _sha256(canonical_json(body))}

    return {
        "fingerprint": _sha256(canonical_json({n: t["sha256"] for n, t in tables.items()})),
        "tables": tables,
    }


def _inputs(hydrated, engagement_row) -> dict:
    """The hydrated engine input, serialized."""
    inputs = to_plain(hydrated)
    # Order-sensitive: kept exactly as the engine consumed it (see NOTES).
    inputs["scenarios"] = [to_plain(s) for s in hydrated.scenarios]
    # hydrate() builds one licence line per row of this same (already loaded)
    # relationship, in order — annotate each with the row it came from.
    lines = [to_plain(line) for line in hydrated.current_licenses]
    source_ids = [lic.id for lic in engagement_row.current_licenses]
    if len(source_ids) == len(lines):
        for source_id, line in zip(source_ids, lines):
            line["source_row_id"] = source_id
    inputs["current_licenses"] = sorted(
        lines, key=lambda line: (str(line.get("source_row_id", "")), canonical_json(line))
    )
    return inputs


def _headline(engagement_row, rollup) -> dict:
    """Both headline formulas the readouts present today, labelled, so a later
    change to either the numbers or the formula shows up in a diff."""
    horizon = int(engagement_row.modeling_horizon_years or 3)
    gui_annual = rollup.quick_win_savings_annual - rollup.move_incremental_delta_annual
    xlsx_annual = rollup.net_tco_delta_annual
    return {
        "horizon_years": horizon,
        "horizon_months": horizon * 12,
        "readout_gui_html": {
            "formula": "(rollup.quick_win_savings_annual - rollup.move_incremental_delta_annual)"
                       " * horizon_years",
            "sign": "positive = saving",
            "annual": decimal_str(gui_annual),
            "over_horizon": decimal_str(gui_annual * horizon),
        },
        "readout_xlsx": {
            "formula": "rollup.net_tco_delta_annual * horizon_years",
            "sign": "negative = saving",
            "annual": decimal_str(xlsx_annual),
            "over_horizon": decimal_str(xlsx_annual * horizon),
        },
        # The timed headline every readout now shows (ENGINE_SPEC 6.11).
        "timed": None if rollup.headline is None else {
            "formula": "rollup.headline (ENGINE_SPEC 6.11): the run rate, then the ramp,"
                       " each sub-line from its renewal",
            "sign": "positive = saving",
            "run_rate_annual": decimal_str(rollup.headline.run_rate_annual),
            "over_horizon": decimal_str(rollup.headline.amount),
            "duplicate_spend": decimal_str(rollup.headline.duplicate_spend_amount),
            "consolidation": decimal_str(rollup.headline.consolidation_amount),
            "overlicensing": decimal_str(rollup.headline.overlicensing_amount),
            "years": [decimal_str(y.amount) for y in rollup.headline.years],
            "full_run_rate_month": rollup.headline.full_run_rate_month,
        },
    }


def _engagement_record(session, engagement_row) -> dict:
    from tco_engine import compute as engine_compute

    from ..services.compute import hydrate  # pure read — never compute_and_persist

    record = {
        "id": engagement_row.id,
        "engagement": {
            name: to_plain(getattr(engagement_row, name, None)) for name in _IDENTITY_FIELDS
        },
    }
    try:
        hydrated = hydrate(session, engagement_row.id)
        inputs = _inputs(hydrated, engagement_row)
        result = engine_compute(hydrated)
        record["inputs"] = inputs
        record["outputs"] = to_plain(result)
        record["headline"] = _headline(engagement_row, result.rollup)
    except Exception as exc:
        if _caused_by_guard(exc):
            raise
        # One broken engagement must not block the baseline of all the others;
        # the error is recorded (and diffed) instead.
        for key in ("inputs", "outputs", "headline"):
            record.pop(key, None)
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


def _pyproject_version() -> str:
    try:
        import tomllib

        with open(BACKEND_DIR / "pyproject.toml", "rb") as fh:
            return str(tomllib.load(fh)["project"]["version"])
    except Exception:
        return "unknown"


def _git_head() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=BACKEND_DIR, capture_output=True,
            text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    sha = proc.stdout.strip()
    return sha if proc.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else ""


def _app_provenance() -> dict:
    from ..config import settings

    sha, source = (settings.build_sha or "").strip(), "image build (TCO_BUILD_SHA)"
    if not sha:
        sha, source = _git_head(), "git rev-parse HEAD"
    if not sha:
        sha, source = "unknown", "unavailable"
    return {
        "version": _pyproject_version(),
        "build_version": settings.build_version or "",
        "build_ref": settings.build_ref or "",
        "git_sha": sha,
        "git_sha_source": source,
    }


def build_export(database_url: str | None = None, *, now: _dt.datetime | None = None) -> dict:
    """Every engagement's identity, engine inputs, engine outputs and headline,
    read in one read-only transaction."""
    from sqlalchemy import select

    from .. import models

    with readonly_session(database_url) as session:
        dialect = session.get_bind().dialect.name
        catalog = catalog_fingerprint(session)
        ids = session.execute(
            select(models.Engagement.id).order_by(models.Engagement.id)
        ).scalars().all()
        records = []
        for engagement_id in ids:
            records.append(_engagement_record(session, session.get(models.Engagement, engagement_id)))
            session.expunge_all()  # bound memory; never writes
    stamp = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(_dt.timezone.utc)
    return {
        "format_version": FORMAT_VERSION,
        "exported_at": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "app": _app_provenance(),
        "database": {"dialect": dialect},
        "catalog": catalog,
        "engagement_count": len(records),
        "engagements": records,
        "notes": NOTES,
    }


def dumps_export(doc: dict) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def export(out: str, database_url: str | None = None) -> dict:
    """Build the export and write it to `out` ('-' = stdout). Refuses to write
    over the database file itself."""
    from sqlalchemy.engine import make_url

    database_url = database_url or _configured_database_url()
    if out != "-":
        db_file = _sqlite_file(make_url(database_url))
        if db_file and os.path.exists(out):
            for candidate in (db_file, db_file + "-journal", db_file + "-wal", db_file + "-shm"):
                if os.path.exists(candidate) and os.path.samefile(out, candidate):
                    raise ValueError("refusing to write the export over the database file")
    doc = build_export(database_url)
    text = dumps_export(doc)
    if out == "-":
        sys.stdout.write(text)
    else:
        Path(out).write_text(text, encoding="utf-8")
    return doc


# --------------------------------------------------------------------------
# Diff (standard library only)
# --------------------------------------------------------------------------

CAUSE_NONE = "no change"
CAUSE_INPUTS_ONLY = "inputs changed, numbers unchanged"
CAUSE_FIELDS_ONLY = "engagement fields changed, numbers unchanged"
CAUSE_INPUT = "input change"
CAUSE_INPUT_AND_CATALOG = "input change (catalog also changed)"
CAUSE_CATALOG = "catalog change (possibly also calculation)"
CAUSE_CALCULATION = "calculation change"
CAUSE_CALCULATION_UNKNOWN_BUILD = "calculation change? (git SHA unknown)"
CAUSE_UNEXPLAINED = "unexplained (same inputs, catalog and build)"
CAUSE_EXPORT_ERROR = "export error (not computable on one side)"

_LABEL_KEYS = ("persona_name", "third_party_product_name", "name", "sku_reference",
               "target_sku_reference")
_NUMERIC = re.compile(r"^-?\d+(?:\.\d+)?$")


def _number(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str) and _NUMERIC.match(value):
        return Decimal(value)
    return None


def _flatten(obj, path: str, label: str, leaves: dict, elements: dict) -> None:
    """Leaf path -> (value, item label). Lists of objects are addressed by their
    id (`scenarios[<id>]`), so a reordered or grown list diffs element by
    element; lists of plain values are compared whole."""
    if isinstance(obj, dict):
        for key in sorted(obj):
            _flatten(obj[key], f"{path}.{key}" if path else key, label, leaves, elements)
        return
    if isinstance(obj, list) and obj and all(isinstance(i, dict) for i in obj):
        id_key = next(
            (k for k in _ID_KEYS
             if all(k in i for i in obj) and len({str(i[k]) for i in obj}) == len(obj)),
            None,
        )
        for index, item in enumerate(obj):
            item_path = f"{path}[{item[id_key] if id_key else index}]"
            name = next((str(item[k]) for k in _LABEL_KEYS if item.get(k) not in (None, "")), "")
            item_label = " › ".join(p for p in (label, name) if p)
            elements[item_path] = item_label
            _flatten(item, item_path, item_label, leaves, elements)
        return
    leaves[path] = (obj, label)


def _topmost(paths: set) -> list:
    return sorted(
        p for p in paths
        if not any(q != p and (p.startswith(q + ".") or p.startswith(q + "[")) for q in paths)
    )


def compare(before, after) -> dict:
    """Leaf-level differences between two serialized trees."""
    b_leaves, b_elems, a_leaves, a_elems = {}, {}, {}, {}
    _flatten(before, "", "", b_leaves, b_elems)
    _flatten(after, "", "", a_leaves, a_elems)
    removed_items = _topmost(set(b_elems) - set(a_elems))
    added_items = _topmost(set(a_elems) - set(b_elems))
    moved = removed_items + added_items

    def under_moved_item(path: str) -> bool:
        return any(path.startswith(e + ".") or path.startswith(e + "[") for e in moved)

    numbers, values, fields_added, fields_removed = [], [], [], []
    for path in sorted(set(b_leaves) | set(a_leaves)):
        if under_moved_item(path):
            continue
        if path in b_leaves and path in a_leaves:
            (b, b_label), (a, a_label) = b_leaves[path], a_leaves[path]
            if b == a:
                continue
            nb, na = _number(b), _number(a)
            if nb is not None and na is not None:
                if nb != na:
                    numbers.append({"path": path, "item": a_label or b_label,
                                    "before": b, "after": a, "delta": decimal_str(na - nb)})
            else:
                values.append({"path": path, "item": a_label or b_label, "before": b, "after": a})
        elif path in b_leaves:
            value, item = b_leaves[path]
            # An emptied list of objects is reported through its removed items.
            if value == [] and any(e.startswith(path + "[") for e in a_elems):
                continue
            fields_removed.append({"path": path, "item": item, "before": value})
        else:
            value, item = a_leaves[path]
            if value == [] and any(e.startswith(path + "[") for e in b_elems):
                continue
            fields_added.append({"path": path, "item": item, "after": value})
    return {
        "numbers": numbers,
        "values": values,
        "items_added": [{"path": p, "item": a_elems[p]} for p in added_items],
        "items_removed": [{"path": p, "item": b_elems[p]} for p in removed_items],
        "fields_added": fields_added,
        "fields_removed": fields_removed,
    }


def _other_count(cmp: dict) -> int:
    return sum(len(cmp[k]) for k in
               ("values", "items_added", "items_removed", "fields_added", "fields_removed"))


def _name(record: dict) -> str:
    return (record.get("engagement") or {}).get("customer_name") or "(unnamed)"


def _gui_headline(record: dict):
    return ((record.get("headline") or {}).get("readout_gui_html") or {}).get("over_horizon")


def _xlsx_headline(record: dict):
    return ((record.get("headline") or {}).get("readout_xlsx") or {}).get("over_horizon")


def _numbers_view(record: dict) -> dict:
    return {"outputs": record.get("outputs") or {}, "headline": record.get("headline") or {},
            "error": record.get("error")}


def _inputs_view(record: dict) -> dict:
    return {"inputs": record.get("inputs") or {},
            "horizon_years": (record.get("engagement") or {}).get("modeling_horizon_years")}


def _cause(numbers_changed: bool, inputs_changed: bool, errored: bool,
           fields_changed: bool, env: dict) -> str:
    if errored and numbers_changed:
        return CAUSE_EXPORT_ERROR
    if not numbers_changed:
        if inputs_changed:
            return CAUSE_INPUTS_ONLY
        return CAUSE_FIELDS_ONLY if fields_changed else CAUSE_NONE
    if inputs_changed:
        return CAUSE_INPUT_AND_CATALOG if env["catalog_changed"] else CAUSE_INPUT
    if env["catalog_changed"]:
        return CAUSE_CATALOG
    if env["sha_changed"]:
        return CAUSE_CALCULATION
    if env["sha_unknown"]:
        return CAUSE_CALCULATION_UNKNOWN_BUILD
    return CAUSE_UNEXPLAINED


def _diff_engagement(before: dict, after: dict, env: dict) -> dict:
    errored = bool(before.get("error") or after.get("error"))
    if errored:
        # A side that could not be computed has no numbers to compare; report
        # the error itself rather than every number as "removed".
        numbers_cmp = compare({"error": before.get("error")}, {"error": after.get("error")})
    else:
        numbers_cmp = compare(_numbers_view(before), _numbers_view(after))
    numbers_changed = bool(numbers_cmp["numbers"]) or _other_count(numbers_cmp) > 0
    b_inputs, a_inputs = _inputs_view(before), _inputs_view(after)
    inputs_changed = not errored and canonical_json(b_inputs) != canonical_json(a_inputs)
    inputs_cmp = compare(b_inputs, a_inputs) if inputs_changed else None
    b_id, a_id = before.get("engagement") or {}, after.get("engagement") or {}
    identity_changes = [
        {"field": k, "before": b_id.get(k), "after": a_id.get(k)}
        for k in sorted(set(b_id) | set(a_id))
        if k != "modeling_horizon_years" and b_id.get(k) != a_id.get(k)
    ]
    return {
        "id": after.get("id"),
        "name": _name(after),
        "headline_before": _gui_headline(before),
        "headline_after": _gui_headline(after),
        "xlsx_before": _xlsx_headline(before),
        "xlsx_after": _xlsx_headline(after),
        "numbers": numbers_cmp,
        "changed_numbers": len(numbers_cmp["numbers"]),
        "other_changes": _other_count(numbers_cmp),
        "inputs_changed": inputs_changed,
        "inputs": inputs_cmp,
        "identity_changes": identity_changes,
        "errored": errored,
        "error_before": before.get("error"),
        "error_after": after.get("error"),
        "cause": _cause(numbers_changed, inputs_changed, errored, bool(identity_changes), env),
        "changed": numbers_changed or inputs_changed or bool(identity_changes),
    }


def _header(doc: dict) -> dict:
    return {
        "format_version": doc.get("format_version"),
        "exported_at": doc.get("exported_at"),
        "app_version": (doc.get("app") or {}).get("version"),
        "git_sha": (doc.get("app") or {}).get("git_sha") or "unknown",
        "dialect": (doc.get("database") or {}).get("dialect"),
        "catalog_fingerprint": (doc.get("catalog") or {}).get("fingerprint"),
        "engagement_count": len(doc.get("engagements") or []),
    }


def diff_exports(before: dict, after: dict) -> dict:
    """Structured before/after comparison of two exports."""
    b_head, a_head = _header(before), _header(after)
    b_tables = (before.get("catalog") or {}).get("tables") or {}
    a_tables = (after.get("catalog") or {}).get("tables") or {}
    tables_changed = [
        {"table": t, "before": b_tables.get(t), "after": a_tables.get(t)}
        for t in sorted(set(b_tables) | set(a_tables))
        if b_tables.get(t) != a_tables.get(t)
    ]
    shas = (b_head["git_sha"], a_head["git_sha"])
    sha_unknown = any(s in ("", "unknown") for s in shas)
    env = {
        "catalog_changed": b_head["catalog_fingerprint"] != a_head["catalog_fingerprint"],
        "sha_changed": not sha_unknown and shas[0] != shas[1],
        "sha_unknown": sha_unknown,
    }
    b_map = {r["id"]: r for r in before.get("engagements") or []}
    a_map = {r["id"]: r for r in after.get("engagements") or []}
    rows = [_diff_engagement(b_map[i], a_map[i], env) for i in sorted(set(b_map) & set(a_map))]
    rows.sort(key=lambda r: (r["name"].lower(), r["id"]))

    def brief(record):
        return {"id": record["id"], "name": _name(record), "headline": _gui_headline(record)}

    added = sorted((brief(a_map[i]) for i in set(a_map) - set(b_map)),
                   key=lambda r: (r["name"].lower(), r["id"]))
    removed = sorted((brief(b_map[i]) for i in set(b_map) - set(a_map)),
                     key=lambda r: (r["name"].lower(), r["id"]))
    return {
        "before": b_head,
        "after": a_head,
        "format_mismatch": b_head["format_version"] != a_head["format_version"],
        "catalog_changed": env["catalog_changed"],
        "catalog_tables_changed": tables_changed,
        "sha_changed": env["sha_changed"],
        "sha_unknown": env["sha_unknown"],
        "engagements": rows,
        "added": added,
        "removed": removed,
        "has_changes": bool(added or removed or any(r["changed"] for r in rows)),
    }


# ---- Markdown rendering ----

_UUID_TAIL = re.compile(r"\[([0-9a-fA-F]{8})-[0-9a-fA-F-]{27}\]")


def _cell(value) -> str:
    text = value if isinstance(value, str) else canonical_json(value)
    if len(text) > 160:
        text = text[:157] + "..."
    return text.replace("\n", " ").replace("|", "\\|").replace("<", "&lt;")


def _path(path: str) -> str:
    return "`" + _UUID_TAIL.sub(r"[\1]", path).replace("|", "\\|") + "`"


def _num_cell(value) -> str:
    number = _number(value)
    return format(number, ",f") if number is not None else _cell(value)


def _delta_cell(delta: str) -> str:
    number = Decimal(delta)
    return ("+" if number > 0 else "") + format(number, ",f")


def _money(value) -> str:
    number = _number(value)
    if number is None:
        return "—"
    return format(number.quantize(Decimal("0.01")), ",.2f")


def _short(engagement_id) -> str:
    return str(engagement_id or "")[:8]


def _yes(flag: bool) -> str:
    return "yes" if flag else "no"


def _render_changes(lines: list, cmp: dict, limit: int | None = None) -> None:
    def cap(rows):
        return rows if limit is None else rows[:limit]

    if cmp["numbers"]:
        lines += ["| Path | Item | Before | After | Delta |", "|---|---|---:|---:|---:|"]
        for n in cap(cmp["numbers"]):
            lines.append(f"| {_path(n['path'])} | {_cell(n['item'])} | {_num_cell(n['before'])} "
                         f"| {_num_cell(n['after'])} | {_delta_cell(n['delta'])} |")
        lines.append("")
    if cmp["values"]:
        lines += ["Other changed values:", "", "| Path | Item | Before | After |", "|---|---|---|---|"]
        for v in cap(cmp["values"]):
            lines.append(f"| {_path(v['path'])} | {_cell(v['item'])} | {_cell(v['before'])} "
                         f"| {_cell(v['after'])} |")
        lines.append("")
    for key, title in (("items_added", "Items added"), ("items_removed", "Items removed")):
        if cmp[key]:
            lines.append(f"{title}:")
            lines.append("")
            lines += [f"- {_path(i['path'])} {_cell(i['item'])}" for i in cap(cmp[key])]
            lines.append("")
    for key, title, side in (("fields_added", "Fields added", "after"),
                             ("fields_removed", "Fields removed", "before")):
        if cmp[key]:
            lines.append(f"{title}:")
            lines.append("")
            lines += [f"- {_path(f['path'])} = {_cell(f[side])}" for f in cap(cmp[key])]
            lines.append("")
    if limit is not None:
        total = len(cmp["numbers"]) + _other_count(cmp)
        shown = sum(min(len(cmp[k]), limit) for k in
                    ("numbers", "values", "items_added", "items_removed",
                     "fields_added", "fields_removed"))
        if total > shown:
            lines += [f"... and {total - shown} more.", ""]


def render_markdown(report: dict) -> str:
    b, a = report["before"], report["after"]
    lines = ["# Before/after comparison", ""]
    if report["format_mismatch"]:
        lines += [f"> **Warning:** the exports use different format versions "
                  f"({b['format_version']} vs {a['format_version']}); paths may not line up.", ""]
    lines += [
        "| | Before | After |",
        "|---|---|---|",
        f"| Exported (UTC) | {_cell(b['exported_at'])} | {_cell(a['exported_at'])} |",
        f"| App version | {_cell(b['app_version'])} | {_cell(a['app_version'])} |",
        f"| Git SHA | {_cell(b['git_sha'])} | {_cell(a['git_sha'])} |",
        f"| Database | {_cell(b['dialect'])} | {_cell(a['dialect'])} |",
        f"| Catalog fingerprint | {_cell((b['catalog_fingerprint'] or '')[:16])} "
        f"| {_cell((a['catalog_fingerprint'] or '')[:16])}"
        f"{' (changed)' if report['catalog_changed'] else ' (same)'} |",
        f"| Engagements | {b['engagement_count']} | {a['engagement_count']} |",
        "",
    ]
    rows = report["engagements"]
    changed = [r for r in rows if r["changed"]]
    lines += [
        f"**Result:** {len(changed)} of {len(rows)} engagement(s) changed; "
        f"{len(report['added'])} added; {len(report['removed'])} removed.",
        "",
    ]
    if report["catalog_tables_changed"]:
        names = ", ".join(t["table"] for t in report["catalog_tables_changed"])
        lines += [f"Catalog tables that changed: {names}.", ""]

    lines += [
        "## Summary",
        "",
        "Headline = the Readout tab / HTML readout total over the modeling horizon "
        "(positive = saving). The spreadsheet headline (net TCO delta × horizon, "
        "negative = saving) is in the details.",
        "",
        "| Engagement | Headline before → after | Changed numbers | Inputs changed | Cause hint |",
        "|---|---|---:|---|---|",
    ]
    for r in rows:
        other = f" (+{r['other_changes']} other)" if r["other_changes"] else ""
        lines.append(
            f"| {_cell(r['name'])} ({_short(r['id'])}) | {_money(r['headline_before'])} → "
            f"{_money(r['headline_after'])} | {r['changed_numbers']}{other} "
            f"| {'n/a' if r['errored'] else _yes(r['inputs_changed'])} | {r['cause']} |"
        )
    lines.append("")

    for key, title in (("added", "Added engagements"), ("removed", "Removed engagements")):
        if report[key]:
            lines += [f"## {title}", ""]
            lines += [f"- {_cell(e['name'])} ({e['id']}) — headline {_money(e['headline'])}"
                      for e in report[key]]
            lines.append("")

    if changed:
        lines += ["## Details", ""]
    for r in changed:
        lines += [f"### {_cell(r['name'])} ({r['id']})", ""]
        catalog = "different" if report["catalog_changed"] else "same"
        sha = ("different" if report["sha_changed"]
               else "unknown" if report["sha_unknown"] else "same")
        lines += [
            f"Cause hint: **{r['cause']}** — inputs "
            f"{'not comparable' if r['errored'] else 'changed' if r['inputs_changed'] else 'identical'}; "
            f"catalog fingerprint {catalog}; git SHA {sha}.",
            "",
        ]
        for side in ("before", "after"):
            if r[f"error_{side}"]:
                lines += [f"Export error ({side}): {_cell(r[f'error_{side}'])}", ""]
        lines += [
            "| Headline | Before | After |",
            "|---|---:|---:|",
            f"| Readout tab / HTML (positive = saving) | {_money(r['headline_before'])} "
            f"| {_money(r['headline_after'])} |",
            f"| Spreadsheet (negative = saving) | {_money(r['xlsx_before'])} "
            f"| {_money(r['xlsx_after'])} |",
            "",
        ]
        if r["changed_numbers"] or r["other_changes"]:
            lines += [f"Output changes: {r['changed_numbers']} number(s), "
                      f"{r['other_changes']} other.", ""]
            _render_changes(lines, r["numbers"])
        if r["identity_changes"]:
            lines += ["Engagement fields changed:", ""]
            lines += [f"- {c['field']}: {_cell(c['before'])} → {_cell(c['after'])}"
                      for c in r["identity_changes"]]
            lines.append("")
        if r["inputs_changed"]:
            cmp = r["inputs"]
            count = len(cmp["numbers"]) + _other_count(cmp)
            if count:
                lines += [f"Input changes ({count}):", ""]
                _render_changes(lines, cmp, limit=100)
            else:
                lines += ["Input changes: ordering only (the order the engine consumes "
                          "scenarios in changed).", ""]
    if not report["has_changes"]:
        lines += ["No changes: every engagement's inputs, outputs and headline are identical.", ""]
    return "\n".join(lines)


def _load(path: str) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or "engagements" not in doc or "format_version" not in doc:
        raise ValueError(f"{path} is not a baseline export")
    return doc


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.tools.baseline",
        description="Read-only before/after export of every engagement's numbers.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("export", help="write every engagement's inputs, outputs and headline")
    ex.add_argument("--out", required=True, help="output JSON file ('-' for stdout)")
    ex.add_argument("--database-url", default=None,
                    help="database to read (default: the app's configured TCO_DATABASE_URL)")
    df = sub.add_parser("diff", help="compare two exports and report every changed number")
    df.add_argument("before")
    df.add_argument("after")
    df.add_argument("--out", default=None, help="Markdown report file (default: stdout)")
    df.add_argument("--fail-on-change", action="store_true",
                    help="exit 1 when anything changed")
    args = parser.parse_args(argv)

    if args.command == "export":
        doc = export(args.out, database_url=args.database_url)
        errors = [r for r in doc["engagements"] if r.get("error")]
        where = "stdout" if args.out == "-" else args.out
        print(
            f"Exported {doc['engagement_count']} engagement(s) to {where}. "
            f"Catalog fingerprint {doc['catalog']['fingerprint'][:16]}, "
            f"git SHA {doc['app']['git_sha'][:12]}.",
            file=sys.stderr,
        )
        for r in errors:
            print(f"  engagement {r['id']} could not be computed: {r['error']}", file=sys.stderr)
        return 1 if errors else 0

    try:
        before, after = _load(args.before), _load(args.after)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = diff_exports(before, after)
    text = render_markdown(report)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    changed = sum(1 for r in report["engagements"] if r["changed"])
    print(
        f"{changed} engagement(s) changed, {len(report['added'])} added, "
        f"{len(report['removed'])} removed.",
        file=sys.stderr,
    )
    return 1 if args.fail_on_change and report["has_changes"] else 0


if __name__ == "__main__":
    sys.exit(main())
