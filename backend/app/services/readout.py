"""The customer readout — one document, two renderings (TARGET_SCHEMA D26).

The HTML readout and the customer PDF are the same HTML: the browser shows it as a
page, and WeasyPrint prints it (paged media: a cover, running footer, page
numbers). Order (docs/WALKTHROUGH.md §7):

1. Cover — the practice logo and the customer logo (or name) on white, a band in
   the practice's primary colour with the title and tagline.
2. The opportunity — the full run rate over the horizon (ENGINE_SPEC 6.11), the run
   rate beside it, each source with its share of the savings, then the ramp.
3. Quick wins.
4. One page per persona — today, the recommended plan, what it replaces, its
   economic impact itemised to its run rate, capability changes, the narrative.
5. How we get to the number — the spend bridge.
6. Third-party tools — today's spend split by what happens to it, then each tool.
7. Assumptions and sources, how the run rate is timed, how we calculated this.
8. Recommended next steps and the practice's closing block.

It only displays what the engine and the capability comparison computed. The
practice's branding comes from PracticeBranding (services/branding), the
customer's logo from the engagement; nothing about either is hard-coded.
"""

from __future__ import annotations

import html
from datetime import date
from decimal import Decimal

from .. import models
from . import branding as branding_svc
from . import help_text

E = html.escape

SUB_LINES = (
    ("duplicate_spend", "duplicate_spend_annual", "Retire duplicate tools",
     "no licensing change"),
    ("consolidation", "consolidation_annual", "Move each persona to right-sized licensing",
     "the recommended plan for each persona"),
    ("overlicensing", "overlicensing_annual", "Drop unused licences",
     "seats you confirmed aren't needed"),
)
ACTION = {"FullyEliminated": ("Retire", "retire"), "PartiallyReduced": ("Reduce seats", "reduce"),
          "Unchanged": ("Keep", "keep")}


# ---- Formatting --------------------------------------------------------------

def _f(v) -> float:
    return float(v or 0)


def _usd0(v) -> str:
    return f"${abs(_f(v)):,.0f}"


def _usd(v) -> str:
    v = _f(v)
    return f"−${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"


def _amt(v, *, cls=True) -> str:
    """Savings-positive finance notation: a saving plain (green), an added cost in
    brackets — never a minus sign next to the word savings."""
    v = _f(v)
    if v > 0:
        return f"<span class='pos'>{_usd0(v)}</span>" if cls else _usd0(v)
    if v < 0:
        return f"({_usd0(v)})"
    return "$0"


def _pct(v) -> str:
    return f"{_f(v) * 100:.0f}%"


def _date(iso) -> str:
    if not iso:
        return ""
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%-d %b %Y")
    except ValueError:
        return E(str(iso))


def _plural(n: int, one: str, many: str | None = None) -> str:
    return one if n == 1 else (many or one + "s")


def _ratio(v) -> str:
    d = Decimal(str(v or 0))
    d = d.to_integral_value() if d == d.to_integral_value() else d.normalize()
    return format(d, "f")


_SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the",
          "to", "vs", "with"}


def _title(text: str) -> str:
    """Title Case for a heading taken from content (help text): capitalise each
    word except short joining words, keeping words already capitalised."""
    words = (text or "").split(" ")
    return " ".join(
        w if (w[:1].isupper() or (i and w.lower() in _SMALL)) else w[:1].upper() + w[1:]
        for i, w in enumerate(words))


def _chip(o: dict, cls: str) -> str:
    """A capability chip; its description (the engagement's own, editable) rides
    as hover text on screen."""
    desc = (o.get("description") or "").strip()
    title = f" title=\"{E(desc, quote=True)}\"" if desc else ""
    return f"<span class='chip {cls}'{title}>{E(o['name'])}</span>"


def _cover_art(b: dict) -> str:
    """Geometric slices for the cover band — the brand's diagonal motif, in the
    practice's colours (written in: the PDF renderer doesn't apply CSS variables
    inside SVG). The colours are validated hex values (services/branding)."""
    accent, hl = b["accent_color"], b["highlight_color"]
    return ("<svg class='cover-art' viewBox='0 0 400 260' preserveAspectRatio='xMaxYMax slice' "
            "xmlns='http://www.w3.org/2000/svg' aria-hidden='true'>"
            "<polygon points='170,260 310,0 400,0 400,260' fill='#ffffff' fill-opacity='0.05'/>"
            f"<polygon points='250,260 360,0 400,0 400,260' fill='{accent}' fill-opacity='0.85'/>"
            f"<polygon points='318,260 400,95 400,260' fill='{hl}' fill-opacity='0.9'/>"
            f"<line x1='150' y1='260' x2='290' y2='0' stroke='{hl}' stroke-width='1.2' "
            "stroke-opacity='0.8'/></svg>")


def _image(data_url: str | None) -> str:
    """A logo the readout can show: a valid image data URL whose picture actually
    decodes. A broken image is left off rather than failing the document."""
    if not branding_svc.is_logo(data_url):
        return ""
    head, _, payload = (data_url or "").partition(",")
    if "svg" in head:
        return data_url
    try:
        import base64
        import io

        from PIL import Image

        raw = base64.b64decode(payload)
        with Image.open(io.BytesIO(raw)) as im:
            im.verify()               # every chunk, as the PDF renderer will read it
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
    except Exception:  # noqa: BLE001 — any undecodable picture is simply left off
        return ""
    return data_url


# ---- The document ------------------------------------------------------------

def build_html(eng: models.Engagement, result: dict, gaps: list[dict],
               brand: dict, *, prepared: date | None = None) -> str:
    """`result` is the readout result (_computed_dict); `gaps` the capability
    comparison per persona (compute.persona_coverage_gaps); `brand` the resolved
    practice branding (services/branding.resolved)."""
    prepared = prepared or date.today()
    ctx = _Context(eng, result, gaps, brand, prepared)
    body = "".join([
        ctx.footer("running-footer"), ctx.cover(), ctx.opportunity(), ctx.quick_wins(),
        ctx.persona_pages(), ctx.bridge(), ctx.third_party(), ctx.assumptions(),
        ctx.next_steps(), ctx.footer("screen-footer"),
    ])
    title = f"{eng.customer_name or 'Customer'} — Microsoft 365 TCO"
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{E(title)}</title><style>{_css(brand)}</style></head>"
            f"<body><main>{body}</main></body></html>")


def build_pdf(eng: models.Engagement, result: dict, gaps: list[dict], brand: dict,
              *, prepared: date | None = None) -> bytes:
    from weasyprint import HTML  # imported here: only the PDF needs it

    doc = build_html(eng, result, gaps, brand, prepared=prepared)
    return HTML(string=doc).write_pdf()


