"""Exhaustive permutation sweep over the engine, checked against invariants.

The reconciliation math is only as trustworthy as the relationships it survives.
Spot-checking one engagement at a time hides whole classes of error — an
over-credit only shows up when a specific tagging shape (many licenses to one
persona, one license to many personas, untagged either side) meets a specific
seat count. So instead of more examples, this enumerates the relationship space
and asserts properties that must hold for EVERY case:

  personas          1..3, distinct headcounts
  license lines     tagged to any subset of personas (including none = org-wide),
                    at seat counts below / at / above the tagged headcount,
                    per-user or tenant-wide, covering or not covering the outcome
  third-party tools tagged to any subset (including none), covered_count below /
                    at / above the population, managed or not
  scenarios         absent / in-scope displacing / in-scope non-displacing /
                    out-of-scope, per persona

`iter_cases()` yields hydrated Engagement objects; `check(case)` runs the engine
and returns the invariant violations. Both are importable so the pytest suite can
run a bounded sweep in CI (`test_engine_invariants.py`) and an operator can run
the full space from the command line:

    python -m tests.sweep            # full sweep + report
    python -m tests.sweep --level ci # the bounded set CI runs

An invariant here is a claim about REALITY, not about the current code: "credit
never exceeds the seats the customer actually holds" is true whatever the engine
does. When one fails, the engine is wrong until proven otherwise.
"""

from __future__ import annotations

import itertools
import random
import sys
from dataclasses import dataclass, replace
from decimal import Decimal

from tco_engine import (
    CoverageScope,
    CurrentLicenseLine,
    Engagement,
    Persona,
    PersonaScenario,
    ThirdPartyProduct,
    compute,
)

D = Decimal
OUT_A = "outcome-a"          # the outcome the tool duplicates
OUT_B = "outcome-b"          # a second outcome, for partial-overlap cases

CENT = D("0.01")


# --------------------------------------------------------------------------
# Case generation
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Case:
    """One generated engagement plus the label describing how it was built."""

    label: str
    engagement: Engagement


def _subsets(ids: tuple[str, ...]) -> list[tuple[str, ...]]:
    """Every persona-tag combination, including the empty (untagged/org-wide) one."""
    out: list[tuple[str, ...]] = []
    for r in range(len(ids) + 1):
        out.extend(itertools.combinations(ids, r))
    return out


def _personas(n: int) -> list[Persona]:
    # Distinct, coprime-ish headcounts so a pooling error can't hide behind a
    # coincidental equality (100 == 50 + 50 would mask a double-count).
    sizes = [100, 50, 25]
    return [
        Persona(id=f"p{i + 1}", name=f"P{i + 1}", headcount=sizes[i]) for i in range(n)
    ]


def _license(tags, qty, scope, covers, price=D("100")) -> CurrentLicenseLine:
    return CurrentLicenseLine(
        quantity_assigned=qty,
        unit_price_paid_annual=price,
        sku_reference="SKU",
        persona_ids=tags,
        coverage_scope=scope,
        covered_outcome_ids=frozenset(covers),
    )


def _tool(tags, covered_count, managed=False, cost=D("30000")) -> ThirdPartyProduct:
    return ThirdPartyProduct(
        id="tool",
        name="Tool",
        annual_cost=cost,
        covered_count=covered_count,
        is_managed=managed,
        tooling_pct=D("0.30"),
        delivered_outcome_ids=frozenset({OUT_A}),
        persona_ids=frozenset(tags),
    )


def _scenario(pid: str, kind: str) -> PersonaScenario | None:
    """kind: none | displacing | nondisplacing | outofscope."""
    if kind == "none":
        return None
    covers = frozenset({OUT_A}) if kind != "nondisplacing" else frozenset({OUT_B})
    return PersonaScenario(
        id=f"s-{pid}",
        persona_id=pid,
        target_sku_reference="TARGET",
        target_unit_price_annual=D("300"),
        target_covered_outcome_ids=covers,
        in_scope=(kind != "outofscope"),
    )


