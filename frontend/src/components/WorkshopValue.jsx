import React, { useEffect, useState } from 'react'
import { api } from '../api'

// Workshop value (docs/TARGET_SCHEMA.md §7.2, D30): every delivered workshop rolled
// up into sales headlines. Built for screenshots, so it never names a customer; the
// server sends no names or ids. Every number is computed server-side — this page
// only formats them. Money is over 36 months (the yearly run rate × 3).

const BASIS_LABEL = { as_completed: 'As completed', today: 'If completed today' }
const WORD = { mean: 'average', median: 'typical' }
const PCT_LABEL = {
  mean: 'The average customer saves',
  median: 'The typical customer saves',
  overall: 'Saved across all customers',
}

function money(n, currency) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency: currency || 'USD', maximumFractionDigits: 0,
  }).format(Number(n || 0))
}
const pct = (n) => `${Math.round(Number(n || 0) * 100)}%`
const count = (n) => new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 }).format(Number(n || 0))

function heroLine(h, currency, months) {
  const s = h.savings_per_customer
  const o = h.new_outcomes_per_customer
  const saves = s && `saves ${money(s.value, currency)} over ${months} months`
  const lights = o && `lights up ${count(o.value)} new outcome${o.value === 1 ? '' : 's'}`
  if (s && o && s.statistic === o.statistic) return `The ${WORD[s.statistic]} customer ${saves} and ${lights}.`
  return [s && `The ${WORD[s.statistic]} customer ${saves}.`, o && `The ${WORD[o.statistic]} customer ${lights}.`]
    .filter(Boolean).join(' ')
}

function Kpi({ icon, label, value, sub, tone }) {
  return (
    <div className={`vd-kpi ${tone}`}>
      <span className="vd-icon" aria-hidden="true">{icon}</span>
      <div className="vd-kpi-num">{value}</div>
      <div className="vd-kpi-label">{label}</div>
      {sub && <div className="vd-sub">{sub}</div>}
    </div>
  )
}

