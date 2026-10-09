"""The customer PDF — the walkthrough's leave-behind (docs/WALKTHROUGH.md §7).

Four parts: a title page; an overview led by the run rate, every group and its
recommendation, and the ramp (how the run rate is reached as contracts renew);
one section per group (current state, what's replaced,
future state, economic impact, capability changes); and "How we calculated
this", built from the same help-text file as the GUI tooltips.

It only displays what the engine and the capability comparison computed — it
never calculates a number. Anything left out (a tool with no cost, users or
uses; unanswered questions) does not appear. Rendered with reportlab, which is
pure Python, so the container needs no system packages.
"""

from __future__ import annotations

import base64
import io
import re
from datetime import date
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .. import models
from . import help_text

INK = colors.HexColor("#1a1f3c")
MUTED = colors.HexColor("#5b6280")
POS = colors.HexColor("#127436")
RULE = colors.HexColor("#d5d9ea")
BAND = colors.HexColor("#f2f4fb")


def _styles(primary):
    ss = getSampleStyleSheet()
    base = ParagraphStyle("base", parent=ss["Normal"], fontName="Helvetica", fontSize=9.5,
                          leading=13, textColor=INK, alignment=TA_LEFT)
    return {
        "base": base,
        "muted": ParagraphStyle("muted", parent=base, textColor=MUTED, fontSize=8.5, leading=11),
        "small": ParagraphStyle("small", parent=base, fontSize=8.5, leading=11),
        "h1": ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=20,
                             leading=24, textColor=primary, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=13,
                             leading=17, textColor=primary, spaceBefore=10, spaceAfter=4),
        "h3": ParagraphStyle("h3", parent=base, fontName="Helvetica-Bold", fontSize=10.5,
                             leading=14, spaceBefore=8, spaceAfter=2),
        "title": ParagraphStyle("title", parent=base, fontName="Helvetica-Bold", fontSize=30,
                                leading=36, textColor=primary),
        "subtitle": ParagraphStyle("subtitle", parent=base, fontSize=14, leading=19,
                                   textColor=MUTED),
        "headline": ParagraphStyle("headline", parent=base, fontName="Helvetica-Bold",
                                   fontSize=24, leading=29),
    }


def _usd0(v) -> str:
    return f"${abs(float(v or 0)):,.0f}"


def _amount(v) -> str:
    """Savings-positive figure in finance notation: a saving plain, a cost in
    parentheses — never a minus sign next to the word savings."""
    v = float(v or 0)
    return _usd0(v) if v >= 0 else f"({_usd0(v)})"


def _date(iso) -> str:
    if not iso:
        return "—"
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%d %b %Y").lstrip("0")
    except ValueError:
        return str(iso)


def _color(value: str, fallback):
    return colors.HexColor(value) if re.fullmatch(r"#[0-9a-fA-F]{6}", value or "") else fallback


def _logo(data_url: str):
    """The customer's logo, when it is a PNG or JPEG data URL (SVG is skipped —
    reportlab can't draw it without extra packages)."""
    m = re.match(r"^data:image/(png|jpe?g);base64,(.+)$", data_url or "", re.S)
    if not m:
        return None
    try:
        img = Image(io.BytesIO(base64.b64decode(m.group(2))))
    except Exception:  # an unreadable image is simply left off
        return None
    w, h = img.imageWidth, img.imageHeight
    scale = min(2.2 * inch / max(w, 1), 0.9 * inch / max(h, 1))
    img.drawWidth, img.drawHeight = w * scale, h * scale
    img.hAlign = "LEFT"
    return img