class _Context:
    def __init__(self, eng, result, gaps, brand, prepared):
        self.eng, self.result, self.brand, self.prepared = eng, result, brand, prepared
        self.logo = _image(brand.get("logo_data_url"))
        self.rollup = result.get("rollup") or {}
        self.h = self.rollup.get("headline") or {}
        self.months = int(self.h.get("horizon_months") or 0)
        self.items = self.h.get("items") or []
        self.scenarios = result.get("scenarios") or []
        self.in_scope = [s for s in self.scenarios if s.get("in_scope")]
        self.gaps = gaps
        self.gaps_by_pid = {g["persona_id"]: g for g in gaps}
        self.new_by_pid = {n["persona_id"]: n for n in result.get("new_outcomes") or []}
        self.dropped_by_pid = {d["persona_id"]: d for d in result.get("dropped_capability") or []}
        self.narr_by_name = {n.get("persona"): n for n in result.get("narratives") or []}
        self.personas = {p.id: p for p in eng.personas}
        self.products = {t.id: t for t in eng.third_party_products}
        self.outcome_name = {o.id: o.name for o in eng.outcomes}
        self.dispositions = [d for d in result.get("dispositions") or []
                             if _f(d.get("effective_annual_cost")) or _f(d.get("residual_annual_cost"))]
        # Ratified uses per tool, for "Used for" (a tool with none is left out, §5).
        self.tool_uses: dict[str, list[str]] = {}
        for ce in eng.coverage_entries:
            if ce.product_kind == "ThirdParty" and ce.ratified and ce.third_party_product_id:
                name = self.outcome_name.get(ce.outcome_id)
                if name:
                    self.tool_uses.setdefault(ce.third_party_product_id, []).append(name)
        self.run_rate_by_pid: dict[str, float] = {}
        self.timed_by_pid: dict[str, float] = {}
        for i in self.items:
            if i.get("sub_line") == "consolidation" and i.get("persona_id"):
                pid = i["persona_id"]
                self.run_rate_by_pid[pid] = self.run_rate_by_pid.get(pid, 0.0) + _f(i["annual_amount"])
                self.timed_by_pid[pid] = self.timed_by_pid.get(pid, 0.0) + _f(i["amount"])

    def _tlabel(self, s) -> str:
        return s.get("target_label") or s.get("target_sku_reference") or "—"

    # ---- 1. Cover --------------------------------------------------------------
    def cover(self) -> str:
        b, eng, h = self.brand, self.eng, self.h
        customer = eng.customer_name or "Customer"
        cust = _image(eng.brand_logo_data_url)
        # Logos sit on white, side by side, practice first; with one logo it
        # stands alone at the left, with none the row is left out.
        logos = [f"<img class='cover-logo' src='{E(src, quote=True)}' alt='{E(alt, quote=True)}'>"
                 for src, alt in ((self.logo, ""), (cust, customer)) if src]
        logo_row = ""
        if logos:
            logo_row = ("<div class='cover-logos'>"
                        + "<span class='cover-logo-divider'></span>".join(
                            f"<span class='cover-logo-cell'>{l}</span>" for l in logos)
                        + "</div>")
        # The headline on the cover, so the first page says what this is worth.
        rr = _f(h.get("run_rate_annual"))
        teaser = ""
        if rr and self.months:
            word = "saved" if rr > 0 else "added"
            teaser = (f"<div class='cover-figure {'saved' if rr > 0 else 'added'}'>"
                      f"<div class='cover-figure-value'>{_usd0(h.get('run_rate_horizon_amount'))}</div>"
                      f"<div class='cover-figure-label'>{word} over {self.months} months<br>"
                      f"at full run rate</div></div>")
        meta = [f"Prepared {_date(self.prepared.isoformat())}"]
        if eng.workshop_date:
            meta.append(f"Workshop {_date(eng.workshop_date)}")
        meta.append(f"Figures in {E(eng.currency or 'USD')}")
        tagline = f"<div class='cover-tagline'>{b['tagline']}</div>" if b["tagline"] else ""
        return (
            "<section class='cover'>"
            "<div class='cover-strip'></div>"
            f"<div class='cover-body'>{logo_row}"
            "<div class='cover-titles'>"
            "<div class='eyebrow'>TCO Readout</div>"
            f"<h1 class='cover-title'>{E(customer)}</h1>"
            f"<div class='cover-subtitle'>{b['cover_subtitle']}</div>"
            "<div class='cover-bar'></div>"
            f"{teaser}</div></div>"
            "<div class='cover-band'>" + _cover_art(b) +
            "<div class='cover-band-inner'><div class='brand-rule'><span></span><span></span></div>"
            f"<div class='cover-meta'>{' · '.join(meta)}</div>{tagline}</div>"
            "</div></section>"
        )

    # ---- 2. The opportunity ----------------------------------------------------
    def opportunity(self) -> str:
        h = self.h
        rr = _f(h.get("run_rate_annual"))
        horizon_total = _f(h.get("run_rate_horizon_amount"))
        months = self.months
        n_new = len({o["id"] for n in self.result.get("new_outcomes") or []
                     for o in n.get("outcomes") or []})
        full = h.get("full_run_rate_month")
        reach = ("" if full is None else
                 "full run rate from day one" if full == 0 else
                 f"full run rate from month {full}" if full < months else
                 f"full run rate reached after the {months} months modelled")
        if rr > 0:
            word, cls = f"saved over {months} months at full run rate", "saved"
            pre = ""
        elif rr < 0 and n_new:
            # A cost that buys confirmed new capabilities leads with what it buys (W6).
            pre, cls = "Invest ", "added"
            word = (f"over {months} months at full run rate to gain {n_new} new "
                    f"{_plural(n_new, 'capability', 'capabilities')}")
        elif rr < 0:
            pre, word, cls = "", f"added over {months} months at full run rate", "added"
        else:
            pre, word, cls = "", "no net change at full run rate", "added"
        per_year = ("saved" if rr > 0 else "added" if rr < 0 else "no change")
        band = (
            "<div class='hero-band'>"
            "<div class='eyebrow on-dark'>Total Opportunity</div>"
            f"<div class='hero-figure {cls}'>{pre}{_usd0(horizon_total)}</div>"
            f"<div class='hero-word'>{E(word)}</div>"
            f"<div class='hero-sub'><b>{_usd0(rr)}</b> per year {per_year} once every contract "
            f"has renewed{(' · ' + reach) if reach else ''}</div>"
            "</div>"
        )

        # Each source, with its share of the savings (from the engine: shares
        # are of the saving sub-lines, never of the net).
        shares = h.get("savings_shares") or {}
        cards, n = [], 0
        marks = "①②③"
        shown = [(k, f, label, note) for k, f, label, note in SUB_LINES
                 if k == "consolidation" or _f(h.get(f))]
        for key, field, label, note in shown:
            v = _f(h.get(field))
            mark = marks[n] + " " if len(shown) > 1 else ""
            n += 1
            share = (f"<div class='share'>{shares[key]}%<span>of the savings</span></div>"
                     if key in shares else "")
            # A cost is a "less" line against the savings; with no savings it is
            # simply the cost.
            head = f"{mark}{'Less: ' if v < 0 and shares else ''}{E(label)}"
            body = ""
            if key == "consolidation" and self.in_scope:
                rows = "".join(
                    f"<tr><td class='num'>{_amt(self.run_rate_by_pid.get(s['persona_id'], 0))}</td>"
                    f"<td><b>{E(s['persona_name'])}</b> <span class='muted'>({s['headcount']:,})</span>"
                    f" → <b>{E(self._tlabel(s))}</b></td></tr>"
                    for s in self.in_scope)
                body = f"<table class='moves'>{rows}</table>"
            cards.append(
                f"<div class='source {'cost' if v < 0 else ''}'><div class='source-main'>"
                f"<div class='source-label'>{head} <span class='muted'>— {E(note)}</span></div>"
                f"<div class='source-value'>{_amt(v)} <span class='per'>per year</span></div>"
                f"{body}</div>{share}</div>")

        caveats = []
        assumed = sum(1 for lic in self.eng.licenses_in_scope if lic.source_tag == "ListPrice")
        if assumed:
            caveats.append(f"Today's spend uses list prices for {assumed} current "
                           f"{_plural(assumed, 'licence')}; your invoices may move this number.")
        missing = int(h.get("missing_dates") or 0)
        if missing:
            caveats.append(f"{missing} {_plural(missing, 'amount has', 'amounts have')} no renewal "
                           f"date and {_plural(missing, 'counts', 'count')} from today.")
        n_drop = len(self.result.get("dropped_capability") or [])
        if n_drop:
            caveats.append(f"Includes capability trade-offs for {n_drop} "
                           f"{_plural(n_drop, 'persona')} — see each persona's page.")
        caveat = f"<p class='caveat'>{' '.join(caveats)}</p>" if caveats else ""

        return (
            "<section class='page opportunity'>"
            "<div class='eyebrow'>Microsoft 365 · Licensing View</div>"
            "<h1>The Opportunity</h1>"
            f"{band}<div class='sources'>{''.join(cards)}</div>{caveat}{self._ramp()}"
            "</section>"
        )

    def _ramp(self) -> str:
        h = self.h
        years = h.get("years") or []
        if not years:
            return ""
        rr = _f(h.get("run_rate_annual"))
        timed = _f(h.get("amount"))
        rows = [(f"Year {y['year']}", _f(y["amount"]), _f(y["cumulative"])) for y in years]
        rows.append(("Full run rate", rr, None))
        peak = max([abs(v) for _, v, _ in rows] + [1.0])
        out = []
        for label, v, cum in rows:
            width = max(round(abs(v) / peak * 100, 1), 0.5 if v else 0)
            extra = (f"<span class='muted'> · running total {_amt(cum, cls=False)}</span>"
                     if cum is not None else "")
            out.append(
                f"<tr class='{'full' if cum is None else ''}'><th>{label}</th>"
                f"<td class='track'><div class='bar {'pos' if v >= 0 else 'neg'}' style='width:{width}%'></div></td>"
                f"<td class='num'>{_amt(v)}{extra}</td></tr>")
        word = "saved" if timed > 0 else "added" if timed < 0 else "no net change"
        return (
            "<div class='ramp-block'><h2>Path to Full Run Rate</h2>"
            "<p class='lede'>Each saving starts when the contract that holds it renews, so the "
            f"first years build up to the full run rate. <b>Within the first {self.months} months, "
            f"{_usd0(timed)} is {word}</b> as contracts renew.</p>"
            f"<table class='ramp'>{''.join(out)}</table></div>"
        )

    # ---- 3. Quick wins ---------------------------------------------------------
    def quick_wins(self) -> str:
        qws = self.rollup.get("quick_wins") or []
        if not qws:
            return ""
        total = self.rollup.get("quick_win_savings_annual") or 0
        rows = "".join(
            f"<tr><td><b>{E(q['third_party_product_name'])}</b></td>"
            f"<td>{''.join(f'<span class=chip>{E(self.outcome_name.get(i, i))}</span>' for i in q['duplicated_outcome_ids'])}</td>"
            f"<td class='num'>{q['covered_count']:,}</td>"
            f"<td class='num'>{q['displaced_today']:,}"
            + (f" <span class='muted'>({q['residual_today']:,} left)</span>" if q["residual_today"] else "")
            + f"</td><td class='num'>{_amt(q['credited_annual'])}</td></tr>"
            for q in qws)
        return (
            "<section class='block keep'><div class='eyebrow'>① Duplicate Spend</div>"
            "<h2>Quick Wins</h2>"
            "<p class='lede'>These tools deliver capabilities your <b>current</b> Microsoft licensing "
            f"already includes. Each can retire at its renewal with no licensing change: "
            f"<b class='pos'>{_usd0(total)}</b> a year.</p>"
            "<table class='data'><thead><tr><th>Tool</th><th>Already in your licensing</th>"
            "<th class='num'>Seats</th><th class='num'>Redundant today</th><th class='num'>Per year</th>"
            f"</tr></thead><tbody>{rows}</tbody></table></section>"
        )

    # ---- 4. One page per persona ----------------------------------------------
    def persona_pages(self) -> str:
        return "".join(self._persona(s) for s in self.in_scope)

    def _persona(self, s) -> str:
        pid = s["persona_id"]
        p = self.personas.get(pid)
        g = self.gaps_by_pid.get(pid) or {}
        desc = (p.description or "").strip() if p is not None else ""
        head = (
            "<div class='persona-head'><div class='eyebrow'>Persona</div>"
            f"<h1>{E(s['persona_name'])}</h1>"
            f"<div class='persona-meta'>{s['headcount']:,} people"
            + (f" · {E(desc)}" if desc else "") + "</div></div>")

        # Today
        lines = [l for l in self.eng.licenses_in_scope
                 if (pid in l.persona_ids if l.persona_ids else True)]
        tools = [t for t in self.eng.third_party_products
                 if _f(t.raw_cost) > 0 and (t.covered_count or 0) > 0 and self.tool_uses.get(t.id)
                 and (not t.persona_ids or pid in t.persona_ids)]
        lic_chips = "".join(f"<span class='chip plain'>{E(r)}</span>"
                            for r in sorted({l.sku_reference for l in lines if l.sku_reference}))
        today = "<div class='col'><h3>Today</h3>"
        today += (f"<div class='label'>Microsoft licences</div><div class='chips'>{lic_chips}</div>"
                  if lic_chips else "<p class='muted'>No Microsoft licences recorded.</p>")
        if tools:
            today += ("<div class='label'>Other tools</div><table class='data compact'><tbody>"
                      + "".join(
                          f"<tr><td><b>{E(t.name)}</b><div class='muted'>"
                          f"{E(', '.join(sorted(self.tool_uses.get(t.id, []))))}</div></td>"
                          f"<td class='num muted'>{'renews ' + _date(t.renewal_date) if t.renewal_date else 'no date'}</td></tr>"
                          for t in tools)
                      + "</tbody></table>")
        today += "</div>"

        per_seat = (_f(s["target_spend_annual"]) / s["headcount"]) if s.get("headcount") else 0
        plan = ("<div class='col'><h3>Recommended</h3>"
                f"<div class='plan'>{E(self._tlabel(s))}</div>"
                f"<div class='muted'>{_usd0(per_seat)} per person per year</div>")
        offs = s.get("offsets") or []
        replaced = [o for o in offs if _f(o.get("credited_offset_annual")) > 0]
        if replaced:
            plan += ("<div class='label'>What it replaces</div><table class='data compact'><tbody>"
                     + "".join(
                         f"<tr><td>{E(o['third_party_product_name'])}</td>"
                         f"<td class='num'>{_usd0(o['credited_offset_annual'])}</td></tr>"
                         for o in replaced)
                     + "</tbody></table>")
        plan += "</div>"

        # Economic impact: itemised to this persona's run rate. Tools it could
        # retire without any change are the duplicate-spend source (ENGINE_SPEC
        # 6.8a), so only the part the move unlocks is credited here.
        today_only = sum(_f(o.get("redundant_today_annual")) for o in offs)
        unlocked = [o for o in offs if _f(o.get("move_unlocked_annual")) > 0]
        rows = [
            f"<tr><td>Microsoft licences today</td><td class='num'>{_usd0(s['current_microsoft_annual'])}</td></tr>",
            f"<tr><td>Tools only this plan retires</td><td class='num'>"
            f"{_usd0(sum(_f(o['move_unlocked_annual']) for o in unlocked))}</td></tr>",
        ] + [
            f"<tr class='sub'><td>{E(o['third_party_product_name'])}</td>"
            f"<td class='num'>{_usd0(o['move_unlocked_annual'])}</td></tr>" for o in unlocked
        ] + [
            f"<tr><td>Less: Microsoft licences with this plan</td>"
            f"<td class='num'>({_usd0(s['target_spend_annual'])})</td></tr>",
            f"<tr class='total'><td>Change per year</td>"
            f"<td class='num'>{_amt(-_f(s.get('move_incremental_delta_annual')))}</td></tr>",
        ]
        note = (f"Per year once every contract has renewed: <b>{_amt(self.run_rate_by_pid.get(pid, 0), cls=False)}</b>. "
                f"Within the {self.months} months modelled, counting each part from its renewal: "
                f"{_amt(self.timed_by_pid.get(pid, 0), cls=False)}.")
        if today_only > 0:
            note += (f" Tools this persona could retire without any change ({_usd0(today_only)} a year) "
                     "are counted under ① Retire duplicate tools.")
        impact = ("<div class='impact'><h3>Economic Impact</h3>"
                  f"<table class='ledger'><tbody>{''.join(rows)}</tbody></table>"
                  f"<p class='note'>{note}</p></div>")

        return (f"<section class='page persona'>{head}{self._narrative(s)}"
                f"<div class='cols'>{today}{plan}</div>"
                f"{impact}{self._capabilities(pid, g)}</section>")

    def _capabilities(self, pid, g) -> str:
        new = (self.new_by_pid.get(pid) or {}).get("outcomes") or []
        dropped = (self.dropped_by_pid.get(pid) or {}).get("outcomes") or []
        outside = g.get("covered_outside_outcomes") or []
        reason = (self.new_by_pid.get(pid) or {}).get("empty_reason")
        unread = [u["sku_reference"] for u in g.get("unmapped_current_licenses") or []]
        parts = []
        if reason == "licence_unread":
            parts.append(
                f"<p class='muted'>Capability changes aren't shown for this persona: we couldn't "
                f"confirm what {E(', '.join(unread))} {_plural(len(unread), 'includes', 'include')}, "
                f"so we can't say what it gains or gives up. Its cost is counted above.</p>")
        if new:
            parts.append("<div class='label'>New capabilities</div><div class='chips'>"
                         + "".join(_chip(o, "new") for o in new)
                         + "</div>")
        if dropped:
            parts.append("<div class='label'>No longer included</div><div class='chips'>"
                         + "".join(_chip(o, "drop") for o in dropped)
                         + "</div>")
        if outside:
            parts.append("<p class='muted'>Covered outside this review (not counted): "
                         + E(", ".join(o["name"] for o in outside)) + "</p>")
        # Why nothing is new, in the customer's words (compute.new_outcomes gives the
        # reason; its own sentence is written for the operator).
        if reason == "awaiting_confirmation":
            parts.append("<p class='muted'>Capabilities this plan would add weren't confirmed in "
                         "the workshop, so none is shown as new.</p>")
        elif reason == "target_unmapped":
            refs = ", ".join(r["reference"] for r in g.get("unmapped_target") or []) or "this plan"
            parts.append(f"<p class='muted'>Capability changes aren't shown: what {E(refs)} "
                         "includes isn't mapped in this review, so nothing can be shown as gained "
                         "or given up.</p>")
        elif reason == "covered_org_wide":
            refs = ", ".join(g.get("org_wide_current_licenses") or [])
            parts.append("<p class='muted'>Nothing new: everything this plan delivers already counts "
                         f"as delivered today — partly from licences with no persona assigned "
                         f"({E(refs)}), which count for every persona.</p>")
        if not parts:
            parts.append("<p class='muted'>Nothing new: everything this plan delivers is already "
                         "in place today.</p>")
        return f"<div class='capabilities'><h3>Capability Changes</h3>{''.join(parts)}</div>"

    def _narrative(self, s) -> str:
        n = self.narr_by_name.get(s["persona_name"])
        if not n or not any(n.get(k) for k in ("today", "whats_new", "value")):
            return ""
        body = "".join(f"<p><b>{label}</b> {E(n[key])}</p>"
                       for label, key in (("Today.", "today"), ("What's new.", "whats_new"),
                                          ("Value.", "value")) if n.get(key))
        return f"<div class='narrative'>{body}</div>"

    # ---- 5. How we get to the number -------------------------------------------
    def bridge(self) -> str:
        r = self.rollup
        if not self.in_scope:
            return ""
        # One column per persona while it fits (up to three); beyond that the total
        # only — each persona's page carries its own itemised impact.
        cols = self.in_scope if 2 <= len(self.in_scope) <= 3 else []
        freed = r.get("freed_third_party") or []
        already = [f for f in freed if f.get("already_covered")]
        newly = [f for f in freed if not f.get("already_covered")]
        offset_key = {"redundant_today_annual": "redundant_today_annual",
                      "move_unlocked_annual": "move_unlocked_annual",
                      "credited_annual": "credited_offset_annual"}

        def off(s, pid, field):
            return next((_f(o.get(offset_key[field])) for o in s.get("offsets", [])
                         if o["third_party_product_id"] == pid), 0.0)

        def cells(values, total, negate=False, cls=""):
            def fmt(v):
                return (f"−{_usd0(v)}" if v else "$0") if negate else _usd(v)
            return ("".join(f"<td class='num {cls}'>{fmt(v)}</td>" for v in values)
                    + f"<td class='num {cls}'>{fmt(total)}</td>")

        def group(label, sub, items, field):
            items = [f for f in items if _f(f.get(field))]
            if not items:
                return ""
            head = (f"<tr class='group'><td>Less: {label} <span class='muted'>{sub}</span></td>"
                    + cells([sum(off(s, f["third_party_product_id"], field) for f in items) for s in cols],
                            sum(_f(f[field]) for f in items), negate=True, cls="pos") + "</tr>")
            return head + "".join(
                f"<tr class='sub'><td>{E(f['third_party_product_name'])}</td>"
                + cells([off(s, f["third_party_product_id"], field) for s in cols], f[field],
                        negate=True) + "</tr>"
                for f in items)

        delta = _f(r.get("net_tco_delta_annual"))
        thead = ("<thead><tr><th></th>" + "".join(
            f"<th class='num'>{E(s['persona_name'])}<div class='muted'>→ {E(self._tlabel(s))}</div></th>"
            for s in cols) + "<th class='num'>Total</th></tr></thead>")
        rows = (
            "<tr><td>Microsoft licensing with the recommended plans</td>"
            + cells([_f(s["target_spend_annual"]) for s in cols], r.get("target_microsoft_annual")) + "</tr>"
            "<tr><td>Less: Microsoft licensing retired <span class='muted'>(current, assigned)</span></td>"
            + cells([_f(s["current_microsoft_annual"]) for s in cols], r.get("existing_microsoft_annual"),
                    negate=True, cls="pos") + "</tr>"
            + group("tools redundant today", "(the quick wins — no licensing change)", already,
                    "redundant_today_annual")
            + group("further seats on those tools", "(retired once the plan lands)", already,
                    "move_unlocked_annual")
            + group("tools the new plan retires", "(the capability arrives with the plan)", newly,
                    "credited_annual")
            + "<tr class='total'><td>Net change per year "
            f"<span class='muted'>({_usd0(delta)} {'saved' if delta < 0 else 'added' if delta > 0 else 'no change'})</span></td>"
            + "".join(f"<td class='num {'pos' if _f(s['delta_annual']) < 0 else ''}'>{_usd(s['delta_annual'])}</td>"
                      for s in cols)
            + f"<td class='num {'pos' if delta < 0 else ''}'>{_usd(delta)}</td></tr>")
        note = ""
        gap = _f(r.get("quick_win_savings_annual")) - _f(r.get("freed_redundant_today_annual"))
        if abs(gap) >= 0.5:
            note = (f"<p class='note'>A further {_usd0(gap)} a year of quick wins sits outside this "
                    "bridge: tools retirable today that no persona's move touches. Together with "
                    "the bridge they make the run rate.</p>")
        unused = _f(self.h.get("overlicensing_annual"))
        if unused:
            note += (f"<p class='note'>Dropping unused licences adds {_usd0(unused)} a year on top "
                     "(③ above); it is not part of this bridge.</p>")
        return (
            "<section class='page'><div class='eyebrow'>The Accounting</div>"
            "<h1>Spend Bridge</h1>"
            "<p class='lede'>The Microsoft licensing the recommended plans cost, less the licensing "
            "and tools they retire, building to the net change per year"
            + (", with each persona side by side." if cols else ".") + " Reductions show as −.</p>"
            f"<table class='bridge'>{thead}<tbody>{rows}</tbody></table>{note}</section>"
        )

    # ---- 6. Third-party tools --------------------------------------------------
    def third_party(self) -> str:
        if not self.dispositions:
            return ""
        r = self.rollup
        qw_by_pid = {q["third_party_product_id"]: _f(q["credited_annual"])
                     for q in r.get("quick_wins") or []}
        today_total = sum(_f(d["effective_annual_cost"]) for d in self.dispositions)
        now = sum(qw_by_pid.get(d["third_party_product_id"], 0.0) for d in self.dispositions)
        reduced_left = sum(_f(d["residual_annual_cost"]) for d in self.dispositions
                           if d["disposition"] == "PartiallyReduced")
        kept = sum(_f(d["residual_annual_cost"]) for d in self.dispositions
                   if d["disposition"] not in ("PartiallyReduced",))
        # What the new plans retire: the move's own credit on tools already
        # partly covered, plus tools only the plans cover (ENGINE_SPEC 6.8a).
        by_move = sum(_f(f.get("move_unlocked_annual")) if f.get("already_covered")
                      else _f(f.get("credited_annual"))
                      for f in r.get("freed_third_party") or [])
        segs = [("now", "Retired at renewal — already covered", now),
                ("move", "Retired by the new plans", by_move),
                ("reduced", "Remains on reduced tools", reduced_left),
                ("kept", "Kept as is", kept)]
        bar = "".join(
            f"<td class='seg {k}' style='width:{v / today_total * 100:.2f}%'></td>"
            for k, _, v in segs if v > 0) if today_total else ""
        legend = "".join(
            f"<div class='legend-item'><span class='swatch {k}'></span><div><b>{_usd0(v)}</b>"
            f" <span class='muted'>{_pct(v / today_total) if today_total else ''}</span>"
            f"<div class='muted small'>{E(label)}</div></div></div>"
            for k, label, v in segs if v > 0)

        def row(d):
            label, cls = ACTION.get(d["disposition"], (d["disposition"], "keep"))
            unset = not d["covered_count"]
            retired = "—" if unset else f"{min(d['displaced_users'], d['covered_count']):,}"
            kept_n = "—" if unset else f"{d['residual_count']:,}"
            basis = (f"Managed service · {_pct(d['tooling_pct'])} software share"
                     if d["is_managed"] else "Direct licence")
            note = ("Users not set — seats and savings can't be counted" if unset else
                    E(d["override_reason"]) if d.get("override") not in (None, "None") else "")
            return (f"<tr><td><b>{E(d['third_party_product_name'])}</b><div class='muted small'>{basis}"
                    + (f" · {note}" if note else "") + "</div></td>"
                    f"<td><span class='tag {cls}'>{label}</span></td>"
                    f"<td class='num'>{retired}</td><td class='num'>{kept_n}</td>"
                    f"<td class='num'>{_usd0(d['effective_annual_cost'])}</td>"
                    f"<td class='num'>{_usd0(d['residual_annual_cost'])}</td></tr>")

        renew = {x["third_party_product_name"]: x.get("renewal_date")
                 for x in r.get("eliminated_renewal_cycles") or []}
        retired = r.get("fully_eliminated_tools") or []
        retired_html = ""
        if retired:
            retired_html = ("<h3>Retired Completely</h3><ul class='retired'>" + "".join(
                f"<li><b>{E(t)}</b>"
                + (f" <span class='muted'>— renews {_date(renew[t])}; co-term the retirement</span>"
                   if renew.get(t) else "") + "</li>" for t in retired) + "</ul>")
        return (
            "<section class='page'><div class='eyebrow'>Disposition</div>"
            "<h1>Third-Party Tools</h1>"
            f"<p class='lede'>Today these tools cost <b>{_usd0(today_total)}</b> a year in the "
            f"counted scope. After the move, <b>{_usd0(r.get('residual_third_party_cost_annual'))}</b> "
            "a year remains (reduced seats plus tools kept as they are).</p>"
            f"<table class='stack'><tr>{bar}</tr></table><div class='legend'>{legend}</div>"
            "<table class='data'><thead><tr><th>Tool</th><th>Action</th><th class='num'>Seats retired</th>"
            "<th class='num'>Seats kept</th><th class='num'>Today / yr</th><th class='num'>Remains / yr</th>"
            f"</tr></thead><tbody>{''.join(row(d) for d in self.dispositions)}</tbody></table>"
            f"{retired_html}</section>"
        )

    # ---- 7. Assumptions, timing, method ------------------------------------------
    def assumptions(self) -> str:
        eng, parts = self.eng, []
        if eng.licenses_in_scope:
            def lic_row(lic):
                eff, base = _f(lic.effective_unit_price_annual), _f(lic.list_unit_price_annual)
                note = ""
                if lic.price_override:
                    note = (" <span class='muted'>(negotiated price"
                            + (f" · {_pct((base - eff) / base)} below list {_usd0(base)}" if base else "")
                            + ")</span>")
                elif lic.source_tag == "ListPrice":
                    note = " <span class='muted'>(list price assumed)</span>"
                return (f"<tr><td>{E(lic.sku_reference)}{note}</td><td class='num'>{lic.quantity_assigned or 0:,}</td>"
                        f"<td class='num'>${eff:,.2f}</td><td class='num'>{_usd0(eff * (lic.quantity_assigned or 0))}</td></tr>")
            parts.append("<h3>Current Licensing</h3><table class='data'><thead><tr>"
                         "<th>Licence</th><th class='num'>Seats assigned</th><th class='num'>Price / seat / yr</th>"
                         "<th class='num'>Per year</th></tr></thead><tbody>"
                         + "".join(lic_row(l) for l in eng.licenses_in_scope) + "</tbody></table>")
        notes = []
        term = {"P1Y": "1-year term", "P1M": "monthly term", "P3Y": "3-year term"}
        notes.append(f"<b>Pricing basis for the recommended plans:</b> {E(eng.default_segment or 'Commercial')} · "
                     f"{E(term.get(eng.default_term_duration or 'P1Y', eng.default_term_duration or 'P1Y'))} · "
                     f"{E(eng.default_billing_plan or 'Monthly')} billing — the catalog price unless a "
                     "negotiated price is entered.")
        persona_name = {p.id: p.name for p in eng.personas}
        tlabels = {s["persona_id"]: self._tlabel(s) for s in self.scenarios}
        negotiated = [
            f"{E(persona_name.get(sc.persona_id, 'Persona'))} → {E(tlabels.get(sc.persona_id, sc.target_sku_reference))}: "
            f"${_f(sc.effective_net_annual):,.2f} per seat per year"
            + (f" ({_pct((_f(sc.composed_list_annual) - _f(sc.effective_net_annual)) / _f(sc.composed_list_annual))} below list)"
               if _f(sc.composed_list_annual) else "")
            for sc in eng.scenarios if sc.price_override]
        if negotiated:
            notes.append("<b>Negotiated prices on the recommended plans:</b> " + "; ".join(negotiated) + ".")
        managed = [d for d in self.dispositions if d["is_managed"]]
        for d in managed:
            notes.append(f"<b>{E(d['third_party_product_name'])}</b> is bought as a managed service; "
                         f"{_pct(d['tooling_pct'])} of its price is counted as replaceable software.")
        for d in self.dispositions:
            if d.get("override") == "ForceFullElimination":
                notes.append(f"<b>{E(d['third_party_product_name'])}</b> is assumed fully retired: "
                             f"{E(d.get('override_reason') or '')}")
        no_dates = sorted({i["label"] for i in self.items if i.get("date_missing")})
        if no_dates:
            notes.append("<b>No renewal date given</b>, so treated as month-to-month and counted from "
                         "today: " + E("; ".join(no_dates)) + ".")
        in_scope_pids = {s["persona_id"] for s in self.in_scope}
        unread: dict[str, list[str]] = {}
        for g in self.gaps:
            if g["persona_id"] in in_scope_pids:
                for u in g.get("unmapped_current_licenses") or []:
                    unread.setdefault(u["sku_reference"], []).append(g["persona_name"])
        if unread:
            notes.append("<b>We couldn't confirm what these licences include</b>, so the capability "
                         "changes of the personas holding them are left out (their cost is counted): "
                         + E("; ".join(f"{k} ({', '.join(v)})" for k, v in unread.items())) + ".")
        out_personas = [s["persona_name"] for s in self.scenarios if not s.get("in_scope")]
        if out_personas:
            notes.append("<b>Personas not included in the totals:</b> " + E(", ".join(out_personas)) + ".")
        out_lic = [f"{l.sku_reference or 'a licence'} ({l.quantity_assigned or l.quantity_purchased or 0})"
                   for l in eng.current_licenses if l.out_of_scope]
        if out_lic:
            notes.append("<b>Not part of this workshop, and in no figure:</b> " + E("; ".join(out_lic)) + ".")
        soft = {"ListPrice": "list price assumed", "Estimate": "estimate",
                "AISuggestedUnconfirmed": "suggested, not yet confirmed"}
        soft_inputs = ([(p.name, "persona", p.source_tag) for p in eng.personas if p.source_tag in soft]
                       + [(t.name, "tool", t.source_tag) for t in eng.third_party_products if t.source_tag in soft])
        if soft_inputs:
            notes.append("<b>Inputs carried as assumptions:</b> " + E("; ".join(
                f"{n} ({k}: {soft[t]})" for n, k, t in soft_inputs)) + ".")
        parts.append("<ul class='notes'>" + "".join(f"<li>{n}</li>" for n in notes) + "</ul>")

        unused = self.h.get("unused_seat_lines") or []
        if unused:
            answer = {"NotNeeded": "not needed — counted as over-licensing", "Intended": "kept on purpose — not counted",
                      None: "not answered — left out"}
            parts.append("<h3>Unused Licences</h3><table class='data'><thead><tr><th>Licence</th>"
                         "<th class='num'>Unused seats</th><th class='num'>Per year</th><th>Your answer</th>"
                         "</tr></thead><tbody>" + "".join(
                             f"<tr><td>{E(u['sku_reference'] or '—')}</td><td class='num'>{u['unused_seats']:,}</td>"
                             f"<td class='num'>{_usd0(u['annual_value'])}</td><td>{answer.get(u.get('answer'), '')}</td></tr>"
                             for u in unused) + "</tbody></table>")

        if self.items:
            kind = {"quick_win": "duplicate tool, at its renewal", "tool_credit": "tool, at its renewal",
                    "microsoft_increase": "Microsoft increase, from the first tool only the plan retires",
                    "microsoft_reduction": "Microsoft reduction, at the Microsoft renewal",
                    "unused_seats": "unused licences, at the Microsoft renewal"}
            parts.append(
                f"<h3>Run-Rate Timing</h3><table class='data timing'><thead><tr><th>Item</th>"
                "<th>Starts</th><th class='num'>Months counted</th><th class='num'>Per year</th>"
                f"<th class='num'>Within {self.months} months</th></tr></thead><tbody>"
                + "".join(
                    f"<tr><td>{E(i['label'])}<div class='muted small'>{kind.get(i['kind'], '')}</div></td>"
                    f"<td>{'no date — from today' if i.get('date_missing') else _date(i.get('timed_by')) or '—'}</td>"
                    f"<td class='num'>{i['months_counted']}</td><td class='num'>{_amt(i['annual_amount'], cls=False)}</td>"
                    f"<td class='num'>{_amt(i['amount'], cls=False)}</td></tr>" for i in self.items)
                + f"<tr class='total'><td>Total</td><td></td><td></td>"
                f"<td class='num'>{_amt(self.h.get('run_rate_annual'), cls=False)}</td>"
                f"<td class='num'>{_amt(self.h.get('amount'), cls=False)}</td></tr></tbody></table>")

        method = "".join(f"<div class='method-item'><h4>{E(_title(m['title']))}</h4><p>{E(m['body'])}</p></div>"
                         for m in help_text.method())
        return (
            "<section class='page'><div class='eyebrow'>The Basis</div>"
            "<h1>Assumptions &amp; Sources</h1>" + "".join(parts)
            + f"<h2>Methodology</h2><div class='method'>{method}</div></section>"
        )

    # ---- 8. Next steps and the close ---------------------------------------------
    def next_steps(self) -> str:
        r, eng = self.rollup, self.eng
        if not self.in_scope:
            return ""
        n_retire = len(r.get("fully_eliminated_tools") or [])
        fund = ("<li><b>Fund.</b> Microsoft co-investment programs may offset transition cost — "
                "quantified in the follow-on.</li>")
        if eng.managed_ms_account and _f(r.get("msft_uplift_annual")) > 0:
            fund = (
                "<li><b>Fund.</b> As a managed Microsoft account, this move lifts annual Microsoft "
                f"spend by {_usd0(r['msft_uplift_annual'])}. Per standard Microsoft investment ROI, "
                f"that uplift may unlock a one-time co-investment (ECIF) of roughly "
                f"<b>{_usd0(r.get('ecif_funding_low'))}–{_usd0(r.get('ecif_funding_high'))}</b> "
                f"(≈ 1/{_ratio(eng.ecif_roi_conservative)}–1/{_ratio(eng.ecif_roi_generous)} of the "
                "annual uplift) toward assessment, design, implementation and migration work. "
                "Indicative only; scoped with the Microsoft account team.</li>")
        steps = (
            "<li><b>Validate.</b> Confirm licence counts and third-party invoices, and the contract "
            f"end dates for the {n_retire} {_plural(n_retire, 'tool')} to retire — turning the "
            "assumptions here into an invoice-verified business case.</li>"
            "<li><b>Sequence.</b> Co-term each retirement against its renewal. The ramp already "
            "counts each saving from its renewal; confirm any amount with no date.</li>"
            + fund +
            "<li><b>Decide.</b> A 30-day validation sprint turns this readout into an "
            "invoice-verified, phased business case.</li>")
        closing = (f"<div class='closing'>{self.brand['closing_block']}</div>"
                   if self.brand["closing_block"] else "")
        return (
            "<section class='block next'><div class='eyebrow'>Recommended</div>"
            f"<h1>Next Steps</h1><ol class='steps'>{steps}</ol>"
            "<p class='note'>Savings are before one-time migration cost, and moving workloads "
            "(chat, device management, identity) is a change-management event, not just a licence "
            f"swap — both are scoped in the follow-on.</p>{closing}</section>"
        )

    # ---- Running footer (every printed page; the end of the page on screen) -----
    def footer(self, cls: str) -> str:
        """The print copy is a running element (it must come first, so every page
        after the cover carries it); the screen copy closes the page."""
        b, eng = self.brand, self.eng
        logo = (f"<img src='{E(self.logo, quote=True)}' alt=''>" if self.logo else "")
        return (
            f"<footer class='doc-footer {cls}'>"
            f"<div class='footer-logo'>{logo}</div><div class='footer-text'>"
            f"<div>{E(eng.customer_name or '')} · Microsoft 365 TCO Readout · "
            f"prepared {_date(self.prepared.isoformat())}</div>"
            f"<div class='disclaimer'>{b['footer_disclaimer']}</div></div></footer>"
        )


