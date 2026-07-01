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
from .routers import admin, catalog, engagements, entities
from .services import seeds


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


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


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "outcome_library_version": seeds.outcome_library_version(),
        "secret_store_enabled": __import__(
            "app.services.secrets", fromlist=["get_store"]
        ).get_store().enabled,
    }


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
