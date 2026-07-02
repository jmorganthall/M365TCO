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

        <DefaultOutcomes onMsg={setMsg} onErr={setErr} />

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

        <PricingSync onMsg={setMsg} onErr={setErr} />

        <div className="card">
          <h2>Microsoft SKU catalog</h2>
          <p className="hint">Import the new-commerce license-based price list CSV from the
            Partner Center Pricing workspace. For automated acquisition, use <b>Pricing
            sync</b> above (CSP refresh token), then "Import latest into catalog".</p>
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

function refreshTokenScript(cfg) {
  const appId = cfg?.client_id || '<CLIENT_ID>'
  const tenant = cfg?.tenant_id || '<TENANT_ID>'
  return `# One-time: install the module (current user)
Install-Module PartnerCenter -Scope CurrentUser -Force

$appId    = "${appId}"
$tenantId = "${tenant}"

# App secret (skip these two lines if you use a certificate — see note below)
$secret = ConvertTo-SecureString "<APP_SECRET>" -AsPlainText -Force
$cred   = New-Object System.Management.Automation.PSCredential($appId, $secret)

# Opens a browser for interactive MFA consent with the service account:
$token = New-PartnerAccessToken -ApplicationId $appId -Credential $cred \`
  -Scopes "https://api.partner.microsoft.com/user_impersonation" \`
  -Tenant $tenantId -UseAuthorizationCode

# Copy this value and paste it into "Refresh token" above:
$token.RefreshToken`
}

