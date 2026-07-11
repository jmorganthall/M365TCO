"""FastAPI application assembly.

The presentation/integration layer. Wires the routers, initializes the database,
and serves the built React front end (when present) so the whole tool ships as a
single container image. The calculation engine (tco_engine) has no part here.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

from .config import settings
from .db import get_db, init_db
from .routers import admin, catalog, engagements, entities, pricesync
from .services import seeds


async def _daily_freshness_timer():
    """Local, no-auth, no-API daily age check that notifies once when Stale
    (PRD §6.4 FR-AGE-4 / §6.5 FR-UI-4). Disabled unless a webhook is configured."""
    import asyncio

    from .db import SessionLocal
    from .pricesync import config as ps_config, notify as ps_notify
    from .services import catalog_provenance

    while True:
        db = SessionLocal()
        try:
            cfg = ps_config.load_config(db)
            if cfg.notify_webhook_url:
                fr, _source = catalog_provenance.pricing_freshness(cfg, db)
                ps_notify.notify_if_stale(cfg, fr)
        except Exception:
            pass  # a monitoring loop must never crash the app
        finally:
            db.close()
        await asyncio.sleep(24 * 3600)


def _backfill_license_persona_tags(db) -> None:
    """One-time migration: seed the many-to-many persona tags from the deprecated
    single persona_id for any license that doesn't have tags yet. Idempotent."""
    from sqlalchemy import select

    from . import models

    rows = db.execute(
        select(models.CurrentMicrosoftLicense).where(
            models.CurrentMicrosoftLicense.persona_id.isnot(None)
        )
    ).scalars().all()
    changed = False
    for lic in rows:
        if lic.persona_id and not lic.persona_links:
            lic.persona_links.append(models.CurrentLicensePersona(persona_id=lic.persona_id))
            changed = True
    if changed:
        db.commit()


def _backfill_coverage_bundle_ids(db) -> None:
    """One-time migration: resolve existing Microsoft SKU coverage rows onto a
    Bundle id from their (shortcode) microsoft_sku_reference. Idempotent."""
    from sqlalchemy import select

    from . import models
    from .services import bundles as bundles_service

    rows = db.execute(
        select(models.CoverageMapEntry).where(
            models.CoverageMapEntry.product_kind == "MicrosoftSku",
            models.CoverageMapEntry.bundle_id.is_(None),
        )
    ).scalars().all()
    changed = False
    for r in rows:
        bid = bundles_service.resolve_bundle(db, r.microsoft_sku_reference or "")
        if bid:
            r.bundle_id = bid
            changed = True
    if changed:
        db.commit()


def _reconcile_catalog_provenance(db) -> None:
    """One-time migration: give a catalog that predates provenance recording a
    CatalogImport row derived from its own state, so freshness (the Readout badge
    and staleness banner) never reads "not set · stale" against a catalog that is
    demonstrably loaded. Idempotent. See services/catalog_provenance.py."""
    from .services import catalog_provenance

    catalog_provenance.reconcile_missing_provenance(db)


def _backfill_addon_eligibility(db) -> None:
    """One-time migration: seed the M:N add-on eligibility set from the legacy
    single `Bundle.base_bundle_id` for any add-on that has a primary base but no
    eligibility rows yet. Carries the old 1:1 base link forward as the enforceable
    set; à-la-carte add-ons (no base) intentionally stay without rows. Idempotent."""
    from sqlalchemy import select

    from . import models

    addons = db.execute(
        select(models.Bundle).where(
            models.Bundle.kind == "addon",
            models.Bundle.base_bundle_id.isnot(None),
        )
    ).scalars().all()
    have = {
        (e.addon_bundle_id, e.base_bundle_id)
        for e in db.execute(select(models.AddonEligibility)).scalars().all()
    }
    changed = False
    for a in addons:
        if not any(k[0] == a.id for k in have):  # no eligibility rows for this add-on
            if (a.id, a.base_bundle_id) not in have:
                db.add(models.AddonEligibility(
                    addon_bundle_id=a.id, base_bundle_id=a.base_bundle_id))
                have.add((a.id, a.base_bundle_id))
                changed = True
    if changed:
        db.commit()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    import asyncio

    init_db()
    # Seed the global default outcome library on first run.
    from .db import SessionLocal
    from .services import seeds as seeds_service

    from .services import ai_prompts as ai_prompts_service

    from .services import bundles as bundles_service

    db = SessionLocal()
    try:
        seeds_service.seed_default_outcomes(db)
        seeds_service.seed_default_coverage(db)
        ai_prompts_service.seed_defaults(db)
        bundles_service.seed_bundles(db)
        _backfill_addon_eligibility(db)
        _backfill_license_persona_tags(db)
        _backfill_coverage_bundle_ids(db)
        _backfill_binary_coverage(db)
        _backfill_new_default_outcomes(db)
        _reconcile_catalog_provenance(db)
    finally:
        db.close()

    timer = asyncio.create_task(_daily_freshness_timer())
    try:
        yield
    finally:
        timer.cancel()


