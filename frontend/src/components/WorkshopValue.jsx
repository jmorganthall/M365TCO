import React, { useEffect, useState } from 'react'
import { api } from '../api'

// Workshop value (docs/TARGET_SCHEMA.md §7.2, D30): every delivered workshop rolled
// up into sales headlines. Built for screenshots, so it never names a customer; the
// server sends no names or ids. Every number is computed server-side — this page
// only formats them.

const BASIS_LABEL = { as_completed: 'As completed', today: 'If completed today' }
const WORD = { mean: 'average', median: 'typical' }

function money(n, currency) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency: currency || 'USD', maximumFractionDigits: 0,
  }).format(Number(n || 0))
}
const pct = (n) => `${Math.round(Number(n || 0) * 100)}%`
const count = (n) => new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 }).format(Number(n || 0))

function heroLine(h, currency) {
  const s = h.savings_per_customer
  const o = h.new_outcomes_per_customer
  const saves = s && `saves ${money(s.value, currency)} a year`
  const lights = o && `lights up ${count(o.value)} new outcome${o.value === 1 ? '' : 's'}`
  if (s && o && s.statistic === o.statistic) return `The ${WORD[s.statistic]} customer ${saves} and ${lights}.`
  return [s && `The ${WORD[s.statistic]} customer ${saves}.`, o && `The ${WORD[o.statistic]} customer ${lights}.`]
    .filter(Boolean).join(' ')
}

function Tile({ label, value, sub }) {
  return (
    <div className="value-tile">
      <div className="value-tile-label">{label}</div>
      <div className="value-tile-num">{value}</div>
      {sub && <div className="value-tile-sub">{sub}</div>}
    </div>
  )
}

// One anonymous dot per workshop that found savings: where its savings per user sit.
function Spread({ values, currency }) {
  if (values.length === 0) return null
  const lo = Math.min(...values), hi = Math.max(...values)
  const x = (v) => (hi === lo ? 50 : 4 + ((v - lo) / (hi - lo)) * 92)
  return (
    <div className="card">
      <h2>Savings per user per year, workshop by workshop</h2>
      <div className="value-spread" role="img"
        aria-label={`From ${money(lo, currency)} to ${money(hi, currency)} per user per year`}>
        <div className="value-spread-axis" />
        {values.map((v, i) => (
          <span key={i} className="value-spread-dot" style={{ left: `${x(v)}%` }}
            title={`${money(v, currency)} per user per year`} />
        ))}
      </div>
      <div className="flex-between"><small className="muted">{money(lo, currency)}</small>
        <small className="muted">{money(hi, currency)}</small></div>
    </div>
  )
}

function Ranking({ rows }) {
  const [all, setAll] = useState(false)
  if (rows.length === 0) return null
  const shown = all ? rows : rows.slice(0, 10)
  return (
    <div className="card">
      <h2>Most common new outcomes</h2>
      <p className="hint">Share of customers who lit up each outcome in their workshop.</p>
      {shown.map((r) => (
        <div key={r.key} className="value-rank">
          <div className="value-rank-name">{r.name}</div>
          <div className="value-rank-bar"><span style={{ width: `${r.share * 100}%` }} /></div>
          <div className="value-rank-num">{pct(r.share)}</div>
        </div>
      ))}
      {rows.length > 10 && (
        <button className="ghost sm" onClick={() => setAll((a) => !a)}>
          {all ? 'Show top 10' : `Show all ${rows.length}`}</button>
      )}
    </div>
  )
}

export default function WorkshopValue() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [basis, setBasis] = useState(null)

  useEffect(() => {
    api.get('/api/workshop-value').then((d) => { setData(d); setBasis(d.lead) })
      .catch((e) => setErr(e.message))
  }, [])

  if (err) return <div className="card"><div className="err">{err}</div></div>
  if (!data) return <div className="card"><p className="muted">Adding up delivered workshops…</p></div>

  if (!data.enough) {
    return (
      <>
        <div className="welcome"><h1>TCO workshop value</h1></div>
        <div className="card">
          <p style={{ margin: 0 }}>Headlines appear once at least {data.min_workshops} workshops are
            marked delivered — {data.delivered} so far.</p>
          <p className="hint" style={{ margin: '.4rem 0 0' }}>Mark a workshop delivered at the end of its
            Review step.</p>
        </div>
      </>
    )
  }

  const b = data.bases[basis]
  const other = data.bases[basis === 'today' ? 'as_completed' : 'today']
  const cur = data.currency
  const h = b.headline
  const t = b.totals
  const word = (x) => (x ? WORD[x.statistic] : '')
  const otherSaves = other.headline.savings_per_customer

  return (
    <>
      <div className="flex-between welcome">
        <h1>TCO workshop value</h1>
        <div className="unit-toggle">
          {Object.entries(BASIS_LABEL).map(([k, label]) => (
            <button key={k} className={basis === k ? 'on' : ''} onClick={() => setBasis(k)}>{label}</button>
          ))}
        </div>
      </div>

      <div className="card value-hero">
        <div className="value-hero-line">{heroLine(h, cur) || 'No delivered workshop has found savings yet.'}</div>
        {otherSaves && (
          <div className="muted">{BASIS_LABEL[basis === 'today' ? 'as_completed' : 'today']}:{' '}
            {money(otherSaves.value, cur)} a year for the {word(otherSaves)} customer</div>
        )}
      </div>

      <div className="value-tiles">
        {h.savings_per_user && <Tile label={`Per user, ${word(h.savings_per_user)} customer`}
          value={money(h.savings_per_user.value, cur)} sub="saved per user per year" />}
        {h.savings_pct && <Tile label={`Of current spend, ${word(h.savings_pct)} customer`}
          value={pct(h.savings_pct.value)} sub="Microsoft and third-party spend saved" />}
        <Tile label="Duplicate spend" value={money(t.duplicate_spend, cur)}
          sub={t.duplicate_share != null ? `${pct(t.duplicate_share)} of savings · tools Microsoft already covers` : 'tools Microsoft already covers'} />
        <Tile label="Optimizing into Microsoft" value={money(t.optimization, cur)}
          sub={t.optimization_share != null ? `${pct(t.optimization_share)} of savings · consolidation and unused seats` : 'consolidation and unused seats'} />
      </div>

      <div className="value-tiles">
        <Tile label="Savings identified" value={money(t.savings, cur)} sub={t.savings_pct != null ? `a year · ${pct(t.savings_pct)} of ${money(t.current_spend, cur)} spend` : 'a year'} />
        <Tile label="Workshops delivered" value={count(data.delivered)} />
        <Tile label="Users" value={count(t.users)} />
        <Tile label="Personas" value={count(t.personas)} />
        <Tile label="New outcomes lit up" value={count(t.new_outcomes)} />
      </div>

      <Ranking rows={b.outcome_ranking} />
      <Spread values={b.savings_per_user_spread} currency={cur} />

      <p className="value-footnote">
        {BASIS_LABEL[basis]}. Savings figures are based on the {b.saving_workshops} of {b.workshops} delivered
        workshops that found net savings; outcome, user and persona figures use all {b.workshops}.
        “Average” is the mean, “typical” the median — each figure shows whichever is higher.
        Yearly run rate once every contract has renewed. As of {data.as_of}.
        {data.mixed_currencies && ` Workshops use more than one currency; amounts are not converted and are shown in ${cur}.`}
      </p>
    </>
  )
}
