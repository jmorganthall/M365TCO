import React, { useEffect, useState } from 'react'
import { api } from '../api'
import Help from './Help.jsx'
import Scenarios from './Scenarios.jsx'
import PartlyReplaced from './PartlyReplaced.jsx'

// Step 4 — Future state per group (docs/WALKTHROUGH.md §3): fill in the
// recommended plan for every group without one, adjust plans, and answer the
// keep-or-retire question for tools only partly replaced.
export default function FutureState({ engagement, meta, moneyUnit }) {
  const [plansKey, setPlansKey] = useState(0)     // remount the plans after a fill-in
  const [changed, setChanged] = useState(0)       // refresh the keep-or-retire card
  return (
    <>
      <RecommendPlans engagement={engagement} changed={changed}
        onDone={() => { setPlansKey((k) => k + 1); setChanged((k) => k + 1) }} />
      <Scenarios key={plansKey} engagement={engagement} meta={meta} moneyUnit={moneyUnit}
        onChanged={() => setChanged((k) => k + 1)} />
      <PartlyReplaced engagement={engagement} refreshKey={changed} />
    </>
  )
}

// One click fills every group that has no plan with the recommender's choice: the
// lowest-cost plan (plus add-ons) that keeps everything the group has today. An
// explicit action, never automatic — and every plan stays editable.
function RecommendPlans({ engagement, changed, onDone }) {
  const eid = engagement.id
  const [missing, setMissing] = useState(null)
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    Promise.all([api.get(`/api/engagements/${eid}/personas`), api.get(`/api/engagements/${eid}/scenarios`)])
      .then(([ps, ss]) => {
        const has = new Set(ss.map((s) => s.persona_id))
        setMissing(ps.filter((p) => !has.has(p.id)))
      }).catch((e) => setErr(e.message))
  }, [eid, changed])

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
              ? 'no priced plan keeps everything this group has today — pick one with ⚡ below'
              : 'no Microsoft prices are loaded — load the price list in Settings, or pick a plan and price below' })
          continue
        }
        await api.post(`/api/engagements/${eid}/scenarios`, {
          persona_id: p.id, target_sku_reference: best.sku_reference,
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
            ? `${missing.length} group${missing.length === 1 ? ' has' : 's have'} no plan yet`
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
