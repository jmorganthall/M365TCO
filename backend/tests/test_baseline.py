"""The read-only before/after export (app/tools/baseline.py).

Built against the suite's FILE-based SQLite DB with a realistic engagement made
through the API (personas, licences, third-party tools, coverage, scenarios with
an add-on, a residual classification), so export -> diff is exercised end to end.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from app.tools import baseline

BACKEND = Path(__file__).resolve().parents[1]


def _db_url() -> str:
    return os.environ["TCO_DATABASE_URL"]


def _db_file() -> Path:
    return Path(_db_url().split("sqlite:///", 1)[1])


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_engagement(client) -> dict:
    eid = client.post("/api/engagements", json={"customer_name": "Baseline Test Co"}).json()["id"]
    base = f"/api/engagements/{eid}"
    outcomes = {o["seed_key"]: o for o in client.get(f"{base}/outcomes").json()}
    kw = client.post(f"{base}/personas", json={"name": "Knowledge workers", "headcount": 400}).json()
    fl = client.post(f"{base}/personas", json={"name": "Frontline", "headcount": 150}).json()
    client.post(f"{base}/current-licenses", json={
        "sku_reference": "Office 365 E3", "quantity_purchased": 420, "quantity_assigned": 400,
        "unit_price_paid_annual": 276, "persona_ids": [kw["id"]]})
    client.post(f"{base}/current-licenses", json={
        "sku_reference": "Microsoft 365 F1", "quantity_assigned": 150,
        "unit_price_paid_annual": 27.6, "price_override": True, "overridden_price_annual": 24,
        "persona_ids": [fl["id"]]})
    sso = client.post(f"{base}/third-party", json={
        "name": "Legacy SSO", "raw_cost": 36000, "persona_ids": [kw["id"], fl["id"]],
        "renewal_date": "2027-01-31"}).json()
    edr = client.post(f"{base}/third-party", json={
        "name": "Endpoint EDR", "raw_cost": 2000, "cost_period": "Monthly", "is_managed": True,
        "tooling_pct": 0.4, "covered_count_override": 500, "persona_ids": [kw["id"]]}).json()
    for tool, outcome in ((sso, "identity-sso"), (edr, "endpoint-edr")):
        assert client.post(f"{base}/coverage", json={
            "outcome_id": outcomes[outcome]["id"], "product_kind": "ThirdParty",
            "third_party_product_id": tool["id"], "coverage": "Full", "ratified": True,
        }).status_code == 201
    addon = next(b for b in client.get("/api/catalog/bundles").json() if b["key"] == "e5-security")
    s_kw = client.post(f"{base}/scenarios", json={
        "persona_id": kw["id"], "target_sku_reference": "Microsoft 365 E3",
        "target_unit_price_annual": 432, "target_discount_pct": 0.1,
        "addons": [{"bundle_id": addon["id"], "unit_price_annual": 144}]}).json()
    s_fl = client.post(f"{base}/scenarios", json={
        "persona_id": fl["id"], "target_sku_reference": "Microsoft 365 F3",
        "target_unit_price_annual": 96}).json()
    # The operator viewed the readout once and classified a residual.
    assert client.post(f"{base}/compute").status_code == 200
    client.put(f"{base}/dispositions/{edr['id']}/override",
               json={"override": "None", "residual_intent": "IntendedOutOfScope"})
    return {"id": eid, "kw": kw, "fl": fl, "sso": sso, "edr": edr,
            "s_kw": s_kw, "s_fl": s_fl}


@pytest.fixture(scope="module")
def engagement(client):
    return _build_engagement(client)


def _record(doc: dict, eid: str) -> dict:
    return next(r for r in doc["engagements"] if r["id"] == eid)


def _export(tmp_path: Path, name: str) -> tuple[dict, str]:
    out = tmp_path / name
    doc = baseline.export(str(out), database_url=_db_url())
    return doc, out.read_text(encoding="utf-8")


def test_export_is_complete_and_deterministic(engagement, tmp_path):
    doc1, text1 = _export(tmp_path, "a.json")
    doc2, text2 = _export(tmp_path, "b.json")

    # Byte-identical apart from the timestamp.
    strip = re.compile(r'"exported_at": "[^"]*"')
    assert strip.sub("", text1) == strip.sub("", text2)

    assert doc1["format_version"] == baseline.FORMAT_VERSION
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", doc1["exported_at"])
    assert doc1["database"] == {"dialect": "sqlite"}  # the dialect only, never the URL
    assert str(_db_file()) not in text1 and "sqlite:" not in text1
    assert doc1["app"]["version"] and doc1["app"]["git_sha"]
    assert re.fullmatch(r"[0-9a-f]{64}", doc1["catalog"]["fingerprint"])
    assert {"bundles", "microsoft_skus", "default_outcomes", "default_bundle_coverage",
            "addon_eligibilities", "license_limits", "license_limit_members",
            "bundle_aliases"} <= set(doc1["catalog"]["tables"])
    ids = [r["id"] for r in doc1["engagements"]]
    assert ids == sorted(ids) and doc1["engagement_count"] == len(ids)

    rec = _record(doc1, engagement["id"])
    assert rec["engagement"]["customer_name"] == "Baseline Test Co"
    assert rec["engagement"]["modeling_horizon_years"] == 3
    assert {"created_at", "updated_at", "currency"} <= set(rec["engagement"])
    inputs, outputs = rec["inputs"], rec["outputs"]
    # Inputs exactly as hydrated: scenarios in engine order, licence lines
    # annotated with their source rows, ratified coverage as sorted id sets.
    assert [s["id"] for s in inputs["scenarios"]] == [engagement["s_kw"]["id"], engagement["s_fl"]["id"]]
    assert len(inputs["current_licenses"]) == 2
    assert all(line["source_row_id"] for line in inputs["current_licenses"])
    kw_scenario = inputs["scenarios"][0]
    assert kw_scenario["target_unit_price_annual"] == "518.4"  # (432 + 144) × 0.9
    assert kw_scenario["target_covered_outcome_ids"] == sorted(kw_scenario["target_covered_outcome_ids"])
    edr_in = next(p for p in inputs["third_party_products"] if p["id"] == engagement["edr"]["id"])
    assert edr_in["residual_intent"] == "IntendedOutOfScope" and edr_in["annual_cost"] == "24000"
    # Outputs: every engine field, Decimals as strings, lists sorted by id.
    assert set(outputs) == {"scenarios", "dispositions", "rollup"}
    assert [s["scenario_id"] for s in outputs["scenarios"]] == sorted(
        [engagement["s_kw"]["id"], engagement["s_fl"]["id"]])
    fl_out = next(s for s in outputs["scenarios"] if s["scenario_id"] == engagement["s_fl"]["id"])
    assert fl_out["target_spend_annual"] == "14400"  # 150 × 96
    assert "ecif_funding_high" in outputs["rollup"] and "quick_wins" in outputs["rollup"]
    # Both headline formulas, labelled.
    rollup = outputs["rollup"]
    head = rec["headline"]
    gui = (float(rollup["quick_win_savings_annual"]) - float(rollup["move_incremental_delta_annual"])) * 3
    assert float(head["readout_gui_html"]["over_horizon"]) == pytest.approx(gui)
    assert float(head["readout_xlsx"]["over_horizon"]) == pytest.approx(
        float(rollup["net_tco_delta_annual"]) * 3)
    assert "quick_win_savings_annual" in head["readout_gui_html"]["formula"]
    assert "net_tco_delta_annual" in head["readout_xlsx"]["formula"]


def test_export_leaves_the_database_untouched(engagement, tmp_path, monkeypatch):
    # The tool must never reach the helpers that write while reading.
    from app.services import bundles, compute, limits

    def _forbidden(*args, **kwargs):
        raise AssertionError("the baseline export called a writing helper")

    for module, name in ((compute, "compute_and_persist"), (bundles, "list_bundles"),
                         (bundles, "seed_bundles"), (limits, "evaluate"),
                         (limits, "seed_license_limits")):
        monkeypatch.setattr(module, name, _forbidden)

    db = _db_file()
    before_hash, before_files = _sha(db), sorted(os.listdir(db.parent))
    _export(tmp_path, "export.json")
    assert _sha(db) == before_hash
    assert sorted(os.listdir(db.parent)) == before_files  # no journal/WAL left behind


def test_guards_refuse_every_write(engagement):
    from app import models

    # ORM write -> the before_flush guard.
    with baseline.readonly_session(_db_url()) as session:
        session.add(models.Persona(engagement_id=engagement["id"], name="x", headcount=1))
        with pytest.raises(baseline.ReadOnlyViolation):
            session.flush()
    # Dirtying a loaded row -> the before_flush guard.
    with baseline.readonly_session(_db_url()) as session:
        persona = session.get(models.Persona, engagement["kw"]["id"])
        persona.headcount = 1
        with pytest.raises(baseline.ReadOnlyViolation):
            session.flush()
    # Raw SQL write -> the statement guard.
    with baseline.readonly_session(_db_url()) as session:
        for sql in ("UPDATE personas SET headcount = 0", "  /* c */ delete from personas",
                    "INSERT INTO personas (id) VALUES ('x')", "DROP TABLE personas",
                    "PRAGMA user_version = 7",
                    "WITH x AS (SELECT 1) DELETE FROM personas"):
            with pytest.raises(baseline.ReadOnlyViolation):
                session.execute(text(sql))
        assert session.execute(text("SELECT count(*) FROM personas")).scalar() > 0
    # Commit -> refused outright.
    with baseline.readonly_session(_db_url()) as session:
        with pytest.raises(baseline.ReadOnlyViolation):
            session.commit()
    # Even bypassing SQLAlchemy entirely, the connection itself is read-only.
    with baseline.readonly_session(_db_url()) as session:
        raw = session.connection().connection.driver_connection
        with pytest.raises(Exception, match="readonly"):
            raw.execute("UPDATE personas SET headcount = 0")


def test_export_refuses_to_overwrite_the_database(engagement, tmp_path):
    with pytest.raises(ValueError, match="database file"):
        baseline.export(str(_db_file()), database_url=_db_url())


def test_diff_identical_exports_reports_no_changes(engagement, tmp_path):
    doc1, _ = _export(tmp_path, "a.json")
    doc2, _ = _export(tmp_path, "b.json")
    report = baseline.diff_exports(doc1, doc2)
    assert not report["has_changes"]
    assert all(r["changed_numbers"] == 0 and r["cause"] == baseline.CAUSE_NONE
               for r in report["engagements"])
    md = baseline.render_markdown(report)
    assert "No changes" in md and "Baseline Test Co" in md
    assert baseline.main(["diff", str(tmp_path / "a.json"), str(tmp_path / "b.json"),
                          "--out", str(tmp_path / "r.md"), "--fail-on-change"]) == 0


def test_diff_traces_an_input_change(client, engagement, tmp_path):
    before, _ = _export(tmp_path, "before.json")
    scenario = engagement["s_fl"]["id"]
    try:
        assert client.patch(f"/api/engagements/{engagement['id']}/scenarios/{scenario}",
                            json={"target_unit_price_annual": 120}).status_code == 200
        after, _ = _export(tmp_path, "after.json")
    finally:
        client.patch(f"/api/engagements/{engagement['id']}/scenarios/{scenario}",
                     json={"target_unit_price_annual": 96})

    report = baseline.diff_exports(before, after)
    assert report["has_changes"] and not report["added"] and not report["removed"]
    changed = [r for r in report["engagements"] if r["changed"]]
    assert [r["id"] for r in changed] == [engagement["id"]]  # nothing else moved
    row = changed[0]
    assert row["inputs_changed"] and row["cause"] == baseline.CAUSE_INPUT
    by_path = {n["path"]: n for n in row["numbers"]["numbers"]}
    spend = by_path[f"outputs.scenarios[{scenario}].target_spend_annual"]
    assert (spend["before"], spend["after"], spend["delta"]) == ("14400", "18000", "3600")
    assert spend["item"] == "Frontline"
    assert "headline.readout_gui_html.over_horizon" in by_path
    assert "headline.readout_xlsx.over_horizon" in by_path
    assert any(n["path"] == f"inputs.scenarios[{scenario}].target_unit_price_annual"
               for n in row["inputs"]["numbers"])

    md = baseline.render_markdown(report)
    assert md.index("## Summary") < md.index("## Details")
    assert "input change" in md and "target_spend_annual" in md and "+3,600" in md
    assert baseline.main(["diff", str(tmp_path / "before.json"), str(tmp_path / "after.json"),
                          "--out", str(tmp_path / "r.md"), "--fail-on-change"]) == 1
    assert baseline.main(["diff", str(tmp_path / "before.json"), str(tmp_path / "after.json"),
                          "--out", str(tmp_path / "r.md")]) == 0


def _tamper_number(doc: dict, eid: str) -> None:
    rollup = _record(doc, eid)["outputs"]["rollup"]
    rollup["net_tco_delta_annual"] = baseline.decimal_str(
        baseline.Decimal(rollup["net_tco_delta_annual"]) + 1)


def test_diff_cause_hints(engagement, tmp_path):
    before, _ = _export(tmp_path, "before.json")
    eid = engagement["id"]

    # Only the build changed and no number moved: nothing to explain.
    sha_only = copy.deepcopy(before)
    sha_only["app"]["git_sha"] = "0" * 40
    report = baseline.diff_exports(before, sha_only)
    assert not report["has_changes"] and report["sha_changed"]

    # Same inputs, same catalog, different build, a number moved -> the calculation.
    calc = copy.deepcopy(sha_only)
    _tamper_number(calc, eid)
    row = next(r for r in baseline.diff_exports(before, calc)["engagements"] if r["id"] == eid)
    assert (row["cause"], row["changed_numbers"], row["inputs_changed"]) == (
        baseline.CAUSE_CALCULATION, 1, False)

    # Catalog fingerprint changed -> catalog (possibly also calculation).
    cat = copy.deepcopy(calc)
    cat["catalog"]["fingerprint"] = "f" * 64
    cat["catalog"]["tables"]["bundles"]["sha256"] = "f" * 64
    report = baseline.diff_exports(before, cat)
    row = next(r for r in report["engagements"] if r["id"] == eid)
    assert row["cause"] == baseline.CAUSE_CATALOG
    assert [t["table"] for t in report["catalog_tables_changed"]] == ["bundles"]

    # Nothing recorded explains it.
    same_build = copy.deepcopy(before)
    _tamper_number(same_build, eid)
    row = next(r for r in baseline.diff_exports(before, same_build)["engagements"] if r["id"] == eid)
    assert row["cause"] == baseline.CAUSE_UNEXPLAINED

    # A side that could not be computed is reported as such, not as lost numbers.
    broken = copy.deepcopy(before)
    rec = _record(broken, eid)
    for key in ("inputs", "outputs", "headline"):
        rec.pop(key)
    rec["error"] = "ValueError: example"
    row = next(r for r in baseline.diff_exports(before, broken)["engagements"] if r["id"] == eid)
    assert row["cause"] == baseline.CAUSE_EXPORT_ERROR and row["other_changes"] == 1

    # Added / removed engagements.
    fewer = copy.deepcopy(before)
    fewer["engagements"] = [r for r in fewer["engagements"] if r["id"] != eid]
    report = baseline.diff_exports(before, fewer)
    assert [e["id"] for e in report["removed"]] == [eid] and report["has_changes"]
    report = baseline.diff_exports(fewer, before)
    assert [e["id"] for e in report["added"]] == [eid]
    assert "Added engagements" in baseline.render_markdown(report)


def test_cli_runs_as_a_module(engagement, tmp_path):
    out = tmp_path / "cli.json"
    env = dict(os.environ, TCO_DATABASE_URL=_db_url())
    proc = subprocess.run([sys.executable, "-m", "app.tools.baseline", "export", "--out", str(out)],
                          cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert any(r["id"] == engagement["id"] for r in doc["engagements"])
    assert "Exported" in proc.stderr and _db_url() not in proc.stderr + proc.stdout
