import React, { useEffect, useState } from 'react'
import { api, usd, pct } from '../api'
import { PricingBadge } from './PricingBanner.jsx'

export default function Readout({ engagement }) {
  const eid = engagement.id
  const [result, setResult] = useState(null)
  const [snapshots, setSnapshots] = useState([])
  const [err, setErr] = useState('')
  // Pre-readout AI sanity check (advisory).
  const [aiEnabled, setAiEnabled] = useState(false)
  const [checking, setChecking] = useState(false)
  const [sanity, setSanity] = useState(null)

  function compute() {
    setErr('')
    api.post(`/api/engagements/${eid}/compute`).then(setResult).catch((e) => setErr(e.message))
    api.get(`/api/engagements/${eid}/snapshots`).then(setSnapshots).catch(() => {})
  }
  useEffect(() => {
    compute()
    setSanity(null)
    api.get('/api/admin/ai/status').then((s) => setAiEnabled(s.enabled)).catch(() => {})
  }, [eid])

  async function runSanity() {
    setChecking(true); setErr('')
    try { setSanity(await api.post(`/api/engagements/${eid}/sanity-check`)) }
    catch (e) { setErr(e.message) } finally { setChecking(false) }
  }

  // Readout branding (logo + theme colors). Local so edits reflect immediately;
  // persisted on the engagement and applied by the HTML readout.
  const [brand, setBrand] = useState({
    logo: engagement.brand_logo_data_url || '',
    primary: engagement.brand_primary_color || '',
    accent: engagement.brand_accent_color || '',
  })
  useEffect(() => setBrand({
    logo: engagement.brand_logo_data_url || '',
    primary: engagement.brand_primary_color || '',
    accent: engagement.brand_accent_color || '',
  }), [eid])
  async function patchBrand(patch) {
    const next = { ...brand, ...patch }
    setBrand(next)
    const body = {
      brand_logo_data_url: next.logo, brand_primary_color: next.primary,
      brand_accent_color: next.accent,
    }
    try { await api.patch(`/api/engagements/${eid}`, body) } catch (e) { setErr(e.message) }
  }
  function onLogoFile(file) {
    if (!file) return
    if (!file.type.startsWith('image/')) { setErr('Logo must be an image (PNG/SVG/JPG).'); return }
    const reader = new FileReader()
    reader.onload = () => patchBrand({ logo: reader.result })
    reader.readAsDataURL(file)
  }

  async function setOverride(tpId, payload) {
    try {
      await api.put(`/api/engagements/${eid}/dispositions/${tpId}/override`, payload)
      compute()
    } catch (e) { setErr(e.message) }
  }
  async function snapshot() {
    const label = prompt('Snapshot label?', new Date().toISOString().slice(0, 10))
    if (label === null) return
    try { await api.post(`/api/engagements/${eid}/snapshots?label=${encodeURIComponent(label)}`); compute() }
    catch (e) { setErr(e.message) }
  }

  if (err) return <div className="card"><div className="err">{err}</div></div>
  if (!result) return <div className="card"><p className="muted">Computing…</p></div>

  const r = result.rollup
  const pos = r.net_tco_delta_annual >= 0
  const needsClassify = result.dispositions.filter((d) => d.requires_residual_classification)

  return (
    <>
      <div className="card">
        <div className="flex-between">
          <div>
            <div className="muted">Net TCO delta · annualized USD <PricingBadge /></div>
            <div className={`headline ${pos ? 'pos' : 'neg'}`}>{usd(r.net_tco_delta_annual)}</div>
            <div className="muted">{pos ? 'Hard-dollar annual savings' : 'Annual cost increase — shown honestly'}</div>
          </div>
          <div className="row" style={{ gap: '.4rem' }}>
            {aiEnabled && (
              <button className="ghost sm" onClick={runSanity} disabled={checking}
                title="Ask an inexpensive model to flag likely mistakes before you present">
                {checking ? 'Checking…' : '✨ AI sanity check'}</button>
            )}
            <a href={`/api/engagements/${eid}/readout.html`} target="_blank" rel="noreferrer">
              <button className="ghost sm">Open HTML readout</button></a>
            <a href={`/api/engagements/${eid}/readout.xlsx`}>
              <button className="ghost sm">Export .xlsx</button></a>
            <button className="ghost sm" onClick={snapshot}>Snapshot</button>
          </div>
        </div>
        <div className="popcheck">
          <b>Population check.</b> In-scope persona headcount{' '}
          <b>{r.population_check.in_scope_persona_headcount}</b> · third-party covered population{' '}
          <b>{r.population_check.third_party_covered_population}</b>. Gaps surface as residuals below.
        </div>
        <details style={{ marginTop: '.5rem' }}>
          <summary className="src" style={{ cursor: 'pointer' }}>Readout branding (logo + theme colors)</summary>
          <div className="grid c4" style={{ marginTop: '.5rem', alignItems: 'end' }}>
            <div><label>Logo (PNG/SVG)</label>
              <input type="file" accept="image/*" onChange={(e) => onLogoFile(e.target.files?.[0])} />
              {brand.logo && <div style={{ marginTop: '.3rem' }}>
                <img src={brand.logo} alt="logo" style={{ maxHeight: 40, maxWidth: 140 }} />{' '}
                <button className="ghost sm" onClick={() => patchBrand({ logo: '' })}>Clear</button>
              </div>}</div>
            <div><label>Primary color</label>
              <input type="color" value={brand.primary || '#1a1a2e'}
                onChange={(e) => patchBrand({ primary: e.target.value })} /></div>
            <div><label>Accent color</label>
              <input type="color" value={brand.accent || '#2563eb'}
                onChange={(e) => patchBrand({ accent: e.target.value })} /></div>
            <div><small className="src">Applied to the HTML readout header, section titles, and callout border. Entered per engagement.</small></div>
          </div>
        </details>
        {sanity && (
          <div className="popcheck" style={{ marginTop: '.5rem' }}>
            <div className="flex-between">
              <b>AI sanity check</b>
              <small className="src">Advisory only — never edits your data. Model: {sanity.model}</small>
            </div>
            {sanity.findings.length === 0
              ? <div className="muted" style={{ marginTop: '.3rem' }}>✓ No issues flagged — the numbers look reasonable.</div>
              : (
                <ul style={{ margin: '.4rem 0 0', paddingLeft: '1.1rem' }}>
                  {sanity.findings.map((f, i) => (
                    <li key={i} style={{ marginBottom: '.25rem' }}>
                      <span className={`badge ${f.severity === 'error' ? 'neg' : f.severity === 'warn' ? 'warn' : 'muted'}`}>
                        {f.severity}</span>{' '}
                      {f.field && <b>{f.field}: </b>}{f.message}
                    </li>
                  ))}
                </ul>
              )}
          </div>
        )}
      </div>

      <div className="card">
        <h2>How we get to the number</h2>
        <p className="hint">Existing annualized spend for the in-scope population, the
          third-party tooling those users free up when they move, and the target
          Microsoft licensing — building to the net TCO delta above.</p>
        <table className="bridge">
          <tbody>
            <tr>
              <td>Existing Microsoft licensing <small className="muted">(current assigned)</small></td>
              <td className="num">{usd(r.existing_microsoft_annual)}</td>
            </tr>
            <tr>
              <td>Existing third-party tooling <small className="muted">(freed up by in-scope moves)</small></td>
              <td className="num">{usd(r.existing_third_party_annual)}</td>
            </tr>
            {r.freed_third_party.map((f) => (
              <tr key={f.third_party_product_id} className="bridge-sub">
                <td>↳ {f.third_party_product_name}{f.credited_annual === 0
                  ? <span className="muted"> — $0 credited (set its covered population to free up spend)</span>
                  : ' freed up'}</td>
                <td className="num">{usd(f.credited_annual)}</td>
              </tr>
            ))}
            <tr className="bridge-total">
              <td><b>Total existing spend (in scope)</b></td>
              <td className="num"><b>{usd(r.existing_microsoft_annual + r.existing_third_party_annual)}</b></td>
            </tr>
            <tr>
              <td>Target Microsoft licensing <small className="muted">(new per-persona bundles)</small></td>
              <td className="num neg">−{usd(r.target_microsoft_annual)}</td>
            </tr>
            <tr className="bridge-total">
              <td><b>Net TCO delta</b> <small className="muted">{pos ? '(annual savings)' : '(annual cost increase)'}</small></td>
              <td className={`num ${pos ? 'pos' : 'neg'}`}><b>{usd(r.net_tco_delta_annual)}</b></td>
            </tr>
          </tbody>
        </table>
      </div>

      {needsClassify.length > 0 && (
        <div className="card" style={{ borderColor: 'var(--warn)' }}>
          <h2 className="warn">⚠ Residuals require classification</h2>
          <p className="hint">A residual exists. You must choose: a ForceFullElimination
            override (asserts savings on undisplaced users, requires a reason that prints on
            the readout) or an intended out-of-scope residual.</p>
          {needsClassify.map((d) => (
            <ResidualClassifier key={d.third_party_product_id} d={d} onSet={setOverride} />
          ))}
        </div>
      )}

      <div className="card">
        <h2>Per-persona scenarios</h2>
        <table>
          <thead><tr><th>Persona</th><th>Target</th><th className="num">HC</th>
            <th className="num">Current</th><th className="num">Target</th><th className="num">Delta</th><th>Scope</th></tr></thead>
          <tbody>
            {result.scenarios.map((s) => (
              <tr key={s.scenario_id} style={{ opacity: s.in_scope ? 1 : 0.5 }}>
                <td>{s.persona_name}</td><td>{s.target_sku_reference}</td><td className="num">{s.headcount}</td>
                <td className="num">{usd(s.current_spend_annual)}</td>
                <td className="num">{usd(s.target_spend_annual)}</td>
                <td className={`num ${s.delta_annual >= 0 ? 'pos' : 'neg'}`}>{usd(s.delta_annual)}</td>
                <td>{s.in_scope ? <span className="badge pos">In scope</span> : <span className="badge muted">Excluded</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h2>Third-party dispositions</h2>
        <table>
          <thead><tr><th>Product</th><th>Disposition</th><th className="num">Displaced/Covered</th>
            <th className="num">Residual</th><th className="num">Residual $/yr</th><th>Override</th></tr></thead>
          <tbody>
            {result.dispositions.map((d) => (
              <tr key={d.third_party_product_id}>
                <td>{d.third_party_product_name}</td>
                <td>
                  <span className={`badge ${d.disposition === 'FullyEliminated' ? 'pos' : d.disposition === 'PartiallyReduced' ? 'warn' : 'muted'}`}>
                    {d.disposition}
                  </span>
                </td>
                <td className="num">{d.displaced_users} / {d.covered_count}</td>
                <td className="num">{d.residual_count}</td>
                <td className="num">{usd(d.residual_annual_cost)}</td>
                <td>
                  {d.override === 'ForceFullElimination'
                    ? <span className="badge neg" title={d.override_reason}>Forced · {d.override_reason.slice(0, 30)}</span>
                    : d.residual_intent === 'IntendedOutOfScope'
                      ? <span className="badge muted">Intended residual</span>
                      : <ResidualClassifier inline d={d} onSet={setOverride} />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h2>Rollup</h2>
        <div className="grid c2">
          <div>
            <b>Fully eliminated tools</b>
            <ul>{r.fully_eliminated_tools.length ? r.fully_eliminated_tools.map((t) => <li key={t}>{t}</li>) : <li className="muted">None</li>}</ul>
          </div>
          <div>
            <b>Eliminated renewal cycles</b> <small className="muted">(gated on full elimination)</small>
            <ul>{r.eliminated_renewal_cycles.length
              ? r.eliminated_renewal_cycles.map((c) => <li key={c.third_party_product_id}>{c.third_party_product_name}{c.renewal_date ? ` — renews ${c.renewal_date}` : ''}</li>)
              : <li className="muted">None</li>}</ul>
          </div>
        </div>
        <p><b>Residual third-party cost:</b> {usd(r.residual_third_party_cost_annual)}</p>
      </div>

      {snapshots.length > 0 && (
        <div className="card">
          <h2>Snapshots</h2>
          <table><thead><tr><th>Label</th><th>Created</th><th>Catalog</th></tr></thead>
            <tbody>{snapshots.map((s) => (
              <tr key={s.id}><td>{s.label}</td><td className="muted">{s.created_at.slice(0, 19).replace('T', ' ')}</td><td className="muted">{s.catalog_version || '—'}</td></tr>
            ))}</tbody></table>
        </div>
      )}
    </>
  )
}

function ResidualClassifier({ d, onSet, inline }) {
  const [reason, setReason] = useState('')
  const [mode, setMode] = useState('')
  if (inline) {
    return (
      <button className="ghost sm warn" onClick={() => {
        const choice = prompt('Type "force" for ForceFullElimination, or "residual" for intended out-of-scope residual:')
        if (choice === 'residual') onSet(d.third_party_product_id, { override: 'None', residual_intent: 'IntendedOutOfScope' })
        else if (choice === 'force') {
          const why = prompt('Override reason (prints on the readout):')
          if (why) onSet(d.third_party_product_id, { override: 'ForceFullElimination', override_reason: why })
        }
      }}>Classify…</button>
    )
  }
  return (
    <div className="card" style={{ background: 'var(--panel2)' }}>
      <b>{d.third_party_product_name}</b> — {d.residual_count} residual units, {usd(d.residual_annual_cost)}/yr
      <div className="toolbar" style={{ marginTop: '.5rem' }}>
        <div>
          <label>Classification</label>
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="">Choose…</option>
            <option value="residual">Intended out-of-scope residual</option>
            <option value="force">Force full elimination</option>
          </select>
        </div>
        {mode === 'force' && (
          <div style={{ flex: 2 }}>
            <label>Override reason (required)</label>
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
        )}
        <button className="sm" disabled={mode === '' || (mode === 'force' && !reason.trim())}
          onClick={() => {
            if (mode === 'residual') onSet(d.third_party_product_id, { override: 'None', residual_intent: 'IntendedOutOfScope' })
            else onSet(d.third_party_product_id, { override: 'ForceFullElimination', override_reason: reason })
          }}>Apply</button>
      </div>
    </div>
  )
}
