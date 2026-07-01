import React, { useEffect, useState } from 'react'
import { api, usd } from '../api'

// Best-bundle analysis for one persona: evaluate every candidate Microsoft
// bundle and rank by TCO. Prices are editable and re-rank on blur.
export default function BundleAnalysis({ engagement, persona, onApply, onClose }) {
  const base = `/api/engagements/${engagement.id}/personas/${persona.id}/bundle-analysis`
  const [data, setData] = useState(null)
  const [prices, setPrices] = useState(null)
  const [err, setErr] = useState('')

  async function run(p) {
    setErr('')
    try {
      const res = await api.post(base, { prices: p })
      setData(res)
      if (p == null) {
        const seed = {}
        res.bundles.forEach((b) => { seed[b.sku_reference] = b.target_unit_price_annual })
        setPrices(seed)
      }
    } catch (e) { setErr(e.message) }
  }
  useEffect(() => { run(null) }, [persona.id])

  const setPrice = (ref, val) => setPrices((p) => ({ ...p, [ref]: Number(val) }))

  return (
    <div className="card" style={{ borderColor: 'var(--accent)' }}>
      <div className="flex-between">
        <h2 style={{ margin: 0 }}>⚡ Best-bundle analysis · {persona.name} ({persona.headcount})</h2>
        <button className="ghost sm" onClick={onClose}>Close</button>
      </div>
      <p className="hint">Every candidate bundle evaluated as this persona's target.
        <b> Recommended</b> = highest annual savings with no capability lost. Bundles with
        gaps show higher savings but drop a required outcome — use those only if you
        reimagine what the persona truly requires. Prices are editable (annual $/seat).</p>
      {err && <div className="err">{err}</div>}

      {data && (
        <div className="muted" style={{ marginBottom: '.5rem', fontSize: '.8rem' }}>
          Required today (from current Microsoft licensing):{' '}
          {data.required_outcomes.length
            ? data.required_outcomes.map((o) => o.name).join(', ')
            : 'none recorded — add current licensing for gap detection'}
        </div>
      )}

      {data && (
        <table>
          <thead><tr>
            <th>Bundle</th><th className="num">$/seat/yr</th><th className="num">Target/yr</th>
            <th className="num">Δ TCO/yr</th><th>Positioning</th><th>Displaces</th>
            <th>Adds</th><th>Gaps</th><th></th>
          </tr></thead>
          <tbody>
            {data.bundles.map((b) => (
              <tr key={b.sku_reference}
                style={b.recommended ? { boxShadow: 'inset 3px 0 0 var(--accent)' } : {}}>
                <td>
                  <b>{b.sku_reference}</b>{' '}
                  {b.recommended && <span className="badge pos">Recommended</span>}
                </td>
                <td className="num">
                  <input type="number" style={{ width: 90 }}
                    value={prices?.[b.sku_reference] ?? b.target_unit_price_annual}
                    onChange={(e) => setPrice(b.sku_reference, e.target.value)}
                    onBlur={() => run(prices)} />
                </td>
                <td className="num">{usd(b.target_spend_annual)}</td>
                <td className={`num ${b.delta_annual >= 0 ? 'pos' : 'neg'}`}>{usd(b.delta_annual)}</td>
                <td style={{ fontSize: '.78rem' }}>{b.positioning}</td>
                <td style={{ fontSize: '.78rem' }}>
                  {b.displaced_products.length ? b.displaced_products.join(', ') : <span className="muted">—</span>}
                </td>
                <td><div className="pill-list">
                  {b.added_outcomes.map((o) => <span key={o} className="badge pos" title={o}>{short(o)}</span>)}
                  {!b.added_outcomes.length && <span className="muted">—</span>}
                </div></td>
                <td><div className="pill-list">
                  {b.gap_outcomes.map((o) => <span key={o} className="badge neg" title={o}>{short(o)}</span>)}
                  {!b.gap_outcomes.length && <span className="badge muted">none</span>}
                </div></td>
                <td className="num">
                  <button className="sm" onClick={() => onApply(b.sku_reference, prices?.[b.sku_reference] ?? b.target_unit_price_annual)}>
                    Use
                  </button>
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