function PricingSync({ onMsg, onErr }) {
  const [s, setS] = useState(null)
  const [cfg, setCfg] = useState(null)
  const [cred, setCred] = useState({ kind: 'certificate', value: '' })
  const [rt, setRt] = useState('')
  const [busy, setBusy] = useState(false)
  const [showHelp, setShowHelp] = useState(false)
  const certFile = useRef()

  function load() {
    api.get('/api/pricesync/status').then(setS).catch(() => {})
    api.get('/api/pricesync/config').then(setCfg).catch(() => {})
  }
  useEffect(() => { load() }, [])
  const setField = (k, v) => setCfg((c) => ({ ...c, [k]: v }))

  async function saveConfig() {
    onErr('')
    try {
      await api.put('/api/pricesync/config', {
        tenant_id: cfg.tenant_id, client_id: cfg.client_id,
        pricesheet_view: cfg.pricesheet_view, market: cfg.market,
        aging_days: Number(cfg.aging_days), stale_days: Number(cfg.stale_days),
        use_month_rule: cfg.use_month_rule, retention_count: Number(cfg.retention_count),
        notify_webhook_url: cfg.notify_webhook_url,
      })
      onMsg('Pricing settings saved.'); load()
    } catch (e) { onErr(e.message) }
  }
  async function saveCredential() {
    onErr('')
    if (!cred.value.trim()) { onErr('Enter the credential value first.'); return }
    try {
      const r = await api.put('/api/pricesync/credential', cred)
      setCred({ ...cred, value: '' }); onMsg(`App credential saved (${r.credential_kind}).`); load()
    } catch (e) { onErr(e.message) }
  }
  async function clearCredential() {
    try { await api.del('/api/pricesync/credential'); onMsg('Credential cleared.'); load() } catch (e) { onErr(e.message) }
  }
  async function saveRefreshToken() {
    onErr('')
    if (!rt.trim()) { onErr('Paste the refresh token first.'); return }
    try { await api.put('/api/pricesync/refresh-token', { value: rt }); setRt(''); onMsg('Refresh token saved.'); load() }
    catch (e) { onErr(e.message) }
  }
  async function clearRefreshToken() {
    try { await api.del('/api/pricesync/refresh-token'); onMsg('Refresh token cleared.'); load() } catch (e) { onErr(e.message) }
  }
  function loadCertFile() {
    const f = certFile.current?.files?.[0]; if (!f) return
    const r = new FileReader(); r.onload = () => setCred({ kind: 'certificate', value: String(r.result) }); r.readAsText(f)
  }
  async function refresh() {
    setBusy(true); onErr('')
    try {
      const r = await api.post('/api/pricesync/refresh')
      onMsg(`Price sheet fetched: ${r.metadata.file_name} (${(r.metadata.file_bytes / 1e6).toFixed(1)} MB, MFA ${String(r.metadata.mfa_compliant)}).`)
      load()
    } catch (e) { onErr(e.message) } finally { setBusy(false) }
  }
  async function importLatest() {
    onErr('')
    try { const r = await api.post('/api/pricesync/import-latest'); onMsg(`Imported into catalog: ${r.inserted} new, ${r.updated} updated, ${r.skipped} skipped.`) }
    catch (e) { onErr(e.message) }
  }
  async function ageCheck() {
    try { const r = await api.post('/api/pricesync/check-notify'); onMsg(`Age check: ${r.state}${r.notified ? ' — notification sent' : ''}.`) } catch (e) { onErr(e.message) }
  }

  const stateCls = s?.state === 'stale' ? 'neg' : s?.state === 'aging' ? 'warn' : 'pos'
  return (
    <div className="card">
      <h2>Pricing sync (Partner Center · CSP)</h2>
      <p className="hint">Cloud Solution Provider authentication (Secure Application Model):
        a one-time partner consent yields a refresh token the app exchanges for access
        tokens on your behalf — no per-fetch browser redirect, so it works whether the app
        is reached by IP or hostname. All configured here; nothing in environment variables.</p>

      {s && (
        <div className="muted" style={{ fontSize: '.84rem', marginBottom: '.6rem' }}>
          Status: <span className={`badge ${stateCls}`}>{s.state}</span>{' '}
          {s.configured ? <>configured ({s.credential_kind})</> : <b className="warn">not configured — complete the fields below</b>}
          {s.latest && <> · sheet {s.latest.data_month} · {(s.latest.file_bytes / 1e6).toFixed(1)} MB · MFA {String(s.latest.mfa_compliant)}</>}
          {s.age_days != null && <> · {s.age_days} days old</>}
        </div>
      )}

      {cfg && (
        <>
          {cfg.signed_in_user && (
            <div className="muted" style={{ fontSize: '.82rem', marginBottom: '.4rem' }}>
              Last consent account: <b>{cfg.signed_in_user}</b>
            </div>
          )}
          <div className="grid c2">
            <div><label>Partner tenant ID <span className="warn">· required</span></label>
              <input value={cfg.tenant_id} placeholder="partner tenant guid"
                onChange={(e) => setField('tenant_id', e.target.value)} /></div>
            <div><label>Client (application) ID <span className="warn">· required</span></label>
              <input value={cfg.client_id} placeholder="app id from App Management"
                onChange={(e) => setField('client_id', e.target.value)} /></div>
            <div><label>Price sheet view</label>
              <select value={cfg.pricesheet_view} onChange={(e) => setField('pricesheet_view', e.target.value)}>
                <option value="">— select —</option>
                {(cfg.valid_views || []).map((v) => <option key={v}>{v}</option>)}
              </select></div>
            <div><label>Market</label>
              <input value={cfg.market} onChange={(e) => setField('market', e.target.value)} /></div>
            <div style={{ gridColumn: '1 / -1' }}><label>Notify webhook (optional, Teams/generic)</label>
              <input value={cfg.notify_webhook_url} placeholder="empty disables"
                onChange={(e) => setField('notify_webhook_url', e.target.value)} /></div>
          </div>
          <div className="grid c4" style={{ marginTop: '.5rem' }}>
            <div><label>Aging at (days)</label><input type="number" value={cfg.aging_days} onChange={(e) => setField('aging_days', e.target.value)} /></div>
            <div><label>Stale at (days)</label><input type="number" value={cfg.stale_days} onChange={(e) => setField('stale_days', e.target.value)} /></div>
            <div><label>Retention (sheets)</label><input type="number" value={cfg.retention_count} onChange={(e) => setField('retention_count', e.target.value)} /></div>
            <div><label><input type="checkbox" style={{ width: 'auto', marginRight: 6 }} checked={cfg.use_month_rule} onChange={(e) => setField('use_month_rule', e.target.checked)} />Month rule</label></div>
          </div>
          <button style={{ marginTop: '.6rem' }} onClick={saveConfig}>Save settings</button>

          <div className="card" style={{ background: 'var(--panel2)', marginTop: '.8rem' }}>
            <b>App credential</b>{' '}
            {cfg.credential_set ? <span className="badge pos">{cfg.credential_kind} set</span> : <span className="badge muted">not set</span>}
            {!cfg.secret_store_enabled && <b className="warn"> — set TCO_MASTER_SECRET to store secrets.</b>}
            <p className="hint" style={{ margin: '.3rem 0' }}>The app registration's credential. Certificate preferred; stored encrypted, never returned.</p>
            <div className="toolbar">
              <div><label>Type</label>
                <select value={cred.kind} onChange={(e) => setCred({ kind: e.target.value, value: '' })}>
                  <option value="certificate">Certificate (PEM: key + cert)</option>
                  <option value="secret">Client secret</option>
                </select></div>
              {cred.kind === 'secret' ? (
                <div style={{ flex: 2 }}><label>Client secret</label>
                  <input type="password" value={cred.value} disabled={!cfg.secret_store_enabled} onChange={(e) => setCred({ ...cred, value: e.target.value })} /></div>
              ) : (
                <div style={{ flex: 2 }}><label>Certificate PEM — upload or paste</label>
                  <input type="file" accept=".pem,.crt,.key,.txt" ref={certFile} disabled={!cfg.secret_store_enabled} onChange={loadCertFile} /></div>
              )}
              <button className="sm" disabled={!cfg.secret_store_enabled} onClick={saveCredential}>Save credential</button>
              {cfg.credential_set && <button className="danger sm" onClick={clearCredential}>Clear</button>}
            </div>
            {cred.kind === 'certificate' && (
              <textarea rows={3} value={cred.value} disabled={!cfg.secret_store_enabled}
                placeholder="-----BEGIN PRIVATE KEY-----&#10;...&#10;-----BEGIN CERTIFICATE-----&#10;..."
                style={{ marginTop: '.4rem', fontFamily: 'monospace', fontSize: '.75rem' }}
                onChange={(e) => setCred({ kind: 'certificate', value: e.target.value })} />
            )}
          </div>

          <div className="card" style={{ background: 'var(--panel2)', marginTop: '.6rem' }}>
            <b>Consent refresh token</b>{' '}
            {cfg.refresh_token_set ? <span className="badge pos">set</span> : <span className="badge muted">not set</span>}
            <p className="hint" style={{ margin: '.3rem 0' }}>
              From a <b>one-time partner consent</b> with an MFA-enabled dedicated service
              account holding Admin Agent or Sales Agent. Obtain it via the Secure Application
              Model consent for scope <code>{cfg.token_scope}</code> (plus offline_access),
              then paste the refresh token here. Stored encrypted; rotates automatically on use.
            </p>
            <div className="toolbar">
              <div style={{ flex: 2 }}><label>Refresh token</label>
                <input type="password" value={rt} disabled={!cfg.secret_store_enabled}
                  placeholder={cfg.refresh_token_set ? '•••••• (replace)' : 'paste refresh token'}
                  onChange={(e) => setRt(e.target.value)} /></div>
              <button className="sm" disabled={!cfg.secret_store_enabled || !rt} onClick={saveRefreshToken}>Save token</button>
              {cfg.refresh_token_set && <button className="danger sm" onClick={clearRefreshToken}>Clear</button>}
            </div>
            <button className="ghost sm" style={{ marginTop: '.4rem' }}
              onClick={() => setShowHelp((v) => !v)}>
              {showHelp ? 'Hide' : 'How do I get a refresh token?'}
            </button>
            {showHelp && (
              <div style={{ marginTop: '.5rem' }}>
                <p className="hint" style={{ margin: '.2rem 0' }}>
                  Run this once in PowerShell on any machine with a browser. Sign in
                  with the <b>MFA-enabled Admin Agent / Sales Agent service account</b>.
                  It opens a browser for consent and prints a refresh token — paste that
                  into the field above. (Uses <code>http://localhost</code> loopback, so
                  it works from any workstation.)
                </p>
                <pre style={{ background: 'var(--bg)', border: '1px solid var(--border)',
                  borderRadius: 6, padding: '.6rem', fontSize: '.72rem', overflowX: 'auto',
                  whiteSpace: 'pre' }}>{refreshTokenScript(cfg)}</pre>
                <small className="src">Using a certificate instead of a secret? Replace the
                  credential lines with <code>-CertificateThumbprint &lt;thumbprint&gt;</code>
                  on <code>New-PartnerAccessToken</code>. Ensure the app registration has the
                  Partner Center API permission and the consent covered it.</small>
              </div>
            )}
          </div>
        </>
      )}

      <div className="row" style={{ gap: '.4rem', marginTop: '.6rem' }}>
        <button className="sm" disabled={!s?.configured || busy} onClick={refresh}
          title={s?.configured ? '' : 'Complete settings, credential, and refresh token first'}>
          {busy ? 'Fetching…' : 'Refresh pricing now'}
        </button>
        <button className="ghost sm" disabled={!s?.latest} onClick={importLatest}>Import latest into catalog</button>
        <button className="ghost sm" onClick={ageCheck}>Run age check</button>
      </div>
    </div>
  )
}
function DefaultOutcomes({ onMsg, onErr }) {
  const base = '/api/admin/default-outcomes'
  const [items, setItems] = useState([])
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')

  const load = () => api.get(base).then(setItems).catch((e) => onErr(e.message))
  useEffect(() => { load() }, [])

  async function add() {
    if (!name.trim()) return
    try {
      await api.post(base, { name, description: desc })
      setName(''); setDesc(''); onMsg('Outcome added to the default library.'); load()
    } catch (e) { onErr(e.message) }
  }
  async function update(id, patch, current) {
    try { await api.patch(`${base}/${id}`, { name: current.name, description: current.description, ...patch }) }
    catch (e) { onErr(e.message) }
  }
  async function remove(id) {
    if (!confirm('Remove from the default library? Existing engagements are unaffected.')) return
    try { await api.del(`${base}/${id}`); load() } catch (e) { onErr(e.message) }
  }

  return (
    <div className="card">
      <h2>Outcomes (default library)</h2>
      <p className="hint">The capability buckets seeded into every <b>new</b> engagement.
        Editing here is the template only — existing engagements keep their own copy and
        are never changed. Outcomes drive the coverage map and the best-bundle analysis.</p>

      <table>
        <thead><tr><th>Outcome</th><th>Description</th><th></th></tr></thead>
        <tbody>
          {items.map((o) => (
            <tr key={o.id}>
              <td style={{ width: '32%' }}>
                <input defaultValue={o.name}
                  onBlur={(e) => update(o.id, { name: e.target.value }, o)} />
              </td>
              <td>
                <input defaultValue={o.description}
                  onBlur={(e) => update(o.id, { description: e.target.value }, o)} />
              </td>
              <td className="num"><button className="danger sm" onClick={() => remove(o.id)}>Remove</button></td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="grid c2" style={{ marginTop: '.6rem' }}>
        <div><label>New outcome name</label>
          <input value={name} placeholder="e.g. Data Backup & Recovery"
            onChange={(e) => setName(e.target.value)} /></div>
        <div><label>Description</label>
          <input value={desc} placeholder="short description"
            onChange={(e) => setDesc(e.target.value)} /></div>
      </div>
      <button style={{ marginTop: '.6rem' }} onClick={add}>Add outcome</button>
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
