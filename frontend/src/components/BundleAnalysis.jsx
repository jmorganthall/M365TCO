import React, { useEffect, useState } from 'react'
import { api, usd } from '../api'

// Prices are stored annualized; this modal shows/edits per-seat MONTHLY.
const toMonthly = (a) => (a ? Math.round((Number(a) / 12) * 100) / 100 : 0)
const toAnnual = (m) => Math.round(Number(m || 0) * 12 * 100) / 100

// Recommend-a-path for one persona: each row is a base bundle composed with the
// cheapest add-ons that close its capability gaps. Prices are per-seat monthly;
// editing the base price re-ranks. "Use" applies the base + its add-ons.
export default function BundleAnalysis({ engagement, persona, onApply, onClose }) {
  const url = `/api/engagements/${engagement.id}/personas/${persona.id}/bundle-analysis`
  const [data, setData] = useState(null)
  const [prices, setPrices] = useState(null)   // bundle name -> BASE price (annual)
  const [err, setErr] = useState('')

  async function run(p) {
    setErr('')
    try {
      const res = await api.post(url, { prices: p })
      setData(res)
      if (p == null) {
        const seed = {}
        res.bundles.forEach((b) => { seed[b.sku_reference] = b.base_price_annual })
        setPrices(seed)
      }
    } catch (e) { setErr(e.message) }
  }
  useEffect(() => { run(null) }, [persona.id])

  // Edit base price in monthly; store annual and re-rank on blur.
  const setBaseMonthly = (ref, monthly) =>
    setPrices((p) => ({ ...p, [ref]: toAnnual(monthly) }))

  return (
    <div className="card" style={{ borderColor: 'var(--accent)' }}>
      <div className="flex-between">
        <h2 style={{ margin: 0 }}>⚡ Recommend a path · {persona.name} ({persona.headcount})</h2>
        <button className="ghost sm" onClick={onClose}>Close</button>
      </div>
      <p className="hint">Each option is a <b>base bundle + the cheapest add-ons that close the gaps</b>
        &nbsp;(outcomes union, prices sum). <b>Recommended</b> = highest monthly savings with no
        capability lost, counting what their third-party tools deliver. Rows still showing gaps have an outcome no priced bundle/add-on covers — use those
        only if you reimagine what the persona truly requires. Base price is editable ($/seat/mo).</p>
      {err && <div className="err">{err}</div>}

      {data && (
        <div className="muted" style={{ marginBottom: '.5rem', fontSize: '.8rem' }}>
          Must keep (what they have today):{' '}
          {data.required_outcomes.length
            ? data.required_outcomes.map((o) => {
                const why = (o.sources || []).map((s) =>
                  s.startsWith('tool:') ? s.slice(5) : s === 'requirement' ? 'declared' : null).filter(Boolean)
                return why.length ? `${o.name} (${why.join(', ')})` : o.name
              }).join(', ')
            : 'none recorded — add current licensing for gap detection'}
          {data.kept_by_tool?.length > 0 && (
            <div>Kept by the tool (no priced plan or add-on delivers it):{' '}
              {data.kept_by_tool.map((o) => `${o.name} (${o.tools.join(', ')})`).join(', ')}</div>
          )}
        </div>
      )}

      {data?.seat_caps?.map((c) => (
        <div key={c.name} className="popcheck" style={{ marginBottom: '.5rem', fontSize: '.8rem' }}>
          <b>{c.name}:</b> the rest of the future state uses {c.consumed} of {c.cap} seats ·{' '}
          <b>{c.headroom}</b> left for {persona.name} ({persona.headcount}).
          {c.headroom === 0 && <span className="neg"> No Business seats left, so Business plans aren't recommended.</span>}
          {c.headroom > 0 && persona.headcount > c.headroom && (
            <span> If a Business plan is used, its row offers to split the {persona.headcount - c.headroom} over
              the cap onto the best other plan.</span>
          )}
        </div>
      ))}

      {data && (
        <table>
          <thead><tr>
            <th>Composed path</th><th className="num">Base $/mo</th><th className="num">+ Add-ons $/mo</th>
            <th className="num">Net $/seat/mo</th><th className="num">Δ TCO/mo</th>
            <th>Positioning</th><th>Displaces</th><th>Gaps</th><th></th>
          </tr></thead>
          <tbody>
            {data.bundles.map((b) => (
              <tr key={b.sku_reference}
                style={b.recommended ? { boxShadow: 'inset 3px 0 0 var(--accent)' } : {}}>
                <td>
                  <b>{b.sku_reference}</b>{' '}
                  {b.recommended && <span className="badge pos">Recommended</span>}
                  {b.cap_limited && (
                    <span className="badge neg" title="Other personas use every Business seat.">
                      No Business seats left
                    </span>
                  )}
                  {b.over_cap_by > 0 && (
                    <span className="badge"
                      title={`${b.cap_headroom} Business seats left; ${b.over_cap_by} of ${persona.headcount} would be over. After applying, split them on the row.`}>
                      {b.over_cap_by} over the Business cap
                    </span>
                  )}
                  {b.addons.length > 0 && (
                    <div className="pill-list" style={{ marginTop: 3 }}>
                      {b.addons.map((a) => (
                        <span key={a.bundle_id} className="badge muted"
                          title={`Closes: ${a.closes.join(', ')}`}>+ {a.name}</span>
                      ))}
                    </div>
                  )}
                </td>
                <td className="num">
                  <input type="number" style={{ width: 80 }}
                    value={toMonthly(prices?.[b.sku_reference] ?? b.base_price_annual)}
                    onChange={(e) => setBaseMonthly(b.sku_reference, e.target.value)}
                    onBlur={() => run(prices)} />
                </td>
                <td className="num">{b.addon_total_annual ? usd(b.addon_total_annual / 12) : <span className="muted">—</span>}</td>
                <td className="num">{usd(b.target_unit_price_annual / 12)}</td>
                <td className={`num ${b.delta_annual < 0 ? 'pos' : ''}`}>{usd(b.delta_annual / 12)}</td>
                <td style={{ fontSize: '.78rem' }}>{b.positioning}</td>
                <td style={{ fontSize: '.78rem' }}>
                  {b.displaced_products.length ? b.displaced_products.join(', ') : <span className="muted">—</span>}
                </td>
                <td><div className="pill-list">
                  {b.gap_outcomes.map((o) => <span key={o} className="badge neg" title={o}>{short(o)}</span>)}
                  {!b.gap_outcomes.length && <span className="badge muted">none</span>}
                </div></td>
                <td className="num">
                  <button className="sm" onClick={() => onApply({
                    sku_reference: b.sku_reference,
                    price: prices?.[b.sku_reference] ?? b.base_price_annual,
                    addons: b.addons.map((a) => ({ bundle_id: a.bundle_id, unit_price_annual: a.unit_price_annual })),
                  })}>Use</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {!data && !err && <p className="muted">Analyzing…</p>}
    </div>
  )
}

// Shorten long outcome names for compact chips.
function short(name) {
  return name.length > 22 ? name.split(/[/(]/)[0].trim().slice(0, 22) : name
}
