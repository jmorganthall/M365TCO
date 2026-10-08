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
  const [err, setErr] = useState('')
  const load = () => api.post(`/api/engagements/${eid}/compute`)
    .then((r) => setRows(r.dispositions.filter((d) =>
      d.disposition === 'PartiallyReduced' || d.requires_residual_classification
      || d.override === 'ForceFullElimination')))
    .catch((e) => setErr(e.message))
  useEffect(() => { load() }, [eid, refreshKey])

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
      {rows.map((d) => <Decision key={d.third_party_product_id} d={d} onDecide={decide} />)}
    </div>
  )
}

function Decision({ d, onDecide }) {
  const current = d.override === 'ForceFullElimination' ? 'retire'
    : d.residual_intent === 'IntendedOutOfScope' ? 'keep' : ''
  const [mode, setMode] = useState(current)
  const [reason, setReason] = useState(d.override_reason || '')
  useEffect(() => { setMode(current); setReason(d.override_reason || '') }, [current, d.override_reason])
  const apply = () => {
    if (mode === 'keep') onDecide(d, { override: 'None', residual_intent: 'IntendedOutOfScope' })
    else if (mode === 'retire' && reason.trim()) onDecide(d, { override: 'ForceFullElimination', override_reason: reason.trim() })
    else if (mode === '') onDecide(d, { override: 'None', residual_intent: 'None' })
  }
  const dirty = mode !== current || (mode === 'retire' && reason.trim() !== (d.override_reason || ''))
  return (
    <div className="review-item">
      <div className="grow">
        <b>{d.third_party_product_name}</b>{' '}
        <span className="muted">— still used by {d.residual_count} of {d.covered_count} people ({usd(d.residual_annual_cost)}/yr)</span>
        {!current && <span className="badge warn" style={{ marginLeft: 6 }}>not answered — assumed kept</span>}
        <div className="toolbar" style={{ marginTop: '.35rem', gap: '.4rem', alignItems: 'flex-end' }}>
          <select value={mode} onChange={(e) => setMode(e.target.value)} style={{ minWidth: 230 }}>
            <option value="">Not answered (assumed kept)</option>
            <option value="keep">Keep it for the remaining users</option>
            <option value="retire">Retire it entirely</option>
          </select>
          {mode === 'retire' && (
            <input style={{ flex: 2 }} value={reason} placeholder="Why it can go (printed on the report)"
              onChange={(e) => setReason(e.target.value)} />
          )}
          {dirty && <button className="sm" disabled={mode === 'retire' && !reason.trim()} onClick={apply}>Save</button>}
        </div>
      </div>
    </div>
  )
}
