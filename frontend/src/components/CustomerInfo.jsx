import React, { useEffect, useState } from 'react'
import { api } from '../api'

// Engagement metadata — the editable engagement/customer name plus basic customer
// context (workshop date, industry, HQ, website, size). Fields live on the
// Engagement (§4.1) and are user-entered; they display here and later ground the
// AI business-narrative research. Committed incrementally on blur, like the rest
// of the workshop tool.
export default function CustomerInfo({ engagement, onUpdate }) {
  const eid = engagement.id
  const [f, setF] = useState(fromEngagement(engagement))
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [savedAt, setSavedAt] = useState('')
  const [aiEnabled, setAiEnabled] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => { setF(fromEngagement(engagement)); setMsg('') }, [eid])
  useEffect(() => { api.get('/api/admin/ai/status').then((s) => setAiEnabled(s.enabled)).catch(() => {}) }, [])

  // AI research: fill the fields the operator hasn't provided from whatever is
  // known (name + anything entered). Advisory — fills EMPTY fields only, never
  // overwrites operator input; each filled field is then saved for review.
  async function research() {
    setBusy(true); setErr(''); setMsg('')
    try {
      const res = await api.post(`/api/admin/engagements/${eid}/ai/research-customer`, {
        customer_name: f.customer_name, hq_location: f.hq_location, website: f.website,
        industry: f.industry, employee_count: f.employee_count === '' ? null : Number(f.employee_count),
      })
      const s = res.suggestions || {}
      const proposed = {
        industry: s.industry, hq_location: s.hq_location, website: s.website,
        employee_count: s.employee_count, notes: s.description,
      }
      const next = { ...f }
      const filled = []
      for (const [field, val] of Object.entries(proposed)) {
        if (val == null || val === '' || (next[field] ?? '') !== '') continue  // fill empty only
        next[field] = String(val); filled.push(field)
      }
      setF(next)
      for (const field of filled) await commit(field, next[field])
      setMsg(filled.length
        ? `AI filled ${filled.length} empty field(s) — please verify before relying on them.`
        : 'AI had nothing confident to add for the empty fields.')
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  async function commit(field, raw) {
    // Normalize empties: text → "", number/date → null (so "clear" really clears).
    let value = raw
    if (field === 'employee_count') value = raw === '' || raw === null ? null : Number(raw)
    else if (field === 'workshop_date') value = raw || null
    if ((engagement[field] ?? '') === (value ?? '')) return  // no-op if unchanged
    setErr('')
    try {
      const updated = await api.patch(`/api/engagements/${eid}`, { [field]: value })
      onUpdate?.(updated)
      setSavedAt(field); setTimeout(() => setSavedAt(''), 1200)
    } catch (e) { setErr(e.message) }
  }

  const set = (field) => (e) => setF({ ...f, [field]: e.target.value })
  const savedTag = (field) => savedAt === field ? <span className="badge pos" style={{ marginLeft: 6 }}>saved</span> : null

  return (
    <div className="card">
      <div className="flex-between">
        <h2 style={{ margin: 0 }}>Customer info</h2>
        {aiEnabled && (
          <button className="ghost sm" onClick={research} disabled={busy || !f.customer_name.trim()}
            title="Use AI to fill the empty fields from the customer name (and anything you've entered)">
            {busy ? 'Researching…' : '✨ AI research'}
          </button>
        )}
      </div>
      <p className="hint">Basic context about this engagement's customer. The <b>name</b> is the
        engagement's display name (shown in the sidebar and header); the rest is background used for
        display and, later, to ground the AI business-narrative research. Saved as you go.
        {aiEnabled && <> Enter a name, then <b>✨ AI research</b> proposes the rest for you to verify.</>}</p>
      {err && <div className="err">{err}</div>}
      {msg && <div className="popcheck" style={{ margin: '.4rem 0' }}>{msg}</div>}

      <div className="grid c2">
        <div>
          <label>Customer / engagement name {savedTag('customer_name')}</label>
          <input value={f.customer_name} placeholder="Acme Corporation"
            onChange={set('customer_name')} onBlur={(e) => commit('customer_name', e.target.value)} />
        </div>
        <div>
          <label>Workshop date {savedTag('workshop_date')}</label>
          <input type="date" value={f.workshop_date}
            onChange={(e) => { setF({ ...f, workshop_date: e.target.value }); commit('workshop_date', e.target.value) }} />
        </div>
        <div>
          <label>Industry {savedTag('industry')}</label>
          <input value={f.industry} placeholder="e.g. Manufacturing, Healthcare, Legal"
            onChange={set('industry')} onBlur={(e) => commit('industry', e.target.value)} />
        </div>
        <div>
          <label>HQ location {savedTag('hq_location')}</label>
          <input value={f.hq_location} placeholder="City, State/Country"
            onChange={set('hq_location')} onBlur={(e) => commit('hq_location', e.target.value)} />
        </div>
        <div>
          <label>Website {savedTag('website')}</label>
          <input value={f.website} placeholder="acme.com"
            onChange={set('website')} onBlur={(e) => commit('website', e.target.value)} />
        </div>
        <div>
          <label>Employee count {savedTag('employee_count')}</label>
          <input type="number" min="0" value={f.employee_count} placeholder="e.g. 1200"
            onChange={set('employee_count')} onBlur={(e) => commit('employee_count', e.target.value)} />
        </div>
      </div>

      <div style={{ marginTop: '.6rem' }}>
        <label>Notes {savedTag('notes')}</label>
        <textarea rows={3} value={f.notes} placeholder="Anything worth remembering about this customer or engagement…"
          onChange={set('notes')} onBlur={(e) => commit('notes', e.target.value)} />
      </div>
    </div>
  )
}

function fromEngagement(e) {
  return {
    customer_name: e.customer_name || '',
    workshop_date: e.workshop_date || '',
    industry: e.industry || '',
    hq_location: e.hq_location || '',
    website: e.website || '',
    employee_count: e.employee_count ?? '',
    notes: e.notes || '',
  }
}