def iter_cases(level: str = "full"):
    """Yield Cases across the relationship space.

    level="ci"   — a bounded, deterministic slice (fast enough for every test run)
    level="full" — the whole enumerated space
    """
    person_counts = (2,) if level == "ci" else (1, 2, 3)
    scenario_kinds = (
        ("displacing", "none")
        if level == "ci"
        else ("none", "displacing", "nondisplacing", "outofscope")
    )
    scopes = (CoverageScope.PER_USER, CoverageScope.TENANT_WIDE)
    managed_flags = (False,) if level == "ci" else (False, True)

    for n in person_counts:
        people = _personas(n)
        pids = tuple(p.id for p in people)
        total_hc = sum(p.headcount for p in people)
        tag_sets = _subsets(pids)
        # Seat counts spanning every interesting relationship to the population:
        # none, a sliver, under, exactly the smallest persona, over the total.
        quantities = (0, 25, 50, 100, total_hc, total_hc + 50)
        # Tool populations below / at / above the personas it serves.
        tool_counts = (25, 100, total_hc, total_hc + 50)

        for lic_tags in tag_sets:
            for qty in quantities:
                for scope in scopes:
                    # A covering line and (in the full sweep) a non-covering one,
                    # so "covers nothing" can't be mistaken for "covers all".
                    cover_sets = ({OUT_A},) if level == "ci" else ({OUT_A}, {OUT_A, OUT_B}, set())
                    for covers in cover_sets:
                        lic = _license(lic_tags, qty, scope, covers)
                        for tool_tags in tag_sets:
                            for tool_count in tool_counts:
                                for managed in managed_flags:
                                    tool = _tool(tool_tags, tool_count, managed)
                                    for kinds in itertools.product(scenario_kinds, repeat=n):
                                        scenarios = [
                                            s for s in (
                                                _scenario(pid, k)
                                                for pid, k in zip(pids, kinds)
                                            ) if s is not None
                                        ]
                                        label = (
                                            f"n={n} lic_tags={lic_tags or '()'} qty={qty} "
                                            f"scope={scope.value} covers={sorted(covers) or '-'} "
                                            f"tool_tags={tool_tags or '()'} covered={tool_count} "
                                            f"managed={managed} scen={kinds}"
                                        )
                                        yield Case(label, Engagement(
                                            id="e",
                                            personas=list(people),
                                            current_licenses=[lic],
                                            third_party_products=[tool],
                                            scenarios=scenarios,
                                        ))


def iter_multi_line_cases(level: str = "full"):
    """Two license lines at once — the many-to-many shapes a single line can't
    express: two lines on one persona, one line shared by two personas plus an
    org-wide line, a tenant-wide line alongside a per-user one."""
    people = _personas(2)
    pids = tuple(p.id for p in people)
    tag_sets = _subsets(pids)
    quantities = (25, 50, 100) if level == "ci" else (0, 25, 50, 100, 150, 200)
    scopes = (CoverageScope.PER_USER, CoverageScope.TENANT_WIDE)
    tool_tag_sets = (pids,) if level == "ci" else tag_sets

    for tags_a, tags_b in itertools.product(tag_sets, repeat=2):
        for qty_a, qty_b in itertools.product(quantities, repeat=2):
            for scope_a, scope_b in itertools.product(scopes, repeat=2):
                lines = [
                    _license(tags_a, qty_a, scope_a, {OUT_A}),
                    _license(tags_b, qty_b, scope_b, {OUT_A}),
                ]
                for tool_tags in tool_tag_sets:
                    tool = _tool(tool_tags, 150)
                    label = (
                        f"2-line A(tags={tags_a or '()'},q={qty_a},{scope_a.value}) "
                        f"B(tags={tags_b or '()'},q={qty_b},{scope_b.value}) "
                        f"tool_tags={tool_tags or '()'}"
                    )
                    yield Case(label, Engagement(
                        id="e", personas=list(people), current_licenses=lines,
                        third_party_products=[tool],
                        scenarios=[_scenario(pid, "displacing") for pid in pids],
                    ))


# --------------------------------------------------------------------------
# Invariants — claims about reality the engine must satisfy
# --------------------------------------------------------------------------

def _tool_population(tool: ThirdPartyProduct, eng: Engagement) -> int:
    """Headcount that can possibly use the tool: its tagged personas, or everyone
    when untagged (org-wide)."""
    if tool.persona_ids:
        return sum(p.headcount for p in eng.personas if p.id in tool.persona_ids)
    return sum(p.headcount for p in eng.personas)


def _entitled_seats(line: CurrentLicenseLine, eng: Engagement) -> int:
    """Seats this line actually entitles: its assigned seats when per-user, or
    the headcount it applies to when tenant-wide. This is the ceiling on how many
    people it can make redundant — the customer cannot hold more coverage than
    they bought."""
    if line.coverage_scope is CoverageScope.TENANT_WIDE:
        if line.persona_ids:
            return sum(p.headcount for p in eng.personas if p.id in line.persona_ids)
        return sum(p.headcount for p in eng.personas)
    return line.quantity_assigned