app = FastAPI(title="M365 TCO Tool", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(engagements.router)
app.include_router(entities.router)
app.include_router(catalog.router)
app.include_router(admin.router)
app.include_router(pricesync.router)


def _backfill_binary_coverage(db) -> None:
    """One-time migration: coverage is now binary, so collapse any legacy 'Partial'
    coverage rows to the single 'Full' marker. Raw SQL so it tolerates the old enum
    values without the ORM validating them. Idempotent."""
    from sqlalchemy import text

    for table in ("coverage_map_entries", "default_bundle_coverage"):
        db.execute(text(f"UPDATE {table} SET coverage='Full' WHERE coverage<>'Full'"))
    db.commit()


def _backfill_new_default_outcomes(db) -> None:
    """Additive migration: insert any default outcome from the seed file whose key
    is missing (so existing deployments pick up newly-added outcomes like Desktop
    Software / Full-Size Cloud Storage), plus the default bundle coverage for those
    NEW outcomes only. Never touches existing rows, so operator edits/deletes to
    other outcomes and coverage are preserved. Idempotent."""
    from sqlalchemy import select

    from . import models
    from .services import seeds as seeds_service

    existing = {o.key for o in db.execute(select(models.DefaultOutcome)).scalars()}
    if not existing:
        return  # fresh DB — normal seeding handles it

    max_sort = max(
        (o.sort_order for o in db.execute(select(models.DefaultOutcome)).scalars()),
        default=0,
    )
    added: set[str] = set()
    for o in seeds_service.load_outcomes()["outcomes"]:
        if o["key"] not in existing:
            max_sort += 1
            db.add(models.DefaultOutcome(
                key=o["key"], name=o["name"],
                description=o.get("description", ""), sort_order=max_sort))
            added.add(o["key"])
    if not added:
        return
    db.flush()

    pairs = {
        (c.bundle_key, c.outcome_key)
        for c in db.execute(select(models.DefaultBundleCoverage)).scalars()
    }
    for item in seeds_service.load_coverage()["bundles"]:
        for entry in item["coverage"]:
            key = (item["bundle"], entry["outcome"])
            if entry["outcome"] in added and key not in pairs:
                db.add(models.DefaultBundleCoverage(
                    bundle_key=item["bundle"], outcome_key=entry["outcome"], coverage="Full"))
    db.commit()


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "outcome_library_version": seeds.outcome_library_version(),
        "secret_store_enabled": __import__(
            "app.services.secrets", fromlist=["get_store"]
        ).get_store().enabled,
    }


@app.get("/api/version")
def version(force: bool = False) -> dict:
    """Running build provenance + whether a newer image is published (best-effort,
    cached, fail-silent). `force=true` bypasses the cache."""
    from .services import updatecheck

    return updatecheck.check(force=force)


@app.get("/api/meta")
def meta(db=Depends(get_db)) -> dict:
    """Metadata the UI needs: enum values, defaults, seed versions."""
    from . import models as m
    from .services import defaults as defaults_service

    gd = defaults_service.get_defaults(db)
    return {
        "source_tags": list(m.SOURCE_TAGS),
        "price_basis": list(m.PRICE_BASIS),
        "cost_periods": list(m.COST_PERIODS),
        "unit_basis": list(m.UNIT_BASIS),
        "coverage": list(m.COVERAGE),
        "term_durations": list(m.TERM_DURATIONS),
        "default_tooling_pct": float(gd.default_tooling_pct),
        "default_market": settings.default_market,
        "default_currency": settings.default_currency,
        "outcome_library_version": seeds.outcome_library_version(),
    }


# ---- Serve the built front end (single-image deployment) ----
_FRONTEND_DIST = os.environ.get(
    "TCO_FRONTEND_DIST", os.path.join(os.path.dirname(__file__), "..", "static")
)

if os.path.isdir(_FRONTEND_DIST):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_FRONTEND_DIST, "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # API routes are matched first by FastAPI; anything else serves the SPA.
        index = os.path.join(_FRONTEND_DIST, "index.html")
        candidate = os.path.join(_FRONTEND_DIST, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(index)