def _ramp(years: list[dict], run_rate: float, width: float):
    """The ramp as bars: one per modelled year, then the full run rate, each with
    its figure. A saving is drawn green to the right of the axis, a cost grey to
    the left, so a ramp that starts as an investment reads as one."""
    rows = [(f"Year {y['year']}", float(y["amount"]), float(y["cumulative"])) for y in years]
    rows.append(("Full run rate", float(run_rate), None))
    peak = max([abs(v) for _, v, _ in rows] + [1.0])
    label_w, value_w, row_h = 1.0 * inch, 1.9 * inch, 18
    track = width - label_w - value_w
    has_cost = any(v < 0 for _, v, _ in rows)
    axis = label_w + (track / 2 if has_cost else 0)
    span = track / 2 if has_cost else track
    d = Drawing(width, row_h * len(rows) + 4)
    for n, (label, v, cum) in enumerate(rows):
        y = row_h * (len(rows) - 1 - n) + 4
        d.add(String(0, y + 4, label, fontName="Helvetica-Bold", fontSize=8.5, fillColor=INK))
        w = abs(v) / peak * span
        d.add(Rect(axis if v >= 0 else axis - w, y + 2, w, row_h - 8, strokeColor=None,
                   fillColor=POS if v >= 0 else MUTED))
        text = _amount(v) + (f"  · running total {_amount(cum)}" if cum is not None else "")
        d.add(String(width, y + 4, text, fontName="Helvetica", fontSize=8.5,
                     fillColor=INK, textAnchor="end"))
    d.add(Line(axis, 2, axis, row_h * len(rows) + 2, strokeColor=RULE, strokeWidth=0.6))
    return d


