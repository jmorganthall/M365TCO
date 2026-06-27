import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api'

export default function AdminPanel({ onClose }) {
  const [secrets, setSecrets] = useState(null)
  const [catalog, setCatalog] = useState(null)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const fileRef = useRef()
  const [version, setVersion] = useState('')

  function load() {
    api.get('/api/admin/secrets').then(setSecrets).catch((e) => setErr(e.message))
    api.get('/api/catalog/version').then(setCatalog).catch(() => {})
  }
  useEffect(() => { load() }, [])

  async function saveSecret(key, value) {
    if (!value) return
    try { await api.put('/api/admin/secrets', { key, value }); setMsg(`Saved ${key}`); load() }
    catch (e) { setErr(e.message) }
  }
  async function clearSecret(key) {
    try { await api.del(`/api/admin/secrets/${key}`); load() } catch (e) { setErr(e.message) }
  }
  async function importCsv() {
    const f = fileRef.current?.files?.[0]
    if (!f) { setErr('Choose a price-sheet CSV first.'); return }
    setErr(''); setMsg('Importing…')
    try {
      const res = await api.uploadCsv('/api/catalog/import-csv', f, { catalog_version: version })
      setMsg(`Imported: ${res.inserted} new, ${res.updated} updated, ${res.skipped} skipped.`)
      load()
    } catch (e) { setErr(e.message); setMsg('') }
  }
  async function refreshPC() {
    setErr(''); setMsg('Pulling from Partner Center…')
    try {
      const res = await api.post('/api/catalog/refresh-partner-center')
      setMsg(res.status === 'no-change' ? res.detail : `Imported ${res.inserted} new, ${res.updated} updated.`)
      load()
    } catch (e) { setErr(e.message); setMsg('') }
  }

  return (
    <div style={overlay} onClick={onClose}>
      <div style={panel} onClick={(e) => e.stopPropagation()}>
        <div className="flex-between">
          <h2 style={{ margin: 0 }}>Settings</h2>
          <button className="ghost sm" onClick={onClose}>Close</button>
        </div>
        {msg && <div className="muted" style={{ margin: '.4rem 0' }}>{msg}</div>}
        {err && <div className="err">{err}</div>}

        <div className="card">
          <h2>Microsoft SKU catalog</h2>
          <p className="hint">Day-one path: import the new-commerce license-based price list
            CSV from the Partner Center Pricing workspace. Phase-two: automated Partner Center
            pull (requires operator consent + stored refresh token).</p>
          <div className="muted">Current catalog: <b>{catalog?.catalog_version || 'none'}</b> · {catalog?.sku_count || 0} SKUs</div>
          <div className="toolbar" style={{ marginTop: '.6rem' }}>
            <div style={{ flex: 2 }}>
              <label>Price-sheet CSV</label>
              <input type="file" accept=".csv" ref={fileRef} />
            </div>
            <div>
              <label>Catalog version</label>
              <input value={version} placeholder="2026-06" onChange={(e) => setVersion(e.target.value)} />
            </div>
            <button onClick={importCsv}>Import CSV</button>
          </div>
          <div style={{ marginTop: '.6rem' }}>
            <button className="ghost sm" disabled={!catalog?.partner_center_configured} onClick={refreshPC}>
              {catalog?.partner_center_configured ? 'Refresh from Partner Center API' : 'Partner Center not configured'}
            </button>
          </div>
        </div>

        <div className="card">
          <h2>Secrets</h2>
          <p className="hint">Stored encrypted-at-rest, keyed by the operator master secret.
            Values are write-only — never read back. {secrets && !secrets.store_enabled &&
              <b className="warn"> Store disabled: set TCO_MASTER_SECRET to enable.</b>}</p>
          {secrets?.secrets.map((s) => (
            <SecretRow key={s.key} s={s} disabled={!secrets.store_enabled}
              onSave={saveSecret} onClear={clearSecret} />
          ))}
        </div>
      </div>
    </div>
  )
}

function SecretRow({ s, onSave, onClear, disabled }) {
  const [val, setVal] = useState('')
  return (
    <div className="toolbar" style={{ marginBottom: '.5rem' }}>
      <div style={{ flex: 2 }}>
        <label>{s.label} {s.set ? <span className="badge pos">set</span> : <span className="badge muted">not set</span>}</label>
        <input type="password" value={val} disabled={disabled} placeholder={s.set ? '•••••• (replace)' : 'enter value'}
          onChange={(e) => setVal(e.target.value)} />
      </div>
      <button className="sm" disabled={disabled || !val} onClick={() => { onSave(s.key, val); setVal('') }}>Save</button>
      {s.set && <button className="danger sm" disabled={disabled} onClick={() => onClear(s.key)}>Clear</button>}
    </div>
  )
}

const overlay = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,.6)', display: 'flex',
  alignItems: 'flex-start', justifyContent: 'center', padding: '3rem 1rem', overflow: 'auto', zIndex: 50,
}
const panel = { background: 'var(--bg)', borderRadius: 12, padding: '1.2rem', width: 'min(820px, 100%)', border: '1px solid var(--border)' }