def _covering_lines(tool: ThirdPartyProduct, eng: Engagement) -> list[CurrentLicenseLine]:
    return [
        l for l in eng.current_licenses
        if tool.delivered_outcome_ids and tool.delivered_outcome_ids <= l.covered_outcome_ids
    ]


def check(case: Case) -> list[str]:
    """Run the engine on a case and return every invariant it violates."""
    eng = case.engagement
    res = compute(eng)
    r = res.rollup
    bad: list[str] = []

    def fail(name: str, detail: str) -> None:
        bad.append(f"{name}: {detail}")

    tools = {t.id: t for t in eng.third_party_products}
    personas = {p.id: p for p in eng.personas}

    # ---- Spend bridge identities (Section 6.8 / 6.8a) ----
    bridge = r.target_microsoft_annual - r.existing_microsoft_annual - r.existing_third_party_annual
    if bridge != r.net_tco_delta_annual:
        fail("bridge", f"net {r.net_tco_delta_annual} != {bridge}")

    decomposed = r.move_incremental_delta_annual - r.freed_redundant_today_annual
    if decomposed != r.net_tco_delta_annual:
        fail("headline-decomposition",
             f"net {r.net_tco_delta_annual} != move {r.move_incremental_delta_annual} "
             f"- free-today {r.freed_redundant_today_annual}")

    in_scope = [s for s in res.scenarios if s.in_scope]
    delta_sum = sum((s.delta_annual for s in in_scope), D("0"))
    if abs(delta_sum - r.net_tco_delta_annual) > CENT * len(in_scope or [1]):
        fail("delta-sum", f"Σ scenario deltas {delta_sum} != net {r.net_tco_delta_annual}")

    # ---- Quick wins: seats must be real ----
    qw_total = sum((q.credited_annual for q in r.quick_wins), D("0"))
    if qw_total != r.quick_win_savings_annual:
        fail("quick-win-total", f"Σ {qw_total} != {r.quick_win_savings_annual}")

    for q in r.quick_wins:
        tool = tools[q.third_party_product_id]
        if not (0 <= q.displaced_today <= q.covered_count):
            fail("qw-seat-bounds",
                 f"displaced {q.displaced_today} outside [0, {q.covered_count}]")
        if q.residual_today != max(q.covered_count - q.displaced_today, 0):
            fail("qw-residual", f"residual {q.residual_today} != covered - displaced")
        pop = _tool_population(tool, eng)
        if q.displaced_today > pop:
            fail("qw-population",
                 f"displaced {q.displaced_today} > population using the tool {pop}")
        # THE bound the 46-seat over-credit broke: a covering line can only make
        # as many people redundant as it entitles.
        entitled = sum(_entitled_seats(l, eng) for l in _covering_lines(tool, eng))
        if q.displaced_today > entitled:
            fail("qw-entitlement",
                 f"displaced {q.displaced_today} > seats the covering licences "
                 f"entitle {entitled}")
        if q.credited_annual < 0 or q.credited_annual > tool.effective_annual_cost:
            fail("qw-credit-cap",
                 f"credited {q.credited_annual} outside [0, {tool.effective_annual_cost}]")

    # ---- Displacement credit (Sections 6.3 / 6.3a) ----
    for f in r.freed_third_party:
        tool = tools[f.third_party_product_id]
        if f.redundant_today_annual + f.move_unlocked_annual != f.credited_annual:
            fail("freed-split",
                 f"{f.redundant_today_annual} + {f.move_unlocked_annual} "
                 f"!= {f.credited_annual}")
        if f.redundant_today_annual < 0 or f.move_unlocked_annual < 0:
            fail("freed-split-sign", f"negative part in {f}")
        if f.credited_annual > tool.effective_annual_cost:
            fail("freed-cap",
                 f"credited {f.credited_annual} > tool cost {tool.effective_annual_cost}")
        qw = next((q for q in r.quick_wins if q.third_party_product_id == f.third_party_product_id), None)
        qw_credit = qw.credited_annual if qw else D("0")
        if f.redundant_today_annual > qw_credit:
            fail("freed-free-today",
                 f"'free today' {f.redundant_today_annual} > quick win {qw_credit}")

    freed_today = sum((f.redundant_today_annual for f in r.freed_third_party), D("0"))
    if freed_today != r.freed_redundant_today_annual:
        fail("freed-today-total", f"Σ {freed_today} != {r.freed_redundant_today_annual}")

    # Per-product offsets across scenarios never exceed what the tool costs or covers.
    for tool in eng.third_party_products:
        units = sum(
            (o.credited_units for s in in_scope for o in s.offsets
             if o.third_party_product_id == tool.id), D("0")
        )
        dollars = sum(
            (o.credited_offset_annual for s in in_scope for o in s.offsets
             if o.third_party_product_id == tool.id), D("0")
        )
        if units > tool.covered_count + CENT:
            fail("offset-units", f"{tool.id}: {units} units > covered {tool.covered_count}")
        if dollars > tool.effective_annual_cost + CENT:
            fail("offset-dollars",
                 f"{tool.id}: {dollars} > tool cost {tool.effective_annual_cost}")

    # ---- Dispositions ----
    for d in res.dispositions:
        tool = tools[d.third_party_product_id]
        displacing_hc = sum(
            personas[s.persona_id].headcount
            for s in eng.scenarios
            if s.in_scope and s.persona_id in personas
            and (not tool.persona_ids or s.persona_id in tool.persona_ids)
            and tool.delivered_outcome_ids
            and tool.delivered_outcome_ids <= s.target_covered_outcome_ids
        )
        if d.displaced_users != displacing_hc:
            fail("disposition-displaced",
                 f"{d.displaced_users} != displacing headcount {displacing_hc}")
        if d.residual_count < 0 or d.residual_annual_cost < 0:
            fail("disposition-sign", f"negative residual on {d.third_party_product_id}")

    # ---- Current Microsoft spend is distributed, never invented ----
    line_total = sum(
        (D(l.quantity_assigned) * l.unit_price_paid_annual for l in eng.current_licenses),
        D("0"),
    )
    attributed = sum((s.current_microsoft_annual for s in res.scenarios), D("0"))
    if attributed > line_total + CENT * (len(res.scenarios) or 1):
        fail("current-ms-conservation",
             f"attributed {attributed} > actual licence spend {line_total}")

    return bad