def _table(rows, widths, *, header=True, num_cols=(), total=False):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if header:
        style += [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
                  ("BACKGROUND", (0, 0), (-1, 0), BAND)]
    for c in num_cols:
        style.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    if total:
        style += [("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 8.5),
                  ("LINEABOVE", (0, -1), (-1, -1), 0.8, INK)]
    t.setStyle(TableStyle(style))
    return t


def build_pdf(eng: models.Engagement, result: dict, gaps: list[dict]) -> bytes:
    """`result` is the readout result (engines' scenarios, rollup with the timed
    headline, new outcomes, dropped capability, narratives); `gaps` is the
    capability comparison per persona (compute.persona_coverage_gaps)."""
    primary = _color(eng.brand_primary_color, INK)
    st = _styles(primary)
    P = lambda text, style="base": Paragraph(text, st[style])  # noqa: E731
    E = escape

    rollup = result.get("rollup") or {}
    h = rollup.get("headline") or {}
    months = int(h.get("horizon_months") or 0)
    items = h.get("items") or []
    scenarios = [s for s in result.get("scenarios", []) if s.get("in_scope")]
    personas = {p.id: p for p in eng.personas}
    products = {t.id: t for t in eng.third_party_products}
    outcomes = {o.id: o for o in eng.outcomes}
    gaps_by_pid = {g["persona_id"]: g for g in gaps}
    new_by_pid = {n["persona_id"]: n for n in result.get("new_outcomes") or []}
    dropped_by_pid = {d["persona_id"]: d for d in result.get("dropped_capability") or []}
    narr_by_name = {n.get("persona"): n for n in result.get("narratives") or []}
    method = help_text.method()

    # Left out (WALKTHROUGH §5): a tool with no cost, no users or no ratified uses.
    tool_uses: dict[str, list[str]] = {}
    for ce in eng.coverage_entries:
        if ce.product_kind == "ThirdParty" and ce.ratified and ce.third_party_product_id:
            o = outcomes.get(ce.outcome_id)
            if o is not None:
                tool_uses.setdefault(ce.third_party_product_id, []).append(o.name)
    def counted(t):
        return float(t.raw_cost or 0) > 0 and (t.covered_count or 0) > 0 and tool_uses.get(t.id)

    # Each group's move, per year at the full run rate and over the horizon.
    consolidation_by_pid: dict[str, float] = {}
    run_rate_by_pid: dict[str, float] = {}
    for i in items:
        if i["sub_line"] == "consolidation" and i.get("persona_id"):
            consolidation_by_pid[i["persona_id"]] = consolidation_by_pid.get(i["persona_id"], 0.0) + float(i["amount"])
            run_rate_by_pid[i["persona_id"]] = run_rate_by_pid.get(i["persona_id"], 0.0) + float(i["annual_amount"])

    story = []

    # ---- 1. Title page -------------------------------------------------------
    logo = _logo(eng.brand_logo_data_url)
    story += [Spacer(1, 1.6 * inch)]
    if logo:
        story += [logo, Spacer(1, 0.3 * inch)]
    story += [
        P(E(eng.customer_name or "Customer"), "title"),
        Spacer(1, 6),
        P("Microsoft 365 total cost of ownership", "subtitle"),
        Spacer(1, 0.5 * inch),
        P(f"Workshop: {_date(eng.workshop_date)}", "base"),
        P(f"Period modelled: {months} months", "base"),
        PageBreak(),
    ]

    # ---- 2. Overview: the run rate, then the groups, then the ramp ----------
    total = float(h.get("amount") or 0)
    rr = float(h.get("run_rate_annual") or 0)
    n_new_all = len({o["id"] for n in result.get("new_outcomes") or [] for o in n.get("outcomes") or []})
    if rr < 0 and n_new_all:
        # A cost that buys confirmed new capabilities leads with what it buys (W6).
        lead = (f"Invest {_usd0(rr)} <font size='13' color='{MUTED.hexval()}'>per year to gain "
                f"{n_new_all} new capabilit{'y' if n_new_all == 1 else 'ies'}</font>")
    else:
        word = "per year saved" if rr > 0 else "per year added" if rr < 0 else "no net change per year"
        lead = (f"<font color='{POS.hexval() if rr > 0 else INK.hexval()}'>{_usd0(rr)}</font> "
                f"<font size='13' color='{MUTED.hexval()}'>{word}</font>")
    full_month = h.get("full_run_rate_month")
    reach = ("" if full_month is None else
             " The full run rate applies from day one." if full_month == 0 else
             f" The full run rate applies from month {full_month}." if full_month < months else
             f" The full run rate is reached after the {months} months modelled.")
    tot_word = "saved" if total > 0 else "added" if total < 0 else "no net change"
    story += [
        P("Overview", "h1"),
        P(lead, "headline"),
        P(f"Once every contract has renewed. Over {months} months, counting each saving from "
          f"the renewal that allows it: {_usd0(total)} {tot_word}.{reach}", "muted"),
        Spacer(1, 8),
    ]
    parts = [["", "Per year"]]
    if float(h.get("duplicate_spend_annual") or 0):
        parts.append(["Retire duplicate tools — no licensing change",
                      _amount(h.get("duplicate_spend_annual"))])
    parts.append(["Move each group to its recommended plan", _amount(h.get("consolidation_annual"))])
    if float(h.get("overlicensing_annual") or 0):
        parts.append(["Drop unused licences you confirmed aren't needed",
                      _amount(h.get("overlicensing_annual"))])
    parts.append(["Total per year", _amount(rr)])
    story += [_table(parts, [4.6 * inch, 1.6 * inch], num_cols=(1,), total=True), Spacer(1, 10)]

    rows = [["Group", "People", "Recommended plan", "Per year", "New capabilities"]]
    for s in scenarios:
        n_new = len((new_by_pid.get(s["persona_id"]) or {}).get("outcomes") or [])
        rows.append([P(E(s["persona_name"]), "small"), f"{s['headcount']:,}",
                     P(E(s.get("target_label") or s.get("target_sku_reference") or ""), "small"),
                     _amount(run_rate_by_pid.get(s["persona_id"], 0)), str(n_new) if n_new else "—"])
    if len(rows) > 1:
        story += [P("Recommendations by group", "h2"),
                  _table(rows, [1.7 * inch, 0.7 * inch, 2.3 * inch, 1.2 * inch, 1.0 * inch],
                         num_cols=(1, 3, 4))]

    if h.get("years"):
        story += [P("How the run rate is reached", "h2"),
                  P("Each saving starts when the contract that holds it renews; amounts with no "
                    "renewal date count from today.", "muted"),
                  Spacer(1, 4), _ramp(h["years"], rr, 6.9 * inch), Spacer(1, 6)]

    tool_rows = [["Tool", "Per year", "Renews", "Counted from"]]
    for i in items:
        if i["kind"] in ("quick_win", "tool_credit") and i.get("third_party_product_id") in products:
            t = products[i["third_party_product_id"]]
            tool_rows.append([P(E(i["label"]), "small"), _amount(i["annual_amount"]),
                              _date(t.renewal_date) if t.renewal_date else "no date",
                              "today (no date)" if i["date_missing"] else _date(i["timed_by"])])
    if len(tool_rows) > 1:
        story += [P("Tools retired", "h2"),
                  _table(tool_rows, [3.0 * inch, 1.0 * inch, 1.1 * inch, 1.8 * inch], num_cols=(1,))]

    unused = [u for u in h.get("unused_seat_lines") or [] if u.get("answer")]
    if unused:
        u_rows = [["Licence", "Unused seats", "Per year", "Your answer"]]
        for u in unused:
            u_rows.append([E(u["sku_reference"] or "—"), f"{u['unused_seats']:,}", _usd0(u["annual_value"]),
                           "not needed — counted" if u["answer"] == "NotNeeded" else "kept on purpose — not counted"])
        story += [P("Unused licences", "h2"),
                  _table(u_rows, [2.4 * inch, 1.0 * inch, 1.0 * inch, 2.5 * inch], num_cols=(1, 2))]

    # ---- 3. One section per group -------------------------------------------
    for s in scenarios:
        pid = s["persona_id"]
        p = personas.get(pid)
        g = gaps_by_pid.get(pid) or {}
        story += [PageBreak(), P(E(s["persona_name"]), "h1"),
                  P(f"{s['headcount']:,} people", "muted")]
        if p is not None and (p.description or "").strip():
            story.append(P(E(p.description), "base"))

        # Current state
        lines = [l for l in eng.licenses_in_scope
                 if (pid in l.persona_ids if l.persona_ids else True)]
        tools_used = [t for t in eng.third_party_products
                      if counted(t) and (not t.persona_ids or pid in t.persona_ids)]
        cs = []
        if lines:
            cs.append(P("<b>Microsoft licences:</b> " + E(", ".join(
                sorted({l.sku_reference for l in lines if l.sku_reference}))), "base"))
        if tools_used:
            cs.append(P("<b>Other tools:</b>", "base"))
            cs.append(_table(
                [["Tool", "Used for", "Renews"]] + [
                    [P(E(t.name), "small"), P(E(", ".join(sorted(tool_uses.get(t.id, [])))), "small"),
                     _date(t.renewal_date) if t.renewal_date else "no date"]
                    for t in tools_used],
                [1.8 * inch, 3.6 * inch, 1.2 * inch]))
        story += [P("Today", "h2")] + (cs or [P("Nothing recorded.", "muted")])

        # What's replaced
        offsets = [o for o in s.get("offsets") or [] if float(o.get("credited_offset_annual") or 0) > 0]
        if offsets:
            story += [P("What's replaced", "h2"), _table(
                [["Tool", "Per year", "Renews"]] + [
                    [P(E(o["third_party_product_name"]), "small"), _usd0(o["credited_offset_annual"]),
                     _date(products[o["third_party_product_id"]].renewal_date)
                     if o["third_party_product_id"] in products and products[o["third_party_product_id"]].renewal_date
                     else "no date"]
                    for o in offsets],
                [3.4 * inch, 1.2 * inch, 1.6 * inch], num_cols=(1,))]

        # Future state + economic impact
        per_seat = (float(s["target_spend_annual"]) / s["headcount"]) if s.get("headcount") else 0
        story += [P("Recommended", "h2"),
                  P(f"<b>{E(s.get('target_label') or s.get('target_sku_reference') or '—')}</b>"
                    f" — {_usd0(per_seat)} per person per year", "base")]
        impact = [["", "Per year"],
                  ["Microsoft licences today", _usd0(s["current_microsoft_annual"])],
                  ["Tools this plan retires", _usd0(s["current_third_party_offset_annual"])],
                  ["Microsoft licences with this plan", _usd0(s["target_spend_annual"])],
                  ["Change per year", _amount(-float(s["delta_annual"]))]]
        story += [P("Economic impact", "h2"),
                  _table(impact, [4.2 * inch, 1.4 * inch], num_cols=(1,), total=True),
                  P(f"Per year once every contract has renewed: "
                    f"<b>{_amount(run_rate_by_pid.get(pid, 0))}</b>. Over the {months} months "
                    f"modelled, counting each part from its renewal: "
                    f"<b>{_amount(consolidation_by_pid.get(pid, 0))}</b>. Positive figures are savings; "
                    f"figures in brackets are added cost."
                    + (" Tools this group could retire without any change are counted under "
                       "<i>Retire duplicate tools</i> instead."
                       if any(float(o.get("redundant_today_annual") or 0) > 0 for o in s.get("offsets") or [])
                       else ""), "muted")]

        # Capability changes — new ones highlighted
        new = (new_by_pid.get(pid) or {}).get("outcomes") or []
        dropped = (dropped_by_pid.get(pid) or {}).get("outcomes") or []
        outside = g.get("covered_outside_outcomes") or []
        cap = []
        if new:
            cap.append(P("<b><font color='%s'>New capabilities:</font></b> " % POS.hexval()
                         + E(", ".join(o["name"] for o in new)), "base"))
        if dropped:
            cap.append(P("<b>No longer included:</b> " + E(", ".join(o["name"] for o in dropped)), "base"))
        if outside:
            cap.append(P("Covered outside this review (not counted): "
                         + E(", ".join(o["name"] for o in outside)), "muted"))
        story += [P("Capability changes", "h2")] + (cap or [P(
            "Everything this plan delivers is already in place today.", "muted")])

        narr = narr_by_name.get(s["persona_name"])
        if narr and any(narr.get(k) for k in ("today", "whats_new", "value")):
            story.append(P("In summary", "h2"))
            for label, key in (("Today", "today"), ("What's new", "whats_new"), ("Value", "value")):
                if narr.get(key):
                    story.append(P(f"<b>{label}:</b> {E(narr[key])}", "base"))

    # ---- 4. How we calculated this -----------------------------------------
    story += [PageBreak(), P("How we calculated this", "h1")]
    for m in method:
        story.append(KeepTogether([P(E(m["title"]), "h3"), P(E(m["body"]), "base")]))

    assumptions = []
    listed = sorted({l.sku_reference for l in eng.licenses_in_scope
                     if l.source_tag == "ListPrice" and not l.price_override and l.sku_reference})
    if listed:
        assumptions.append("Microsoft list prices assumed for: " + ", ".join(listed) + ".")
    managed = [t for t in eng.third_party_products if t.is_managed and counted(t)]
    for t in managed:
        assumptions.append(f"{t.name} is bought as a managed service; {float(t.tooling_pct or 0) * 100:.0f}% "
                           f"of its price is counted as replaceable software.")
    no_dates = sorted({i["label"] for i in items if i.get("date_missing")})
    if no_dates:
        assumptions.append("No renewal date given, so treated as month-to-month and counted "
                           "from today: " + "; ".join(no_dates) + ".")
    groups_out = [s["persona_name"] for s in result.get("scenarios", []) if not s.get("in_scope")]
    if groups_out:
        assumptions.append("Groups not included in the totals: " + ", ".join(groups_out) + ".")
    licences_out = [f"{l.sku_reference or 'a licence'} ({l.quantity_assigned or l.quantity_purchased or 0})"
                    for l in eng.current_licenses if l.out_of_scope]
    if licences_out:
        assumptions.append("Not part of this workshop, and in no figure: "
                           + "; ".join(licences_out) + ".")
    if assumptions:
        story.append(P("Assumptions in these figures", "h2"))
        story += [P("• " + E(a), "base") for a in assumptions]

    buf = io.BytesIO()
    prepared = date.today().strftime("%d %b %Y").lstrip("0")

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(0.75 * inch, 0.5 * inch,
                          f"{eng.customer_name or ''} · Microsoft 365 total cost of ownership · "
                          f"prepared {prepared} · licensing view only")
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            topMargin=0.75 * inch, bottomMargin=0.8 * inch,
                            title=f"{eng.customer_name or 'Customer'} — Microsoft 365 TCO",
                            author="", subject="Microsoft 365 total cost of ownership")
    doc.build(story, onFirstPage=lambda c, d: None, onLaterPages=_footer)
    return buf.getvalue()
