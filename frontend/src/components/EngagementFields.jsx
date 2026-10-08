import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { EngagementBasisEditor } from './basis.jsx'
import Help from './Help.jsx'

// The engagement's own fields, placed where the walkthrough asks for them
// (docs/WALKTHROUGH.md §3): the customer on step 1, the Microsoft agreement on
// step 2, the horizon, partner funding and report colours on step 7. Every field
// saves as you go; empties are stored as "not given" (NULL), never a default.

function useEngagementFields(engagement, onUpdate) {
  const [f, setF] = useState(fromEngagement(engagement))
  const [err, setErr] = useState('')
  const [savedAt, setSavedAt] = useState('')
  useEffect(() => setF(fromEngagement(engagement)), [engagement.id])

  async function commit(field, raw) {
    let value = raw
    if (field === 'employee_count') value = raw === '' || raw == null ? null : Number(raw)
    else if (field === 'modeling_horizon_years') value = Math.min(10, Math.max(1, Number(raw) || 3))
    else if (field === 'ecif_roi_conservative') value = raw === '' || raw == null ? 10 : Math.max(1, Number(raw) || 10)
    else if (field === 'ecif_roi_generous') value = raw === '' || raw == null ? 5 : Math.max(1, Number(raw) || 5)
    else if (field === 'workshop_date' || field === 'microsoft_renewal_date') value = raw || null
    if ((engagement[field] ?? '') === (value ?? '')) return
    setErr('')
    try {
      const updated = await api.patch(`/api/engagements/${engagement.id}`, { [field]: value })
      onUpdate?.(updated)
      setSavedAt(field); setTimeout(() => setSavedAt(''), 1200)
    } catch (e) { setErr(e.message) }
  }
  const set = (field) => (e) => setF((x) => ({ ...x, [field]: e.target.value }))
  const saved = (field) => savedAt === field ? <span className="badge pos" style={{ marginLeft: 6 }}>saved</span> : null
  return { f, setF, err, setErr, commit, set, saved }
}

function fromEngagement(e) {
  return {
    customer_name: e.customer_name || '',
    workshop_date: e.workshop_date || '',
    microsoft_renewal_date: e.microsoft_renewal_date || '',
    industry: e.industry || '',
    hq_location: e.hq_location || '',
    website: e.website || '',
    employee_count: e.employee_count ?? '',
    notes: e.notes || '',
    modeling_horizon_years: e.modeling_horizon_years ?? 3,
    managed_ms_account: !!e.managed_ms_account,
    ecif_roi_conservative: Number(e.ecif_roi_conservative ?? 10),
    ecif_roi_generous: Number(e.ecif_roi_generous ?? 5),
    brand_primary_color: e.brand_primary_color || '',
    brand_accent_color: e.brand_accent_color || '',
  }
}

