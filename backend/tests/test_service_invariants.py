"""Invariants for the two decision surfaces above the engine — the recommend-a-path
optimizer and the persona carve-out.

`tests/sweep_services.py` enumerates the decision space through the HTTP API and
asserts that each surface keeps its promises. This module runs the bounded slice
on every test run; the full space is one command:

    cd backend && python -m tests.sweep_services

Three defects were found by this sweep and fixed with it:
  * the optimizer credited `headcount × per-unit` for every displaceable tool, with
    no cap at the tool's covered population — recommending a bundle as saving
    $181,120 when applying it produced $91,120;
  * the optimizer credited tools tagged to OTHER personas to the one being analyzed;
  * the automatic Business Premium swap could be enabled, apply to nobody, move no
    number and explain nothing — it moved whole personas under a 300-seat cap, so
    any persona over 300 could never move. It is retired; the carve-out replaces it.

The carve-out invariants are conservation laws: carving seats out of a persona
changes the FUTURE state only. The same people exist, they hold the same licences
and tools, and today's spend splits between parent and child rather than moving.
Each one is mutation-tested — the guard has been shown to fail when the behaviour
it protects is removed.

If one of these fails, a user is being shown a number they cannot get.
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def swept(tmp_path_factory):
    """Run the sweep in its own process against its own database.

    It imports a pricing catalog, and the catalog is GLOBAL (not engagement-
    scoped) — sharing the session's test DB would silently reprice every other
    test's bundles. Isolation here is not tidiness; it is the difference between
    this suite testing the app and testing itself.
    """
    backend = pathlib.Path(__file__).resolve().parents[1]
    tmp = tmp_path_factory.mktemp("sweep-services")
    env = {
        **os.environ,
        "TCO_DATABASE_URL": f"sqlite:///{tmp}/tco.db",
        "TCO_DATA_DIR": str(tmp),
        "TCO_MASTER_SECRET": "sweep",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "tests.sweep_services", "--level", "ci", "--json"],
        cwd=backend, env=env, capture_output=True, text=True, timeout=900,
    )
    assert proc.returncode == 0, f"sweep failed to run: {proc.stderr[-2000:]}"
    return json.loads(proc.stdout)


def test_sweep_covers_the_decision_space(swept):
    """Guard the guard: an empty generator would make everything below pass.

    Set just under the current case count — this catches a generator that stopped
    producing, without failing every time the space is legitimately re-shaped."""
    assert swept["total"] >= 14


def test_no_invariant_violations_across_the_sweep(swept):
    failures = swept["failures"]
    if failures:
        worst = next(iter(sorted(failures.items())))
        pytest.fail(
            "; ".join(f"{len(v)} cases violate {k}" for k, v in sorted(failures.items()))
            + f" — first: {worst[1][0][1]}  ↳  {worst[1][0][0]}"
        )


@pytest.mark.parametrize("invariant", [
    "opt-offset-cap",            # never credit more than a tool costs
    "opt-offset-phantom",        # no credit without something displaced
    "opt-persona-attribution",   # only tools this persona holds
    "opt-engine-agreement",      # the recommendation's delta is what applying it yields
    "opt-recommend-eligible",    # never recommend a gapped/unpriced/capped bundle
    "opt-recommend-best",        # recommend the best eligible option
    "carve-accepted",                # a legitimate carve is not refused
    "carve-population-conserved",    # carving moves seats, it does not mint them
    "carve-seats-moved",             # the child gets exactly the seats asked for
    "carve-parent-reduced",          # ...and the parent loses exactly those seats
    "carve-lineage",                 # the child records what it was carved from
    "carve-current-spend-conserved", # today's total bill is untouched
    "carve-covers-conserved",        # third-party coverage is untouched
    "carve-inherits-associations",   # the child holds what those people already held
    "carve-spend-splits",            # ...so today's spend splits, it does not vanish
    "carve-has-scenario",            # the child has a future state, not just a name
    "carve-target-applied",          # on the target that was asked for
    "carve-undo-restores",           # deleting the child puts the seats back
    "carve-bounded",                 # carving the whole persona is refused
])
def test_named_invariant_holds(swept, invariant):
    hits = swept["failures"].get(invariant, [])
    assert not hits, f"{len(hits)} cases, e.g. {hits[0][1]} ↳ {hits[0][0]}"