def check_order_independence(case: Case, seed: int = 7) -> list[str]:
    """Shuffling the input lists must not change a single number. Any dependence
    on list order means a seat is being allocated by position rather than by
    entitlement — which is how allocation bugs hide."""
    eng = case.engagement
    rng = random.Random(seed)
    shuffled = replace(
        eng,
        personas=rng.sample(eng.personas, len(eng.personas)),
        current_licenses=rng.sample(eng.current_licenses, len(eng.current_licenses)),
        third_party_products=rng.sample(
            eng.third_party_products, len(eng.third_party_products)
        ),
        scenarios=rng.sample(eng.scenarios, len(eng.scenarios)),
    )
    a, b = compute(eng), compute(shuffled)
    bad = []
    if a.rollup.net_tco_delta_annual != b.rollup.net_tco_delta_annual:
        bad.append(
            f"order-dependence: net {a.rollup.net_tco_delta_annual} vs "
            f"{b.rollup.net_tco_delta_annual}"
        )
    if a.rollup.quick_win_savings_annual != b.rollup.quick_win_savings_annual:
        bad.append(
            f"order-dependence: quick wins {a.rollup.quick_win_savings_annual} vs "
            f"{b.rollup.quick_win_savings_annual}"
        )
    qa = {q.third_party_product_id: q.displaced_today for q in a.rollup.quick_wins}
    qb = {q.third_party_product_id: q.displaced_today for q in b.rollup.quick_wins}
    if qa != qb:
        bad.append(f"order-dependence: displaced seats {qa} vs {qb}")
    return bad


def run(level: str = "full", check_order: bool = True) -> dict:
    """Run the sweep and return {invariant: [(label, detail), ...]} plus counts."""
    failures: dict[str, list[tuple[str, str]]] = {}
    total = 0
    for case in itertools.chain(iter_cases(level), iter_multi_line_cases(level)):
        total += 1
        problems = check(case)
        if check_order:
            problems += check_order_independence(case)
        for p in problems:
            name, _, detail = p.partition(": ")
            failures.setdefault(name, []).append((case.label, detail))
    return {"total": total, "failures": failures}


def main(argv: list[str]) -> int:
    level = "full"
    if "--level" in argv:
        level = argv[argv.index("--level") + 1]
    report = run(level)
    total, failures = report["total"], report["failures"]
    print(f"swept {total:,} engagements ({level})")
    if not failures:
        print("no invariant violations")
        return 0
    print(f"\n{sum(len(v) for v in failures.values()):,} violations "
          f"across {len(failures)} invariants:\n")
    for name, items in sorted(failures.items(), key=lambda kv: -len(kv[1])):
        print(f"  {name}: {len(items):,} cases")
        for label, detail in items[:3]:
            print(f"      {detail}")
            print(f"        ↳ {label}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