// ---- Step 1: Customer -------------------------------------------------------
export function CustomerCard({ engagement, meta, onUpdate }) {
  const { f, setF, err, setErr, commit, set, saved } = useEngagementFields(engagement, onUpdate)
  const [aiEnabled, setAiEnabled] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  useEffect(() => {
    api.get('/api/admin/ai/status').then((s) => setAiEnabled(s.enabled)).catch(() => {})
  }, [])

  // Optional AI enrichment: fills EMPTY fields only, never overwrites what a
  // person typed; each filled field is saved for review.
  async function research() {
    setBusy(true); setErr(''); setMsg('')
    try {
      const res = await api.post(`/api/admin/engagements/${engagement.id}/ai/research-customer`, {
        customer_name: f.customer_name, hq_location: f.hq_location, website: f.website,
        industry: f.industry, employee_count: f.employee_count === '' ? null : Number(f.employee_count),
      })
      const s = res.suggestions || {}
      const proposed = { industry: s.industry, hq_location: s.hq_location, website: s.website,
        employee_count: s.employee_count, notes: s.description }
      const next = { ...f }
      const filled = []
      for (const [field, val] of Object.entries(proposed)) {
        if (val == null || val === '' || (next[field] ?? '') !== '') continue
        next[field] = String(val); filled.push(field)
      }
      setF(next)
      for (const field of filled) await commit(field, next[field])
      setMsg(filled.length ? `AI filled ${filled.length} empty field(s) — please check them.`
        : 'AI had nothing confident to add.')
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  function onLogoFile(file) {
    if (!file) return
    if (!file.type.startsWith('image/')) { setErr('The logo must be an image (PNG or JPG).'); return }
    const reader = new FileReader()
    reader.onload = async () => {
      try { onUpdate?.(await api.patch(`/api/engagements/${engagement.id}`, { brand_logo_data_url: reader.result })) }
      catch (e) { setErr(e.message) }
    }
    reader.readAsDataURL(file)
  }
  async function clearLogo() {
    try { onUpdate?.(await api.patch(`/api/engagements/${engagement.id}`, { brand_logo_data_url: '' })) }
    catch (e) { setErr(e.message) }
  }

  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Customer</h2>
      {err && <div className="err">{err}</div>}
      {msg && <div className="popcheck" style={{ margin: '.4rem 0' }}>{msg}</div>}
      <div className="grid c3">
        <div>
          <label>Customer name <Help k="customer.name" /> {saved('customer_name')}</label>
          <input value={f.customer_name} placeholder="Customer name" autoComplete="off"
            onChange={set('customer_name')} onBlur={(e) => commit('customer_name', e.target.value)} />
        </div>
        <div>
          <label>Workshop date <Help k="customer.workshop_date" /> {saved('workshop_date')}</label>
          <input type="date" value={f.workshop_date}
            onChange={(e) => { setF({ ...f, workshop_date: e.target.value }); commit('workshop_date', e.target.value) }} />
        </div>
        <div>
          <label>Logo <Help k="customer.logo" /></label>
          {engagement.brand_logo_data_url
            ? <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
                <img src={engagement.brand_logo_data_url} alt="Customer logo" style={{ maxHeight: 34, maxWidth: 140 }} />
                <button className="ghost sm" onClick={clearLogo}>Remove</button></div>
            : <input type="file" accept="image/png,image/jpeg" onChange={(e) => onLogoFile(e.target.files?.[0])} />}
        </div>
      </div>

      <details className="more">
        <summary>More about the customer (optional) <Help k="customer.details" /></summary>
        {aiEnabled && (
          <button className="ghost sm" onClick={research} disabled={busy || !f.customer_name.trim()}
            title="Fill the empty fields from public information (optional)" style={{ marginBottom: '.5rem' }}>
            {busy ? 'Researching…' : '✨ AI research'}</button>
        )}
        <div className="grid c2">
          <div><label>Industry {saved('industry')}</label>
            <input value={f.industry} placeholder="e.g. Manufacturing" onChange={set('industry')}
              onBlur={(e) => commit('industry', e.target.value)} /></div>
          <div><label>Headquarters {saved('hq_location')}</label>
            <input value={f.hq_location} placeholder="City, country" onChange={set('hq_location')}
              onBlur={(e) => commit('hq_location', e.target.value)} /></div>
          <div><label>Website {saved('website')}</label>
            <input value={f.website} onChange={set('website')} onBlur={(e) => commit('website', e.target.value)} /></div>
          <div><label>Employees {saved('employee_count')}</label>
            <input type="number" min="0" value={f.employee_count} onChange={set('employee_count')}
              onBlur={(e) => commit('employee_count', e.target.value)} />
            <small className="src">Used to check that the groups add up.</small></div>
        </div>
        <div style={{ marginTop: '.5rem' }}>
          <label>Notes {saved('notes')}</label>
          <textarea rows={3} value={f.notes} onChange={set('notes')} onBlur={(e) => commit('notes', e.target.value)} />
        </div>
      </details>

      <details className="more">
        <summary>Pricing basis <Help k="customer.pricing_basis" /></summary>
        <p className="hint" style={{ margin: '0 0 .5rem' }}>Prices are in <b>{engagement.market}/{engagement.currency}</b>,
          the loaded price list's market and currency. Licence lines and plans can override these per line.</p>
        <EngagementBasisEditor engagement={engagement} meta={meta} onUpdate={onUpdate} onError={setErr} />
      </details>
    </div>
  )
}

// ---- Step 2: the Microsoft agreement ----------------------------------------
export function AgreementCard({ engagement, onUpdate }) {
  const { f, setF, err, commit, saved } = useEngagementFields(engagement, onUpdate)
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Microsoft agreement</h2>
      {err && <div className="err">{err}</div>}
      <div className="grid c3">
        <div>
          <label>Renews on <Help k="agreement.renewal" /> {saved('microsoft_renewal_date')}</label>
          <input type="date" value={f.microsoft_renewal_date}
            onChange={(e) => { setF({ ...f, microsoft_renewal_date: e.target.value }); commit('microsoft_renewal_date', e.target.value) }} />
          {!f.microsoft_renewal_date && <small className="src warn">Not given — assumed one year after the workshop.</small>}
        </div>
      </div>
    </div>
  )
}

