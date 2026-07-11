import React, { useEffect, useState } from 'react'
import { api, usd, pct } from '../api'
import { PricingBadge } from './PricingBanner.jsx'

// Sanity-check results persist across tab navigation (per engagement) without a
// new data field — a module-level cache that outlives the Readout unmount.
const _sanityCache = {}

function timeAgo(ms) {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000))
  if (s < 60) return `${s}s ago`
  const m = Math.round(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.round(h / 24)}d ago`
}

export default function Readout({ engagement }) {
  const eid = engagement.id
  const [result, setResult] = useState(null)
  const [snapshots, setSnapshots] = useState([])
  const [err, setErr] = useState('')
  // Pre-readout AI sanity check (advisory).
  const [aiEnabled, setAiEnabled] = useState(false)
  const [checking, setChecking] = useState(false)
  const [sanity, setSanity] = useState(null)
  const [narrating, setNarrating] = useState(false)
  const [narratives, setNarratives] = useState(null)
  const [outcomes, setOutcomes] = useState([])
  const outcomeName = (id) => outcomes.find((o) => o.id === id)?.name || id

  function compute() {
    setErr('')
    api.post(`/api/engagements/${eid}/compute`).then(setResult).catch((e) => setErr(e.message))
    api.get(`/api/engagements/${eid}/snapshots`).then(setSnapshots).catch(() => {})
  }
  useEffect(() => {
    compute()
    // Restore any prior sanity result for this engagement (survives navigation).
    setSanity(_sanityCache[eid] || null); setNarratives(null)
    api.get('/api/admin/ai/status').then((s) => setAiEnabled(s.enabled)).catch(() => {})
    api.get(`/api/engagements/${eid}/outcomes`).then(setOutcomes).catch(() => setOutcomes([]))
  }, [eid])

  async function runSanity() {
    setChecking(true); setErr('')
    try {
      const res = await api.post(`/api/engagements/${eid}/sanity-check`)
      const entry = { ...res, at: Date.now() }
      _sanityCache[eid] = entry
      setSanity(entry)
    } catch (e) { setErr(e.message) } finally { setChecking(false) }
  }
  async function runNarrative() {
    setNarrating(true); setErr('')
    try { setNarratives((await api.post(`/api/engagements/${eid}/narrative`)).narratives) }
    catch (e) { setErr(e.message) } finally { setNarrating(false) }
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
  // Cost-change convention: negative delta = saving (good -> green); positive =
  // a cost increase (neutral/default -> not alarming, spending more isn't bad).
  const saving = r.net_tco_delta_annual < 0
  const quickWin = Number(r.quick_win_savings_annual) || 0
  const needsClassify = result.dispositions.filter((d) => d.requires_residual_classification)

  return (
    <>
      <div className="card">
        <div className="flex-between">
          <div>
            <div className="muted">Net TCO delta · annualized USD · <small>negative = saving</small> <PricingBadge /></div>
            <div className={`headline ${saving ? 'pos' : ''}`} style={{ fontSize: '2.6rem' }}>{usd(r.net_tco_delta_annual)}</div>
            <div className="muted">{saving ? 'Hard-dollar annual savings if you move to the target scenarios' : r.net_tco_delta_annual > 0 ? 'Annual cost increase — shown honestly (for the added capabilities below)' : 'No net change'}</div>
          </div>
          <div className="row" style={{ gap: '.4rem' }}>
            {aiEnabled && (
              <button className="ghost sm" onClick={runSanity} disabled={checking}
                title="Ask an inexpensive model to flag likely mistakes before you present">
                {checking ? 'Checking…' : '✨ AI sanity check'}</button>
            )}
            {aiEnabled && (
              <button className="ghost sm" onClick={runNarrative} disabled={narrating}
                title="Draft the per-persona business narrative (today / what's new / value)">
                {narrating ? 'Writing…' : '✨ Business narratives'}</button>
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
          <b>{r.population_check.in_scope_persona_headcount}</b> · third-party tool-seats{' '}
          <b>{r.population_check.third_party_covered_population}</b>{' '}
          <small className="muted">(summed across tools; people may hold several, so this isn't a distinct-people count)</small>. Per-tool coverage vs. displacement is in the dispositions below.
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
      </div>

      {aiEnabled && (
        <details className="card">
          <summary style={{ cursor: 'pointer', listStyle: 'revert' }}>
            <b>AI Sanity Check</b>{' '}
            {sanity
              ? <small className="muted">— last run {timeAgo(sanity.at)} · {sanity.findings.length === 0 ? 'no issues' : `${sanity.findings.length} finding(s)`}</small>
              : <small className="muted">— not run yet</small>}
          </summary>
          <div style={{ marginTop: '.6rem' }}>
            <div className="flex-between">
              <small className="src">Advisory only — never edits your data.{sanity ? ` Model: ${sanity.model}` : ''}</small>
              <button className="ghost sm" onClick={runSanity} disabled={checking}>
                {checking ? 'Checking…' : sanity ? '↻ Re-run' : 'Run sanity check'}</button>
            </div>
            {!sanity && <div className="muted" style={{ marginTop: '.4rem' }}>Not run yet — run it to flag likely mistakes before you present.</div>}
            {sanity && sanity.findings.length === 0 && (
              <div className="muted" style={{ marginTop: '.4rem' }}>✓ No issues flagged — the numbers look reasonable.</div>
            )}
            {sanity && sanity.findings.length > 0 && (
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
        </details>
      )}

      {r.quick_wins && r.quick_wins.length > 0 && (
        <div className="card" style={{ borderColor: 'var(--pos, #127436)' }}>
          <div className="muted">💡 Quick wins — save today, without changing licenses <small>(duplicates your current licensing already covers)</small></div>
          <div className="headline pos">{usd(quickWin)}<span style={{ fontSize: '1rem', fontWeight: 400 }}>/yr</span></div>
          <p className="hint">These third-party tools deliver outcomes your <b>current</b> Microsoft
            licensing already provides — you're paying twice. They can be retired <b>today</b>,
            independent of any move to a new scenario.</p>
          <table>
            <thead><tr>
              <th>Tool</th><th>Duplicated capability (already in current licensing)</th>
              <th className="num">Covered</th><th className="num">Redundant today</th><th className="num">Save/yr</th>
            </tr></thead>
            <tbody>
              {r.quick_wins.map((q) => (
                <tr key={q.third_party_product_id}>
                  <td>{q.third_party_product_name}</td>
                  <td>{q.duplicated_outcome_ids.map(outcomeName).join(', ')}</td>
                  <td className="num">{q.covered_count}</td>
                  <td className="num">{q.displaced_today}{q.residual_today ? ` (${q.residual_today} left)` : ''}</td>
                  <td className="num pos">{usd(q.credited_annual)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {narratives && (
        <div className="card">
          <div className="flex-between">
            <h2>Business narratives</h2>
            <small className="src">AI draft per in-scope persona — review before you present. Advisory only.</small>
          </div>
          {narratives.length === 0
            ? <p className="muted">No in-scope scenarios to narrate yet — set target bundles on the Scenarios tab.</p>
            : narratives.map((n, i) => (
              <div key={i} className="popcheck" style={{ marginTop: '.5rem' }}>
                <b>{n.persona}</b>
                {n.today && <p style={{ margin: '.3rem 0' }}><b>Today: </b>{n.today}</p>}
                {n.whats_new && <p style={{ margin: '.3rem 0' }}><b>What's new: </b>{n.whats_new}</p>}
                {n.value && <p style={{ margin: '.3rem 0' }}><b>Value: </b>{n.value}</p>}
              </div>
            ))}
        </div>
      )}

      <div className="card">
        <h2>How we get to the number</h2>
        <p className="hint">The new target Microsoft licensing, less the existing spend it
          retires (current Microsoft plus the third-party tooling those users free up),
          building to the net change above. Negative = saving.</p>
        <table className="bridge">
          <tbody>
            <tr>
              <td>Target Microsoft licensing <small className="muted">(new per-persona bundles)</small></td>
              <td className="num">{usd(r.target_microsoft_annual)}</td>
            </tr>
            <tr>
              <td>Less: existing Microsoft licensing retired <small className="muted">(current assigned)</small></td>
              <td className="num pos">−{usd(r.existing_microsoft_annual)}</td>
            </tr>
            {(() => {
              const freed = r.freed_third_party || []
              const already = freed.filter((f) => f.already_covered)
              const newly = freed.filter((f) => !f.already_covered)
              const sum = (arr) => arr.reduce((s, f) => s + Number(f.credited_annual || 0), 0)
              const subRows = (arr) => arr.map((f) => (
                <tr key={f.third_party_product_id} className="bridge-sub">
                  <td>↳ {f.third_party_product_name}{f.credited_annual === 0
                    ? <span className="muted"> — $0 credited (set its covered population to free up spend)</span>
                    : ' freed up'}</td>
                  <td className="num pos">{f.credited_annual ? `−${usd(f.credited_annual)}` : usd(0)}</td>
                </tr>
              ))
              const block = (label, sub, arr) => (
                <React.Fragment key={label}>
                  <tr>
                    <td>Less: {label} <small className="muted">{sub}</small></td>
                    <td className="num pos">−{usd(sum(arr))}</td>
                  </tr>
                  {subRows(arr)}
                </React.Fragment>
              )
              if (freed.length === 0) return (
                <tr><td>Less: existing third-party tooling freed up <small className="muted">(none)</small></td><td className="num pos">−{usd(0)}</td></tr>
              )
              return <>
                {already.length > 0 && block('third-party already covered by current licensing', '(quick win — free today)', already)}
                {newly.length > 0 && block('third-party additionally freed by the move', '(new displacement from the target)', newly)}
              </>
            })()}
            <tr className="bridge-total">
              <td><b>Net TCO delta</b> <small className="muted">{saving ? '(annual savings)' : r.net_tco_delta_annual > 0 ? '(annual cost increase)' : '(no net change)'}</small></td>
              <td className={`num ${saving ? 'pos' : ''}`}><b>{usd(r.net_tco_delta_annual)}</b></td>
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

      {(result.license_limits || []).length > 0 && (
        <div className="card" style={{ borderColor: (result.license_limits.some((l) => l.violated) ? 'var(--warn)' : undefined) }}>
          <h2 className={result.license_limits.some((l) => l.violated) ? 'warn' : undefined}>
            {result.license_limits.some((l) => l.violated) ? '⚠ ' : ''}License limits</h2>
          <p className="hint">Microsoft licensing caps evaluated tenant-wide — current state (existing
            licenses) and future state (in-scope scenarios) summed across all personas.</p>
          <table>
            <thead><tr><th>Limit</th><th>Applies to</th><th className="num">Cap</th>
              <th className="num">Current</th><th className="num">Target (in-scope)</th><th>Status</th></tr></thead>
            <tbody>
              {result.license_limits.map((l) => (
                <tr key={l.id}>
                  <td>{l.name}</td>
                  <td className="muted" style={{ fontSize: '.8rem' }}>{l.member_bundle_names.join(', ')}</td>
                  <td className="num">{l.max_quantity}</td>
                  <td className={`num ${l.current_over_by > 0 ? 'warn' : ''}`}>{l.current_seats}</td>
                  <td className={`num ${l.target_over_by > 0 ? 'warn' : ''}`}>{l.target_seats}</td>
                  <td>{l.violated
                    ? <span className="badge warn">Over by {Math.max(l.current_over_by, l.target_over_by)}</span>
                    : <span className="badge pos">Within cap</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {result.bp_swap?.enabled && result.bp_swap.swapped_count > 0 && (
        <div className="card" style={{ background: 'var(--panel2)' }}>
          <h2>Microsoft 365 Business Premium swap</h2>
          <p className="hint">Eligible personas moved to Business Premium (Business Premium covers every
            outcome they require). {result.bp_swap.eligible_count} eligible · {result.bp_swap.swapped_count} applied.</p>
          <div className="popcheck">
            <b>{result.bp_swap.swapped_users}</b> users swapped to Business Premium ·
            combined annual delta{' '}
            <b className={result.bp_swap.swap_delta_annual < 0 ? 'pos' : ''}>{usd(result.bp_swap.swap_delta_annual)}</b>
            {result.bp_swap.swap_delta_annual < 0 ? ' (saving)' : result.bp_swap.swap_delta_annual > 0 ? ' (cost increase)' : ''}.
          </div>
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
                <td className={`num ${s.delta_annual < 0 ? 'pos' : ''}`}>{usd(s.delta_annual)}</td>
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
                      : d.requires_residual_classification
                        // Only when unclassified residual users remain is there
                        // anything to override. FullyEliminated (0 residual) has none.
                        ? <ResidualClassifier inline d={d} onSet={setOverride} />
                        : <span className="muted">—</span>}
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
  const apply = () => {
    if (mode === 'residual') onSet(d.third_party_product_id, { override: 'None', residual_intent: 'IntendedOutOfScope' })
    else if (mode === 'force' && reason.trim()) onSet(d.third_party_product_id, { override: 'ForceFullElimination', override_reason: reason })
  }
  // A real dropdown (never a browser prompt) in both the inline table cell and
  // the expanded card. Picking a mode reveals the reason field for force-elim.
  const controls = (
    <div className="toolbar" style={{ marginTop: inline ? 0 : '.5rem', gap: '.4rem', alignItems: 'flex-end' }}>
      <div>
        {!inline && <label>Classification</label>}
        <select value={mode} onChange={(e) => setMode(e.target.value)} style={inline ? { minWidth: 160 } : undefined}>
          <option value="">Classify…</option>
          <option value="residual">Intended out-of-scope residual</option>
          <option value="force">Force full elimination</option>
        </select>
      </div>
      {mode === 'force' && (
        <div style={{ flex: 2 }}>
          {!inline && <label>Override reason (required)</label>}
          <input value={reason} placeholder="Override reason (prints on readout)"
            onChange={(e) => setReason(e.target.value)} />
        </div>
      )}
      {mode && (
        <button className="sm" disabled={mode === 'force' && !reason.trim()} onClick={apply}>Apply</button>
      )}
    </div>
  )
  if (inline) return controls
  return (
    <div className="card" style={{ background: 'var(--panel2)' }}>
      <b>{d.third_party_product_name}</b> — {d.residual_count} residual units, {usd(d.residual_annual_cost)}/yr
      {controls}
    </div>
  )
}
