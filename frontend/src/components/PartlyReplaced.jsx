import React, { useEffect, useState } from 'react'
import { api, usd } from '../api'
import Help from './Help.jsx'

// Step 4's keep-or-retire question (docs/WALKTHROUGH.md §3): a tool the
// recommended plans replace for only some of its users. "Keep" records an
// intended residual; "Retire" forces full elimination and needs a reason, printed
// on the report. Unanswered, the tool is assumed kept (its remaining cost stays).
// The same decision the Summary's dispositions table edits — one stored answer.
export default function PartlyReplaced({ engagement, refreshKey }) {
  const eid = engagement.id
  const [rows, setRows] = useState(null)
  const [outcomeName, setOutcomeName] = useState({})
  const [err, setErr] = useState('')
  const load = () => api.post(`/api/engagements/${eid}/compute`)
    .then((r) => setRows(r.dispositions.filter((d) =>
      d.disposition === 'PartiallyReduced' || d.requires_residual_classification
      || d.override === 'ForceFullElimination')))
    .catch((e) => setErr(e.message))
  useEffect(() => { load() }, [eid, refreshKey])
  useEffect(() => {
    api.get(`/api/engagements/${eid}/outcomes`)
      .then((os) => setOutcomeName(Object.fromEntries(os.map((o) => [o.id, o.name])))).catch(() => {})
  }, [eid])

  async function decide(d, payload) {
    setErr('')
    try { await api.put(`/api/engagements/${eid}/dispositions/${d.third_party_product_id}/override`, payload); load() }
    catch (e) { setErr(e.message) }
  }

  if (!rows || rows.length === 0) return null
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Partly replaced tools <Help k="future.keep_or_retire" /></h2>
      <p className="hint">The recommended plans replace these tools for some of their users only.</p>
      {err && <div className="err">{err}</div>}
      {rows.map((d) => <Decision key={d.third_party_product_id} d={d} onDecide={decide} outcomeName={outcomeName} />)}
    </div>
  )
}

// Suggested reason when the person confirms over-licensing — editable, and theirs
// once saved (printed on the report like any retirement reason).
const OVERLICENSED_REASON = 'Over-licensed: the extra seats belong to no one in this analysis'

