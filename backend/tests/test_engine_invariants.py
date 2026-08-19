"""The engine's invariants, checked across the relationship space — not examples.

`tests/sweep.py` enumerates engagements over every persona↔licence↔tool tagging
shape (1:1, 1:many, many:1, many:many, untagged either side) at seat counts
below / at / above the population, and asserts properties that must hold for
EVERY case. This module runs the bounded slice on every test run; the full
1.2M-case space is one command away:

    cd backend && python -m tests.sweep

Two real over-credits were found by this sweep and fixed with it:
  * a per-user line tagged to several personas granted EACH of them its full
    `quantity_assigned` — 25 shared seats credited 50 seats of a duplicate tool;
  * an untagged tool was credited from any covering line's seat count regardless
    of who held it, so 200 seats could be credited against 150 people.

If one of these tests fails, the engine is asserting savings the customer cannot
realize. Fix the math, don't relax the invariant.
"""

import pytest

from tests import sweep


@pytest.fixture(scope="module")
def swept():
    return sweep.run(level="ci")


def test_sweep_covers_the_relationship_space(swept):
    """Guard the guard: a generator that silently stopped producing cases would
    make every invariant below pass vacuously."""
    assert swept["total"] > 3000


def test_no_invariant_violations_across_the_sweep(swept):
    failures = swept["failures"]
    if failures:
        lines = [f"{len(v)} cases violate {k}" for k, v in sorted(failures.items())]
        worst = next(iter(sorted(failures.items())))
        lines.append(f"first: {worst[1][0][1]}  ↳  {worst[1][0][0]}")
        pytest.fail("; ".join(lines))


@pytest.mark.parametrize("invariant", [
    "bridge",                    # net = target − existing MS − existing 3P
    "headline-decomposition",    # net = move value − free-today
    "quick-win-total",           # Σ credited = the headline
    "qw-seat-bounds",            # 0 ≤ displaced ≤ covered
    "qw-population",             # displaced ≤ the headcount that uses the tool
    "qw-entitlement",            # displaced ≤ seats the covering licences entitle
    "freed-split",               # free-today + move-unlocked = credited
    "freed-cap",                 # credit never exceeds what the tool costs
    "offset-units",              # allocated units never exceed the covered population
    "current-ms-conservation",   # attributed spend never exceeds actual spend
    "order-dependence",          # shuffling the inputs changes nothing
])
def test_named_invariant_holds(swept, invariant):
    """Name each invariant individually so a regression reports WHICH claim about
    reality broke, not just 'the sweep failed'."""
    hits = swept["failures"].get(invariant, [])
    assert not hits, f"{len(hits)} cases, e.g. {hits[0][1]} ↳ {hits[0][0]}"
