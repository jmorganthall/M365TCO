import React, { useState } from 'react'
import { api } from '../api'

// The name last used to mark a workshop delivered — a per-browser convenience so
// the AE doesn't retype it; the record itself is on the engagement.
const NAME_KEY = 'tco-delivered-by'
const loadName = () => { try { return localStorage.getItem(NAME_KEY) || '' } catch (e) { return '' } }
const saveName = (n) => { try { localStorage.setItem(NAME_KEY, n) } catch (e) {} }

// Workshop delivered, at the end of Summary & PDF (TARGET_SCHEMA §4.1, D30): who and
// when, plus a frozen copy of the results. Delivered workshops feed the Workshop value page (no names shown).
export default function DeliveredCard({ engagement, onUpdate }) {
  const eid = engagement.id
  const delivered = !!engagement.delivered_at
  const [name, setName] = useState(loadName)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function toggle() {
    setBusy(true); setErr('')
    try {
      const next = delivered
        ? await api.del(`/api/engagements/${eid}/delivered`)
        : await api.post(`/api/engagements/${eid}/delivered`, { delivered_by: name })
      if (!delivered) saveName(name.trim())
      onUpdate?.(next)
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Workshop delivered</h2>
      <p className="hint">Tick this once the workshop has been run with the customer. It records who and
        when, and freezes today's results. Delivered workshops are added up, without names, on the
        Workshop value page.</p>
      <div style={{ display: 'flex', alignItems: 'center', gap: '.6rem', flexWrap: 'wrap' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: '.4rem' }}>
          <input type="checkbox" checked={delivered} disabled={busy || (!delivered && !name.trim())}
            onChange={toggle} />
          <b>Workshop delivered</b>
        </label>
        {delivered
          ? <span className="muted">on {new Date(engagement.delivered_at + 'Z').toLocaleDateString()} by {engagement.delivered_by}</span>
          : <input placeholder="Your name" value={name} onChange={(e) => setName(e.target.value)}
              style={{ maxWidth: 220 }} />}
      </div>
      {err && <div className="err">{err}</div>}
    </div>
  )
}
