"""The readout's Path to Full Run Rate bars share one zero line: a net-cost year
grows left of it and a saving year right, on one scale. Sizing by magnitude
alone drew a $96,854 cost year longer than a $78,535 saving year."""

import re

from app.services.readout import _Context


def _bars(html):
    return [(cls, float(left), float(width)) for cls, left, width in re.findall(
        r"class='bar (pos|neg)' style='left:([\d.-]+)%;width:([\d.-]+)%'", html)]


def _ramp(years, run_rate):
    h = {"horizon_months": 12 * len(years), "amount": sum(a for a, _ in years),
         "run_rate_annual": run_rate,
         "run_rate_horizon_amount": run_rate * len(years),
         "years": [{"year": i + 1, "amount": a, "cumulative": c} for i, (a, c) in enumerate(years)]}
    ctx = object.__new__(_Context)  # only the headline feeds the ramp
    ctx.h, ctx.months = h, h["horizon_months"]
    return ctx._ramp()


def test_cost_year_grows_left_of_zero_and_savings_right():
    html = _ramp([(-96854, -96854), (78535, -18320), (136312, 117993)], 160389)
    (c1, l1, w1), (c2, l2, w2), (c3, l3, w3), (cf, lf, wf) = _bars(html)
    zero = l2
    assert c1 == "neg" and l1 + w1 == zero               # ends at the zero line
    assert (c2, c3, cf) == ("pos", "pos", "pos") and l3 == lf == zero
    assert w1 > w2 and w3 > w1 and wf > w3                # one scale, by amount
    assert zero + wf <= 100.0001 and l1 >= 0
    assert "class='zero'" in html and "costs more than it saves" in html
    # The run rate over the whole period closes the table: 3 years × $160,389.
    assert "36 months × full run rate</th><td></td><td class='num'><b><span class='pos'>$481,167" in html


def test_all_savings_start_at_the_left_edge_with_no_zero_line():
    html = _ramp([(50000, 50000), (90000, 140000)], 100000)
    bars = _bars(html)
    assert all(c == "pos" and left == 0 for c, left, _ in bars)
    assert bars[-1][2] == 100
    assert "class='zero'" not in html and "costs more than it saves" not in html
