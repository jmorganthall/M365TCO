import React, { useEffect, useState } from 'react'
import { api } from '../api'
import Help from './Help.jsx'

// A carve-out is the same people as its parent, so it should hold the same
// licences and tools. Tags added to one side after the carve don't reach the
// other, and the recommender then plans the carve-out without them. This card
// lists every carve-out that no longer matches; "Match parent" copies the
// parent's tags onto it. Nothing changes until someone clicks.
export default function CarveOutDrift({ engagement, refreshKey, onMatched }) {
  const eid = engagement.id
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')
  const load = () => api.get(`/api/engagements/${eid}/carve-out-drift`)
    .then(setRows).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [eid, refreshKey])

  async function match(r) {
    setErr('')
    try {
      await api.post(`/api/engagements/${eid}/personas/${r.persona_id}/match-parent`, {})
      load(); onMatched && onMatched()
    } catch (e) { setErr(e.message) }
  }

  if (rows.length === 0 && !err) return null
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Carve-outs that differ from their parent <Help k="groups.carve_out_drift" /></h2>
      {err && <div className="err">{err}</div>}
      {rows.map((r) => (
        <div key={r.persona_id} className="toolbar" style={{ alignItems: 'center', marginBottom: '.4rem' }}>
          <div style={{ flex: 3 }}>
            <strong>{r.persona_name}</strong> <span className="muted">vs {r.parent_name}</span>
            <div className="hint">
              {[...r.missing_tools, ...r.missing_licenses].length > 0 &&
                <>Missing: {[...r.missing_tools, ...r.missing_licenses].join(', ')}. </>}
              {[...r.extra_tools, ...r.extra_licenses].length > 0 &&
                <>Only here: {[...r.extra_tools, ...r.extra_licenses].join(', ')}.</>}
            </div>
          </div>
          <button className="sm" onClick={() => match(r)}>Match parent</button>
        </div>
      ))}
    </div>
  )
}