// Where the savings come from: duplicate spend vs optimizing into Microsoft, as a
// ring with the total in the middle. Both slices are labelled with money and share
// beside the ring, so colour never carries the meaning alone.
function SavingsSplit({ t, currency, months }) {
  const parts = [
    { key: 'dup', label: 'Duplicate spend', note: 'tools Microsoft already covers',
      value: t.duplicate_spend, share: t.duplicate_share },
    { key: 'opt', label: 'Optimizing into Microsoft', note: 'consolidation and unused seats',
      value: t.optimization, share: t.optimization_share },
  ]
  const ring = parts.every((x) => x.share != null && x.share >= 0)
  const R = 54, C = 2 * Math.PI * R, GAP = 3
  let offset = 0
  return (
    <div className="vd-card vd-split">
      <h3>Where the savings come from</h3>
      <div className="vd-split-body">
        {ring && (
          <svg viewBox="0 0 140 140" className="vd-donut" role="img"
            aria-label={parts.map((x) => `${x.label} ${pct(x.share)}`).join(', ')}>
            <circle cx="70" cy="70" r={R} className="vd-donut-track" />
            {parts.map((x) => {
              const len = Math.max(0, x.share * C - (x.share > 0 && x.share < 1 ? GAP : 0))
              const el = (
                <circle key={x.key} cx="70" cy="70" r={R} className={`vd-donut-seg ${x.key}`}
                  strokeDasharray={`${len} ${C - len}`} strokeDashoffset={-offset}
                  transform="rotate(-90 70 70)">
                  <title>{`${x.label}: ${money(x.value, currency)} (${pct(x.share)})`}</title>
                </circle>
              )
              offset += x.share * C
              return el
            })}
            <text x="70" y="66" className="vd-donut-num">{money(t.savings, currency)}</text>
            <text x="70" y="84" className="vd-donut-cap">over {months} months</text>
          </svg>
        )}
        <div className="vd-legend">
          {parts.map((x) => (
            <div key={x.key} className="vd-legend-row">
              <span className={`vd-swatch ${x.key}`} />
              <div>
                <div className="vd-legend-label">{x.label}</div>
                <div className="vd-legend-num">{money(x.value, currency)}
                  {x.share != null && <span className="vd-legend-pct"> · {pct(x.share)}</span>}</div>
                <div className="vd-sub">{x.note}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function Reach({ rows }) {
  return (
    <div className="vd-card vd-reach">
      <h3>Across the workshops</h3>
      {rows.map(([label, value]) => (
        <div key={label} className="vd-reach-row"><span>{label}</span><b>{value}</b></div>
      ))}
    </div>
  )
}

// One anonymous dot per workshop that found savings: where its savings per user sit.
function Spread({ values, currency, months }) {
  if (values.length === 0) return null
  const lo = Math.min(...values), hi = Math.max(...values)
  const x = (v) => (hi === lo ? 50 : 4 + ((v - lo) / (hi - lo)) * 92)
  const unit = `per user over ${months} months`
  return (
    <div className="vd-card">
      <h3>Savings {unit}, workshop by workshop</h3>
      <div className="value-spread" role="img"
        aria-label={`From ${money(lo, currency)} to ${money(hi, currency)} ${unit}`}>
        <div className="value-spread-axis" />
        {values.map((v, i) => (
          <span key={i} className="value-spread-dot" style={{ left: `${x(v)}%` }}
            title={`${money(v, currency)} ${unit}`} />
        ))}
      </div>
      <div className="flex-between"><small className="muted">{money(lo, currency)}</small>
        <small className="muted">{money(hi, currency)}</small></div>
    </div>
  )
}

// Every outcome lit up, with its share of all new outcomes lit up (adds up to 100%).
function OutcomesLit({ rows, total }) {
  const [all, setAll] = useState(false)
  if (rows.length === 0) return null
  const shown = all ? rows : rows.slice(0, 10)
  // Bars are scaled to the most common outcome; the exact share is printed beside each.
  const top = rows[0].share || 1
  return (
    <div className="vd-card vd-outcomes">
      <h3>New outcomes lit up</h3>
      <p className="vd-sub" style={{ marginTop: 0 }}>Each outcome's share of the {count(total)} new outcomes lit up.</p>
      {shown.map((r) => (
        <div key={r.key} className="value-rank">
          <div className="value-rank-name">{r.name}</div>
          <div className="value-rank-bar"><span style={{ width: `${(r.share / top) * 100}%` }} /></div>
          <div className="value-rank-num">{pct(r.share)} <small className="muted">({r.count})</small></div>
        </div>
      ))}
      {rows.length > 10 && (
        <button className="ghost sm" onClick={() => setAll((a) => !a)}>
          {all ? 'Show top 10' : `Show all ${rows.length}`}</button>
      )}
    </div>
  )
}

// `compact` is the home page's version: everything but the spread, which is on the
// full page.
export default function WorkshopValue({ compact = false, onOpenFull }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [basis, setBasis] = useState(null)

  useEffect(() => {
    api.get('/api/workshop-value').then((d) => { setData(d); setBasis(d.lead) })
      .catch((e) => setErr(e.message))
  }, [])

  if (err) return <div className="card"><div className="err">{err}</div></div>
  if (!data) return <div className="card"><p className="muted">Adding up delivered workshops…</p></div>

  if (data.delivered === 0) {
    return (
      <>
        <div className="welcome"><h2 style={{ margin: 0 }}>TCO workshop value</h2></div>
        <div className="card">
          <p style={{ margin: 0 }}>No workshops are marked delivered yet. Mark one delivered at the end of
            its Summary &amp; PDF step and its results are added up here, without names.</p>
        </div>
      </>
    )
  }

  const b = data.bases[basis]
  const otherKey = basis === 'today' ? 'as_completed' : 'today'
  const other = data.bases[otherKey]
  const cur = data.currency
  const months = data.months
  const h = b.headline
  const t = b.totals
  const word = (x) => (x ? WORD[x.statistic] : '')

  return (
    <div className="vd">
      <div className="vd-head">
        {compact ? <h2>TCO workshop value</h2> : <h1>TCO workshop value</h1>}
        <div className="vd-head-actions">
          <div className="unit-toggle">
            {Object.entries(BASIS_LABEL).map(([k, label]) => (
              <button key={k} className={basis === k ? 'on' : ''} onClick={() => setBasis(k)}>{label}</button>
            ))}
          </div>
          {compact && onOpenFull && <button className="ghost sm" onClick={onOpenFull}>Full details ›</button>}
        </div>
      </div>

      <div className="vd-kpis">
        <Kpi tone="warm" icon="$" label={`Savings identified over ${months} months`} value={money(t.savings, cur)}
          sub={`${money(t.savings_annual, cur)} a year · ${count(b.saving_workshops)} customers`} />
        {h.savings_pct && <Kpi tone="berry" icon="%" label={PCT_LABEL[h.savings_pct.statistic]}
          value={pct(h.savings_pct.value)} sub="of current Microsoft and third-party spend" />}
        <Kpi tone="ocean" icon="✦" label="New outcomes lit up" value={count(t.new_outcomes)}
          sub={`across ${count(b.workshops)} workshops`} />
      </div>

      <div className="vd-row">
        <div className="vd-card vd-hero">
          <div className="vd-hero-line">{heroLine(h, cur, months) || 'No delivered workshop has found savings yet.'}</div>
          <div className="vd-minis">
            {h.savings_per_customer && (
              <div><div className="vd-mini-num">{money(h.savings_per_customer.value, cur)}</div>
                <div className="vd-sub">per {word(h.savings_per_customer)} customer over {months} months</div></div>
            )}
            {h.savings_per_user && (
              <div><div className="vd-mini-num">{money(h.savings_per_user.value, cur)}</div>
                <div className="vd-sub">per user, {word(h.savings_per_user)} customer, over {months} months</div></div>
            )}
            {h.new_outcomes_per_customer && (
              <div><div className="vd-mini-num">{count(h.new_outcomes_per_customer.value)}</div>
                <div className="vd-sub">new outcomes, {word(h.new_outcomes_per_customer)} customer</div></div>
            )}
          </div>
          {other.totals.savings > 0 && (
            <div className="vd-sub">{BASIS_LABEL[otherKey]}: {money(other.totals.savings, cur)} identified
              over {months} months</div>
          )}
        </div>
        <SavingsSplit t={t} currency={cur} months={months} />
      </div>

      <div className="vd-row">
        <OutcomesLit rows={b.outcomes_lit} total={t.new_outcomes} />
        <Reach rows={[
          ['Workshops delivered', count(data.delivered)],
          ['Users', count(t.users)],
          ['Personas', count(t.personas)],
          [`Current spend over ${months} months`, money(t.current_spend, cur)],
          [`Optimized spend over ${months} months`, money(t.optimized_spend, cur)],
        ]} />
      </div>

      {!compact && <Spread values={b.savings_per_user_spread} currency={cur} months={months} />}

      <p className="value-footnote">
        {BASIS_LABEL[basis]}. Savings figures are based on the {b.saving_workshops} of {b.workshops} delivered
        workshops that found net savings; outcome, user and persona figures use all {b.workshops}.
        Current and optimized spend are for the customers that found savings.
        Money is over {months} months: the yearly run rate once every contract has renewed, × {months / 12}.
        “Average” is the mean, “typical” the median — each figure shows whichever is higher.
        As of {data.as_of}.
        {data.mixed_currencies && ` Workshops use more than one currency; amounts are not converted and are shown in ${cur}.`}
      </p>
    </div>
  )
}
