import React, { useEffect, useState } from 'react'
import { api, useDataVersion, useInterpreted } from '../api'
import Help from './Help.jsx'
import Scenarios from './Scenarios.jsx'
import ExtraSeats from './ExtraSeats.jsx'
import { MdrPanel, Sections, ServicePicker } from './PracticeServices.jsx'
import CarveOutDrift from './CarveOutDrift.jsx'
import { PlanTable, WhatsNew } from './PlanCards.jsx'

// Step 4 — Future state per persona (docs/WALKTHROUGH.md §3): fill in the
// recommended plan for every persona without one, adjust plans, and answer the
// seat-mismatch question for tools the plans replace (GUIDED_PLAN G4).
export default function FutureState({ engagement, meta, moneyUnit }) {
  // Every card refreshes after any write (api.js data version) — no card has to
  // tell another that something changed.
  const changed = useDataVersion()
  return (
    <>
      <CarveOutDrift engagement={engagement} refreshKey={changed} />
      <RecommendPlans engagement={engagement} />
      <PlanTable engagement={engagement} />
      <Scenarios engagement={engagement} meta={meta} moneyUnit={moneyUnit} />
      <WhatsNew engagement={engagement} />
      <ExtraSeats engagement={engagement} moneyUnit={moneyUnit} />
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Practice services</h2>
        <p className="hint">The practice's own services in each group's future state. Their outcomes count in what the
          move replaces; their price is part of the future cost, shown in its own section.</p>
        <ServicePicker engagement={engagement} moneyUnit={moneyUnit} />
        <Sections engagement={engagement} />
        <MdrPanel engagement={engagement} />
      </div>
    </>
  )
}

// One click fills every persona that has no plan with the recommender's choice: the
// lowest-cost plan (plus add-ons) that keeps everything the persona has today; a
// persona and its carve-outs decide each tool together (TARGET_SCHEMA D29). An
// explicit action, never automatic — and every plan stays editable.
export function RecommendPlans({ engagement, onDone }) {
  const eid = engagement.id
  const [missing, setMissing] = useState(null)
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState(null)
  const [err, setErr] = useState('')

  // Personas with no plan: the interpreter's `group_without_plan` finding (the same
  // one Review shows), refetched after every write.
  const interp = useInterpreted(eid)
  useEffect(() => {
    if (!interp.data) return
    setMissing(interp.findings.filter((f) => f.code === 'group_without_plan')
      .map((f) => ({ id: f.persona_id, name: f.facts.name })))
  }, [interp.data])

  async function fill() {
    setBusy(true); setErr(''); setReport(null)
    const done = []
    try {
      for (const p of missing) {
        const res = await api.post(`/api/engagements/${eid}/personas/${p.id}/bundle-analysis`, { prices: null })
        const bundles = res.bundles || []
        const best = bundles.find((b) => b.recommended)
        if (!best) {
          done.push({ name: p.name, plan: null,
            why: bundles.some((b) => b.price_known)
              ? 'no priced plan keeps everything this persona has today — pick one with ⚡ below'
              : 'no Microsoft prices are loaded — load the price list in Settings, or pick a plan and price below' })
          continue
        }
        await api.post(`/api/engagements/${eid}/scenarios`, {
          persona_id: p.id, target_sku_reference: best.sku_reference, bundle_id: best.base_bundle_id,
          target_unit_price_annual: best.base_price_annual, in_scope: true,
          addons: (best.addons || []).map((a) => ({ bundle_id: a.bundle_id, unit_price_annual: a.unit_price_annual })),
        })
        done.push({ name: p.name, plan: [best.sku_reference, ...(best.addons || []).map((a) => a.name || '')].filter(Boolean).join(' + ') })
      }
      setReport(done)
      onDone?.()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  if (!missing || (missing.length === 0 && !report)) return null
  return (
    <div className="card">
      <div className="flex-between">
        <div>
          <b>{missing.length
            ? `${missing.length} persona${missing.length === 1 ? ' has' : 's have'} no plan yet`
            : 'Recommended plans filled in'}</b> <Help k="future.recommend" />
        </div>
        {missing.length > 0 && (
          <button onClick={fill} disabled={busy}>{busy ? 'Working…' : 'Fill in recommended plans'}</button>
        )}
      </div>
      {err && <div className="err">{err}</div>}
      {report && (
        <ul style={{ margin: '.4rem 0 0', paddingLeft: '1.1rem' }}>
          {report.map((r) => (
            <li key={r.name}>{r.name}: {r.plan
              ? <b>{r.plan}</b>
              : <span className="warn">{r.why}</span>}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