// ---- Step 7: horizon, the PDF, and the report's optional settings ----------
export function SummaryCard({ engagement, onUpdate, onPdfCreated }) {
  const { f, setF, err, setErr, commit, set, saved } = useEngagementFields(engagement, onUpdate)
  const [making, setMaking] = useState(false)
  const [presented, setPresented] = useState(null)
  const loadPresented = () => api.get(`/api/engagements/${engagement.id}/snapshots`)
    .then((rows) => setPresented(rows.find((r) => r.is_baseline) || null)).catch(() => {})
  useEffect(() => { loadPresented() }, [engagement.id, engagement.presented_snapshot_id])

  async function createPdf() {
    setMaking(true); setErr('')
    try {
      const res = await fetch(`/api/engagements/${engagement.id}/customer-report.pdf`, { method: 'POST' })
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText)
      const blob = await res.blob()
      const name = /filename="([^"]+)"/.exec(res.headers.get('content-disposition') || '')?.[1] || 'M365-TCO.pdf'
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 2000)
      onUpdate?.(await api.get(`/api/engagements/${engagement.id}`))
      onPdfCreated?.()
    } catch (e) { setErr(e.message) } finally { setMaking(false) }
  }

  return (
    <div className="card">
      <div className="flex-between">
        <h2 style={{ margin: 0 }}>Customer report</h2>
        <button onClick={createPdf} disabled={making}>{making ? 'Creating…' : 'Create customer PDF'}</button>
      </div>
      <p className="hint" style={{ margin: '.3rem 0 .6rem' }}>
        Title page, an overview of every group, a page per group and how we calculated it. <Help k="summary.pdf" />
        {presented && <> Last created <b>{new Date(presented.created_at).toLocaleString()}</b> — those are the
          numbers the customer was handed.</>}
      </p>
      {err && <div className="err">{err}</div>}
      <div className="grid c3">
        <div>
          <label>Years to model <Help k="summary.horizon" /> {saved('modeling_horizon_years')}</label>
          <input type="number" min="1" max="10" value={f.modeling_horizon_years} onChange={set('modeling_horizon_years')}
            onBlur={(e) => commit('modeling_horizon_years', e.target.value)} />
        </div>
      </div>
      <details className="more">
        <summary>Report options</summary>
        <div className="grid c3">
          <div><label>Title colour</label>
            <input type="color" value={f.brand_primary_color || '#1a1f3c'}
              onChange={(e) => { setF({ ...f, brand_primary_color: e.target.value }); commit('brand_primary_color', e.target.value) }} /></div>
          <div><label>Accent colour (HTML readout)</label>
            <input type="color" value={f.brand_accent_color || '#2563eb'}
              onChange={(e) => { setF({ ...f, brand_accent_color: e.target.value }); commit('brand_accent_color', e.target.value) }} /></div>
        </div>
        <label className="check" style={{ marginTop: '.6rem' }}>
          <input type="checkbox" checked={f.managed_ms_account}
            onChange={(e) => { setF({ ...f, managed_ms_account: e.target.checked }); commit('managed_ms_account', e.target.checked) }} />
          Microsoft account team assigned <Help k="summary.ecif" /> {saved('managed_ms_account')}
        </label>
        {f.managed_ms_account && (
          <div className="grid c2" style={{ marginTop: '.4rem' }}>
            <div><label>Co-funding ratio — conservative (N:1)</label>
              <input type="number" min="1" step="0.5" value={f.ecif_roi_conservative} onChange={set('ecif_roi_conservative')}
                onBlur={(e) => commit('ecif_roi_conservative', e.target.value)} /></div>
            <div><label>Co-funding ratio — generous (N:1)</label>
              <input type="number" min="1" step="0.5" value={f.ecif_roi_generous} onChange={set('ecif_roi_generous')}
                onBlur={(e) => commit('ecif_roi_generous', e.target.value)} /></div>
          </div>
        )}
      </details>
    </div>
  )
}