function Decision({ d, onDecide, outcomeName }) {
  const current = d.override === 'ForceFullElimination' ? 'retire'
    : d.residual_intent === 'IntendedOutOfScope' ? 'keep' : ''
  const [mode, setMode] = useState(current)
  const [reason, setReason] = useState(d.override_reason || '')
  useEffect(() => { setMode(current); setReason(d.override_reason || '') }, [current, d.override_reason])
  // A forced retirement zeroes the engine's residual, so count the people the
  // plans actually leave uncovered (covered − displaced) from the population.
  const forced = d.override === 'ForceFullElimination'
  const uncovered = Math.max((d.covered_count || 0) - (d.displaced_users || 0), 0)
  const personaLeft = (d.population || []).filter((p) => p.status !== 'Replaced')
    .reduce((t, p) => t + p.headcount, 0)
  const held = (d.covered_count || 0) - (d.unassigned_count || 0)
  // Every uncovered seat is one the tool counts beyond the personas holding it:
  // the question is whether our headcount is right (the tool is over-licensed),
  // not whether to retire it from people who still need it.
  const overOnly = personaLeft === 0 && (d.unassigned_count || 0) > 0 && uncovered > 0
  const cost = usd(uncovered * (d.per_unit_annual_cost || 0))
  const choose = (m) => {
    setMode(m)
    if (m === 'retire' && overOnly && !reason.trim()) setReason(OVERLICENSED_REASON)
  }
  const apply = () => {
    if (mode === 'keep') onDecide(d, { override: 'None', residual_intent: 'IntendedOutOfScope' })
    else if (mode === 'retire' && reason.trim()) onDecide(d, { override: 'ForceFullElimination', override_reason: reason.trim() })
    else if (mode === '') onDecide(d, { override: 'None', residual_intent: 'None' })
  }
  const dirty = mode !== current || (mode === 'retire' && reason.trim() !== (d.override_reason || ''))
  return (
    <div className="review-item partly-item">
      <div className="grow">
        <b>{d.third_party_product_name}</b>{' '}
        {overOnly ? (
          <span className="muted">— licensed for {d.covered_count}; the personas holding it total {held}.{' '}
            {forced ? `The extra ${uncovered} are treated as over-licensing (${cost}/yr counted as saved).`
              : `${uncovered} seats sit beyond our headcount (${cost}/yr).`}</span>
        ) : forced ? (
          <span className="muted">— {uncovered} of {d.covered_count} people aren't covered by their plan;
            retired anyway ({cost}/yr counted as saved)</span>
        ) : (
          <span className="muted">— still used by {d.residual_count} of {d.covered_count} people ({usd(d.residual_annual_cost)}/yr)</span>
        )}
        {!current && <span className="badge warn" style={{ marginLeft: 6 }}>not answered — assumed kept</span>}
        {overOnly && (
          <div className="partly-question">Is our headcount right, and the tool simply over-licensed?</div>
        )}
        <div className="toolbar" style={{ marginTop: '.35rem', gap: '.4rem', alignItems: 'flex-end' }}>
          <select value={mode} onChange={(e) => choose(e.target.value)} style={{ minWidth: 230 }}>
            <option value="">Not answered (assumed kept)</option>
            {overOnly ? <>
              <option value="retire">Yes — headcount is right; the extra {uncovered} seats go with the tool</option>
              <option value="keep">No — more people use it; keep paying for the extra {uncovered}</option>
            </> : <>
              <option value="keep">Keep it for the remaining users</option>
              <option value="retire">Retire it entirely</option>
            </>}
          </select>
          {mode === 'retire' && (
            <input style={{ flex: 2 }} value={reason} placeholder="Why it can go (printed on the report)"
              onChange={(e) => setReason(e.target.value)} />
          )}
          {dirty && <button className="sm" disabled={mode === 'retire' && !reason.trim()} onClick={apply}>Save</button>}
        </div>
        {overOnly && mode === 'keep' && (
          <div className="hint" style={{ marginTop: '.3rem' }}>If those people matter to the plan, add them as a
            persona (or correct the tool's user count on Other tools) so the analysis covers them.</div>
        )}
      </div>
      <WhereFrom d={d} uncovered={uncovered} outcomeName={outcomeName} />
    </div>
  )
}

// Why each persona's people are or aren't covered (engine Section 6.4a).
const STATUS_REASON = {
  OutOfScope: () => 'plan set out of scope',
  NoPlan: () => 'no plan chosen yet',
}

// The second column: where the uncovered people come from, persona by persona,
// plus the people the tool counts beyond its personas. The rows add up exactly to
// the uncovered count (covered − displaced); the replaced personas sit underneath.
function WhereFrom({ d, uncovered, outcomeName }) {
  const pop = d.population || []
  const replaced = pop.filter((p) => p.status === 'Replaced')
  const left = pop.filter((p) => p.status !== 'Replaced' && p.headcount > 0)
  const extra = d.unassigned_count || 0
  const replacedCount = replaced.reduce((t, p) => t + p.headcount, 0)
  const reason = (p) => (p.status === 'PlanGap'
    ? `plan doesn't cover ${(p.missing_outcome_ids || []).map((id) => outcomeName[id] || id).join(', ') || 'what the tool does'}`
    : STATUS_REASON[p.status]())
  const total = Math.max(d.covered_count || 0, 1)
  return (
    <div className="where-from">
      <div className="where-from-head">Where the {uncovered} come from</div>
      <div className="where-bar" title={`${replacedCount} replaced · ${uncovered} not covered`}>
        <span className="seg replaced" style={{ width: `${Math.min(100, (replacedCount / total) * 100)}%` }} />
        <span className="seg left" style={{ width: `${Math.min(100, (uncovered / total) * 100)}%` }} />
      </div>
      <ul className="where-list">
        {left.map((p) => (
          <li key={p.persona_id}>
            <span className="n">{p.headcount}</span>
            <span><b>{p.persona_name}</b> <span className="muted">— {reason(p)}</span></span>
          </li>
        ))}
        {extra !== 0 && (
          <li>
            <span className="n">{extra > 0 ? extra : `−${-extra}`}</span>
            <span>{extra > 0
              ? <><b>Licensed beyond our headcount</b> <span className="muted">— the tool is licensed for {d.covered_count}; the personas holding it total {d.covered_count - extra}</span></>
              : <><b>Licensed below our headcount</b> <span className="muted">— the tool is licensed for {d.covered_count}; the personas holding it total {d.covered_count - extra}</span></>}
            </span>
          </li>
        )}
        {!left.length && extra === 0 && <li className="muted">Every persona holding it is replaced.</li>}
      </ul>
      {replaced.length > 0 && (
        <div className="where-replaced muted">
          {replacedCount} replaced by their new plan: {replaced.map((p) => `${p.persona_name} (${p.headcount})`).join(' · ')}
        </div>
      )}
    </div>
  )
}
