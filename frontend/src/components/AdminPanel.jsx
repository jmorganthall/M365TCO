import React, { useEffect, useRef, useState } from 'react'
import { api, pct } from '../api'

export default function AdminPanel({ onClose }) {
  const [secrets, setSecrets] = useState(null)
  const [catalog, setCatalog] = useState(null)
  const [defaults, setDefaults] = useState(null)
  const [ai, setAi] = useState({ enabled: false, model: '' })
  const [models, setModels] = useState([])
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const fileRef = useRef()
  const [version, setVersion] = useState('')

  function load() {
    api.get('/api/admin/secrets').then(setSecrets).catch((e) => setErr(e.message))
    api.get('/api/catalog/version').then(setCatalog).catch(() => {})
    api.get('/api/admin/defaults').then(setDefaults).catch(() => {})
    api.get('/api/admin/ai/status').then((s) => {
      setAi(s)
      if (s.enabled) api.get('/api/admin/ai/models').then((r) => setModels(r.models)).catch(() => {})
    }).catch(() => {})
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
  async function saveDefaults(patch) {
    setErr('')
    try { setDefaults(await api.put('/api/admin/defaults', patch)); setMsg('Defaults saved.') }
    catch (e) { setErr(e.message) }
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
          <h2>Defaults</h2>
          <p className="hint">System-wide defaults new engagements inherit. Changing these
            does not alter existing engagements.</p>
          {defaults && (
            <div className="toolbar">
              <div>
                <label>Global tooling split (e.g. 0.30 = 30%)</label>
                <input type="number" step="0.05" min="0" max="1"
                  defaultValue={defaults.default_tooling_pct}
                  onBlur={(e) => saveDefaults({ default_tooling_pct: Number(e.target.value) })} />
              </div>
              <div>
                <label>Default modeling horizon (years)</label>
                <input type="number" step="1" min="1"
                  defaultValue={defaults.default_modeling_horizon_years}
                  onBlur={(e) => saveDefaults({ default_modeling_horizon_years: Number(e.target.value) })} />
              </div>
              <span className="muted" style={{ alignSelf: 'flex-end', paddingBottom: '.5rem' }}>
                Current tooling split: {pct(defaults.default_tooling_pct)}
              </span>
            </div>
          )}
        </div>

        <div className="card">
          <h2>AI assist (OpenRouter)</h2>
          <p className="hint">Coverage suggestions are advisory and written as unratified
            entries. {ai.enabled
              ? <>Enabled. Current model: <b>{ai.model || '—'}</b>.</>
              : <b className="warn"> Set the OpenRouter API key below to enable and pick a model.</b>}</p>
          {ai.enabled && (
            <div className="toolbar">
              <div style={{ flex: 2 }}>
                <label>Model — type to filter (e.g. “gem”, “sonnet”)</label>
                <ModelCombobox models={models} value={ai.model}
                  onChange={(id) => saveDefaults({ openrouter_model: id }).then(() => setAi({ ...ai, model: id }))} />
              </div>
              <span className="muted" style={{ alignSelf: 'flex-end', paddingBottom: '.5rem' }}>
                {models.length} models available
              </span>
            </div>
          )}
        </div>

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

function ModelCombobox({ models, value, onChange }) {
  const [query, setQuery] = useState(value || '')
  const [open, setOpen] = useState(false)
  const q = query.trim().toLowerCase()
  const filtered = (q
    ? models.filter((m) => m.id.toLowerCase().includes(q) || (m.name || '').toLowerCase().includes(q))
    : models
  ).slice(0, 60)

  return (
    <div className="combo">
      <input
        value={query}
        placeholder="type to filter models…"
        onFocus={() => setOpen(true)}
        onChange={(e) => { setQuery(e.target.value); setOpen(true) }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && filtered.length > 0 && (
        <div className="combo-list">
          {filtered.map((m) => (
            <div
              key={m.id}
              className={`combo-item ${m.id === value ? 'sel' : ''}`}
              onMouseDown={() => { onChange(m.id); setQuery(m.id); setOpen(false) }}
            >
              <span className="combo-id">{m.id}</span>
              {m.name && m.name !== m.id && <span className="combo-name">{m.name}</span>}
            </div>
          ))}
        </div>
      )}
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