# ---- Styles ------------------------------------------------------------------

def _css(b: dict) -> str:
    return """
:root{--primary:%(primary)s;--accent:%(accent)s;--hl:%(hl)s;--ink:#1b2433;--muted:#5d6778;
  --line:#e2e7ef;--soft:#f3f6fb;--pos:#127436;--warn:#9a6700;--now:#127436;--move:%(accent)s;
  --reduced:#C98500;--kept:#9aa3b2}
*{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact}
html{font-size:10pt}
body{margin:0;background:#fff;color:var(--ink);
  font-family:'TeX Gyre Heros','Helvetica Neue',Helvetica,Arial,sans-serif;line-height:1.42}
main{max-width:8.5in;margin:0 auto}
h1{font-size:22pt;line-height:1.1;margin:.1rem 0 .6rem;color:var(--primary);letter-spacing:-.01em}
h2{font-size:13pt;margin:1.4rem 0 .4rem;color:var(--primary)}
h3{font-size:10.5pt;margin:1rem 0 .35rem;color:var(--primary)}
h4{font-size:9.5pt;margin:.6rem 0 .1rem}
p{margin:.3rem 0}
.eyebrow{font-size:7.5pt;font-weight:700;letter-spacing:.22em;text-transform:uppercase;
  color:var(--accent);margin-bottom:.2rem}
.eyebrow.on-dark{color:var(--hl)}
.muted{color:var(--muted)} .small{font-size:8pt} .pos{color:var(--pos)}
.lede{color:var(--muted);font-size:10pt;max-width:6.6in}
.note{color:var(--muted);font-size:8.5pt}
section{padding:0 .1in}
section.page{break-before:page;padding-top:.05in}
section.block{margin-top:1.6rem}
section.block.keep{break-inside:avoid}
h1,h2,h3,.eyebrow,.label{break-after:avoid}

/* Cover */
section.cover{padding:0;break-after:page;page:cover;position:relative;height:11in;overflow:hidden;background:#fff}
.cover-strip{height:.16in;background:var(--primary)}
.cover-body{padding:.75in .85in 0;height:7.5in}
.cover-logos{height:1.15in;white-space:nowrap}
.cover-logo-cell{display:inline-block;vertical-align:middle}
.cover-logo{max-height:.95in;max-width:3.1in;vertical-align:middle}
.cover-logo-divider{display:inline-block;width:1px;height:.75in;background:var(--line);
  margin:0 .32in;vertical-align:middle}
.cover-titles{margin-top:1.15in}
.cover-titles .eyebrow{font-size:9pt}
.cover-title{color:var(--primary);font-size:46pt;line-height:1.02;margin:.08in 0 .1in;letter-spacing:-.025em}
.cover-subtitle{font-size:17pt;color:var(--muted)}
.cover-bar{width:.75in;height:4px;background:var(--hl);margin:.3in 0 .32in}
.cover-figure{display:inline-block;border-left:4px solid var(--accent);padding:.02in 0 .02in .2in}
.cover-figure-value{font-size:30pt;font-weight:700;color:var(--primary);letter-spacing:-.02em;line-height:1}
.cover-figure-label{font-size:10pt;color:var(--muted);margin-top:.06in;line-height:1.3}
.cover-band{position:absolute;left:0;right:0;bottom:0;height:3.34in;background:var(--primary);overflow:hidden}
.cover-art{position:absolute;right:0;bottom:0;width:4.6in;height:3.34in}
.cover-band-inner{position:absolute;left:.85in;bottom:.7in;width:4.4in}
.brand-rule{display:table;width:100%%;height:2px;margin-bottom:.14in}
.brand-rule span{display:table-cell;height:2px}
.brand-rule span:first-child{width:22%%;background:var(--hl)}
.brand-rule span:last-child{background:var(--accent)}
.cover-meta{font-size:9.5pt;letter-spacing:.05em;color:#fff;opacity:.8}
.cover-tagline{margin-top:.16in;font-size:13pt;letter-spacing:.06em;color:#fff}
.cover-tagline .hl,.closing .hl,.disclaimer .hl{color:var(--hl);font-weight:700}
.cover-tagline a{color:#fff}

/* The opportunity */
.hero-band{background:var(--primary);color:#fff;border-radius:6px;padding:.28in .32in;margin:.15in 0 .18in}
.hero-figure{font-size:40pt;font-weight:700;line-height:1;letter-spacing:-.02em;margin:.06in 0 .04in}
.hero-figure.saved{color:var(--hl)} .hero-figure.added{color:#fff}
.hero-word{font-size:13pt;font-weight:700}
.hero-sub{margin-top:.1in;font-size:10pt;opacity:.85}
.sources{margin:.1in 0}
.source{display:table;width:100%%;border:1px solid var(--line);border-left:3px solid var(--accent);
  border-radius:5px;padding:.11in .16in;margin:.08in 0;break-inside:avoid;background:#fff}
.source.cost{border-left-color:var(--kept)}
.source-main{display:table-cell;vertical-align:top}
.source-label{font-size:9pt;font-weight:700}
.source-value{font-size:15pt;font-weight:700;margin-top:.03in}
.source-value .per{font-size:9pt;font-weight:400;color:var(--muted)}
.share{display:table-cell;width:1.1in;text-align:right;vertical-align:middle;font-size:22pt;
  font-weight:700;color:var(--primary)}
.share span{display:block;font-size:7.5pt;font-weight:400;color:var(--muted);letter-spacing:.04em}
table.moves{border-collapse:collapse;margin-top:.05in;font-size:9pt}
table.moves td{padding:.02in .12in .02in 0}
table.moves td.num{width:1.05in;font-weight:700}
.caveat{font-size:8.5pt;color:var(--muted);margin-top:.06in}
.ramp-block{break-inside:avoid}
table.ramp{width:100%%;border-collapse:collapse;margin-top:.08in}
table.ramp th{text-align:left;width:1.1in;font-size:9pt;padding:.04in 0}
table.ramp td.track{padding:.04in .1in}
table.ramp td.num{width:2.6in;text-align:right;font-size:9pt;white-space:nowrap}
table.ramp tr.full th,table.ramp tr.full td{border-top:1px solid var(--line);padding-top:.07in}
.bar{height:.15in;border-radius:0 3px 3px 0;background:var(--now)}
.bar.neg{background:var(--kept)}
tr.full .bar.pos{background:var(--primary)}

/* Tables */
table.data{width:100%%;border-collapse:collapse;margin:.08in 0 .12in;font-size:9pt}
table.data th{font-size:7pt;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);
  text-align:left;padding:.05in .07in;border-bottom:1.5px solid var(--primary)}
table.data td{padding:.06in .07in;border-bottom:1px solid var(--line);vertical-align:top}
table.data.compact td{padding:.04in .05in}
table.data tr{break-inside:avoid}
table.data tr.total td{border-top:1.5px solid var(--ink);font-weight:700}
.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
th.num{text-align:right}
.chips{margin:.03in 0 .08in}
.chip{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:.02in .09in;
  margin:0 .04in .04in 0;font-size:8pt;font-weight:700;color:var(--ink);background:#fff}
.chip.plain{background:var(--soft)}
.chip.new{border-color:var(--pos);color:var(--pos)}
.chip.drop{border-color:var(--warn);color:var(--warn)}
.label{font-size:7pt;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);
  font-weight:700;margin:.1in 0 .03in}

/* Persona pages */
.persona-head{border-bottom:2px solid var(--line);padding-bottom:.1in;margin-bottom:.12in}
.persona-head h1{margin-bottom:.04in}
.persona-meta{color:var(--muted)}
.cols{display:table;width:100%%;table-layout:fixed}
.col{display:table-cell;vertical-align:top;padding-right:.25in}
.col:last-child{padding-right:0;padding-left:.1in}
.plan{font-size:15pt;font-weight:700;color:var(--primary);margin:.02in 0}
.impact{background:var(--soft);border-radius:6px;padding:.08in .18in .1in;margin:.12in 0;break-inside:avoid}
.impact h3{margin-top:.04in}
table.ledger{width:100%%;border-collapse:collapse;font-size:9.5pt}
table.ledger td{padding:.035in 0;border-bottom:1px solid var(--line)}
table.ledger tr.sub td{color:var(--muted);font-size:8.5pt;padding-left:.18in}
table.ledger tr.total td{border-top:1.5px solid var(--primary);border-bottom:none;font-weight:700;
  font-size:11pt;padding-top:.06in}
.capabilities{break-inside:avoid}
.narrative{border-left:3px solid var(--hl);padding:.02in .16in;margin:.04in 0 .14in;break-inside:avoid;font-size:9.5pt}
.narrative p{margin:.06in 0}

/* Bridge */
table.bridge{width:100%%;border-collapse:collapse;font-size:9pt;margin-top:.1in}
table.bridge th{font-size:8.5pt;text-align:right;padding:.05in .07in;border-bottom:1.5px solid var(--primary);
  vertical-align:bottom}
table.bridge td{padding:.05in .07in;border-bottom:1px solid var(--line)}
table.bridge tr.sub td{color:var(--muted);font-size:8.5pt}
table.bridge tr.sub td:first-child{padding-left:.25in}
table.bridge tr.group td{font-weight:700}
table.bridge tr.total td{border-top:2px solid var(--ink);border-bottom:none;font-weight:700;font-size:10.5pt}
table.bridge tr{break-inside:avoid}

/* Third-party */
table.stack{width:100%%;border-collapse:separate;border-spacing:2px 0;margin:.16in 0 .08in;height:.32in}
table.stack td{height:.32in;padding:0}
td.seg.now,.swatch.now{background:var(--now)} td.seg.move,.swatch.move{background:var(--move)}
td.seg.reduced,.swatch.reduced{background:var(--reduced)} td.seg.kept,.swatch.kept{background:var(--kept)}
td.seg:first-child{border-radius:3px 0 0 3px} td.seg:last-child{border-radius:0 3px 3px 0}
.legend{display:table;width:100%%;margin-bottom:.12in}
.legend-item{display:table-cell;font-size:9pt;padding-right:.1in;vertical-align:top}
.legend-item .swatch{display:inline-block;width:.12in;height:.12in;border-radius:2px;margin-right:.05in;
  vertical-align:top;margin-top:.02in;float:left}
.tag{display:inline-block;border-radius:3px;padding:.015in .07in;font-size:7.5pt;font-weight:700;
  letter-spacing:.04em;color:#fff}
.tag.retire{background:var(--now)} .tag.reduce{background:var(--reduced)} .tag.keep{background:var(--kept)}
ul.retired{margin:.04in 0;padding-left:.2in}

/* Assumptions */
ul.notes{padding-left:.18in;margin:.1in 0}
ul.notes li{margin:.05in 0}
.method-item{break-inside:avoid}
.method p{font-size:9pt;color:var(--ink)}

/* Next steps */
ol.steps{padding-left:.22in}
ol.steps li{margin:.08in 0}
.closing{margin-top:.22in;border-top:3px solid var(--hl);padding-top:.12in;font-size:10.5pt;break-inside:avoid}
.closing a{color:var(--accent)}

/* Footer */
footer.doc-footer{display:table;width:100%%;margin-top:.4in;border-top:1px solid var(--line);
  padding-top:.08in;font-size:7pt;color:var(--muted)}
.footer-logo{display:table-cell;width:1.1in;vertical-align:middle}
.footer-logo img{max-height:.22in;max-width:1in}
.footer-text{display:table-cell;vertical-align:middle}
.disclaimer{margin-top:.02in}
.disclaimer a{color:var(--muted)}

@page{size:Letter;margin:.6in .7in .95in;
  @bottom-left{content:element(footer);width:6.4in;vertical-align:top;padding-top:.15in}
  @bottom-right{content:counter(page);font-family:'TeX Gyre Heros',Helvetica,Arial,sans-serif;
    font-size:8pt;color:#5d6778;vertical-align:top;padding-top:.28in}}
@page cover{margin:0;@bottom-left{content:none}@bottom-right{content:none}}
@media print{
  main{max-width:none}
  footer.running-footer{position:running(footer);margin:0}
  footer.screen-footer{display:none}
  section{padding:0}
}
@media screen{
  body{background:#eef1f6}
  main{background:#fff;box-shadow:0 1px 6px rgba(0,0,0,.08)}
  section.cover{height:auto;padding:0}
  .cover-body{height:auto;padding:.5in .6in .5in}
  .cover-titles{margin-top:.5in}
  .cover-band{position:relative;height:2.2in}
  .cover-band-inner{left:.6in;bottom:.4in}
  section{padding:.25in .6in}
  section.page{border-top:8px solid #eef1f6}
  footer.running-footer{display:none}
  footer.screen-footer{padding:.2in .6in .3in}
}
@media screen and (max-width:720px){
  html{font-size:9.5pt}
  section,.cover-body,footer.screen-footer{padding-left:16px;padding-right:16px}
  section.cover{padding:0}
  .cover-band-inner{left:16px;right:16px;width:auto}
  .cover-logo{max-width:40vw}
  .cover-art{opacity:.5}
  .cover-title{font-size:30pt} .hero-figure{font-size:28pt}
  .cols,.col{display:block;padding:0}
  table.data,table.bridge{display:block;overflow-x:auto}
  .legend,.legend-item{display:block}
}
""" % {"primary": b["primary_color"], "accent": b["accent_color"], "hl": b["highlight_color"]}
