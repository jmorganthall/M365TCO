import React, { useEffect, useState } from 'react'
import { api, usd, useDataVersion } from '../api'
import { useHelpText } from './Help.jsx'

// How a persona's numbers were built (docs/LAYER_AUDIT.md Phase 4): each figure the
// readout shows, then — per part — the DERIVED values it came from (the
// interpreter, read-only) and the ENTERED values behind those, with a link to the
// step where they are edited. Every figure's parts add up to it (the backend
// guarantees it; nothing is added up here).

const LABELS = {
  annual_cost: 'Annual cost', effective_price_annual: 'Price per seat / yr', price_source: 'Price from',
  applies_to: 'Applies to', applies_to_assumed: 'Untagged (counted for every persona)', share: 'This persona’s share',
  read_state: 'How the line is read', credited_people: 'People credited', cost_per_person: 'Cost per person / yr',
  effective_annual_cost: 'Counted cost / yr', tooling_pct: 'Software share', tooling_source: 'Software share from',
  people_covered: 'People covered', covered_source: 'People covered from', redundant_today_annual: 'Already covered today / yr',
  headcount: 'People', net_annual: 'Net per seat / yr', composed_list_annual: 'List (base + add-ons) / yr',
  pct_off_list: 'Below list', plan: 'Plan', plan_source: 'Plan from', add_ons: 'Add-ons', in_scope: 'In scope',
  sku_reference: 'Licence', quantity_assigned: 'Seats assigned', list_price_annual: 'List price / seat / yr',
  price_override: 'Negotiated price', overridden_price_annual: 'Negotiated price / seat / yr', personas: 'Personas',
  name: 'Tool', raw_cost: 'Cost as entered', cost_period: 'Per', is_managed: 'Managed service',
  covered_count_override: 'People covered (typed)', target_sku_reference: 'Plan as typed', picked_plan: 'Plan picked',
  add_on_list_prices: 'Add-on list prices / yr', discount_pct: 'Discount',
}
const MONEY = /annual|cost|price|value/i
const PCT = /pct|pct_off/i

function show(k, v) {
  if (v === null || v === undefined || v === '') return <span className="muted">—</span>
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (Array.isArray(v)) return v.length ? v.join(', ') : <span className="muted">none</span>
  if (typeof v === 'object') return Object.entries(v).map(([a, b]) => `${a} ${usd(b)}`).join(' · ') || '—'
  if (typeof v === 'number' && PCT.test(k)) return `${Math.round(v * 1000) / 10}%`
  if (typeof v === 'number' && MONEY.test(k)) return usd(v)
  if (typeof v === 'number') return v.toLocaleString('en-US')
  return String(v)
}

function Facts({ title, facts, badge }) {
  return (
    <div className="trace-facts">
      <div className="trace-facts-head">{title} {badge}</div>
      <table><tbody>
        {Object.entries(facts).filter(([k]) => k !== 'id' && k !== 'step').map(([k, v]) => (
          <tr key={k}><td className="muted">{LABELS[k] || k}</td><td>{show(k, v)}</td></tr>
        ))}
      </tbody></table>
    </div>
  )
}

export default function TracePanel({ engagement, personaId, onClose }) {
  const eid = engagement.id
  const version = useDataVersion()
  const help = useHelpText()
  const [t, setT] = useState(null)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState({})
  useEffect(() => {
    api.get(`/api/engagements/${eid}/trace/personas/${personaId}`).then(setT).catch((e) => setErr(e.message))
  }, [eid, personaId, version])
  const stepTitle = (k) => help?.steps?.[k]?.title || k
  const toggle = (k) => setOpen((o) => ({ ...o, [k]: !o[k] }))

  return (
    <div className="trace-overlay" onClick={onClose}>
      <div className="trace-panel card" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="How these numbers were built">
        <div className="flex-between">
          <h2 style={{ margin: 0 }}>How {t ? t.persona.name : 'this persona'}’s numbers were built</h2>
          <button className="ghost sm" onClick={onClose}>Close</button>
        </div>
        <p className="hint">Each figure, the derived values it came from, and what was entered. Derived values
          are worked out from the entries and can’t be edited; change an entry on its step.</p>
        {err && <div className="err">{err}</div>}
        {!t && !err && <p className="muted">Loading…</p>}
        {t && t.figures.map((f) => (
          <div key={f.key} className="trace-figure">
            <div className="flex-between"><b>{f.label}</b><b>{usd(f.value)}/yr</b></div>
            <div className="src">{f.rule}</div>
            {f.parts.map((p, i) => {
              const k = `${f.key}:${i}`
              return (
                <div key={k} className="trace-part">
                  <button className="ghost sm trace-part-head" onClick={() => toggle(k)}>
                    <span>{open[k] ? '▾' : '▸'} {p.label}</span><span>{usd(p.value)}</span>
                  </button>
                  {open[k] && (
                    <div className="trace-layers">
                      <Facts title="Derived" badge={<span className="badge muted">read-only</span>} facts={p.interpreted} />
                      <Facts title="Entered" facts={p.raw}
                        badge={<a className="sm" href={`#/e/${eid}/${p.raw.step}`} onClick={onClose}>Edit on {stepTitle(p.raw.step)} ›</a>} />
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        ))}
        {t && t.headline_items.length > 0 && (
          <div className="trace-figure">
            <b>When it counts in the headline</b>
            <table className="data"><tbody>
              {t.headline_items.map((h, i) => (
                <tr key={i}><td>{h.label}</td><td className="num">{usd(h.annual_amount)}/yr</td>
                  <td className="muted">{h.date_missing ? 'no date — from day one' : `from ${h.timed_by || 'day one'}`}</td>
                  <td className="num">{usd(h.amount_over_horizon)} over the horizon</td></tr>
              ))}
            </tbody></table>
          </div>
        )}
      </div>
    </div>
  )
}
