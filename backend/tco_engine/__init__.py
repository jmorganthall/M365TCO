"""tco_engine — the pure M365 TCO reconciliation engine.

This package is the asset that must survive a platform change. It has NO
web, database, or framework imports. It takes a fully hydrated engagement
object (plain dataclasses) and returns computed scenarios and a rollup.

The algorithm is specified in PRD Section 6 and mirrored in docs/ENGINE_SPEC.md.
Any reimplementation (SharePoint, Power Platform, Dataverse) executes the same
algorithm against the same model and should reproduce these numbers exactly.
"""

from .models import (
    Coverage,
    Disposition,
    Override,
    ResidualIntent,
    CurrentLicenseLine,
    Persona,
    ThirdPartyProduct,
    PersonaScenario,
    Engagement,
)
from .engine import (
    compute,
    ScenarioResult,
    ProductDispositionResult,
    RollupResult,
    FreedThirdParty,
    EngineResult,
)
from .optimizer import (
    analyze_bundles,
    CandidateBundle,
    BundleAnalysis,
)

__all__ = [
    "Coverage",
    "Disposition",
    "Override",
    "ResidualIntent",
    "CurrentLicenseLine",
    "Persona",
    "ThirdPartyProduct",
    "PersonaScenario",
    "Engagement",
    "compute",
    "ScenarioResult",
    "ProductDispositionResult",
    "RollupResult",
    "FreedThirdParty",
    "EngineResult",
]
