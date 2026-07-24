import React, { useEffect, useState } from 'react'
import { api, usd } from '../api'

// Compact unsigned money for headline altitude ($246,560) — direction is said
// in words ("saved" / "adds"), never as a sign next to the word savings.
// The bridge tables keep signed accounting math.
const usd0 = (v) => `$${Math.abs(Math.round(Number(v) || 0)).toLocaleString('en-US')}`
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
  const [narrativesAt, setNarrativesAt] = useState(null)
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
    setSanity(_sanityCache[eid] || null)
    // Narratives are ENGAGEMENT-LEVEL data — load the stored set (survives
    // navigation); the Business narratives button regenerates and replaces it.
    setNarratives(null); setNarrativesAt(null)
    api.get(`/api/engagements/${eid}/narrative`).then((r) => {
      if (r.narratives?.length) { setNarratives(r.narratives); setNarrativesAt(r.generated_at) }
    }).catch(() => {})
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
    try {
      const r = await api.post(`/api/engagements/${eid}/narrative`)
      setNarratives(r.narratives); setNarrativesAt(r.generated_at)
    } catch (e) { setErr(e.message) } finally { setNarrating(false) }
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
  // The in-scope scenarios drive the headline move summary and the per-persona
  // columns of the spend bridge — their per-scenario numbers sum exactly to the
  // rollup totals (the engine builds the rollup from these same values).
  const inScope = result.scenarios.filter((s) => s.in_scope)

  return (
    <>
      <div className="card">
        <div className="flex-between">
          <div>
            {(() => {
              const horizon = engagement.modeling_horizon_years || 3
              const qw = Number(r.quick_win_savings_annual) || 0
              const movesValue = -(Number(r.move_incremental_delta_annual) || 0)
              const total = qw + movesValue // positive = saved per year
              const word = total > 0 ? `saved over ${horizon * 12} months`
                : total < 0 ? `added cost over ${horizon * 12} months` : 'no net change'
              return (
                <>
                  <div className="muted">Total opportunity · {horizon}-year run-rate view · quick wins + licensing moves · {engagement.currency} <PricingBadge /></div>
                  <div className={`headline headline-xl ${total > 0 ? 'pos' : ''}`}>
                    {usd0(total * horizon)} <span style={{ fontSize: '1.1rem', fontWeight: 600, color: 'var(--muted)' }}>{word}</span>
                  </div>
                  <div className="muted">
                    {qw > 0
                      ? <>{usd0(qw)}/yr from quick wins {movesValue > 0 ? `+ ${usd0(movesValue)}/yr from the persona moves` : movesValue < 0 ? `− ${usd0(movesValue)}/yr invested in the persona moves` : 'with the persona moves cost-neutral'} = <b>{usd0(total)}/yr</b> run-rate</>
                      : <>{usd0(total)}/yr run-rate</>}
                  </div>
                  <div className="popcheck" style={{ marginTop: '.5rem' }}>
                    {qw > 0 && <div>① Retire duplicate tools today — no licensing change: <b className="pos">{usd0(qw)}/yr</b></div>}
                    <div>{qw > 0 ? '② ' : ''}Move each persona to right-sized licensing:{' '}
                      <b className={movesValue > 0 ? 'pos' : ''}>
                        {movesValue > 0 ? `saves ${usd0(movesValue)}/yr` : movesValue < 0 ? `adds ${usd0(movesValue)}/yr` : 'cost-neutral'}
                      </b>
                      <MoveSummary scenarios={inScope} />
                    </div>
                  </div>
                </>
              )
            })()}
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

      {(result.new_outcomes || []).length > 0 && (
        <div className="card">
          <h2>New outcomes</h2>
          <p className="hint">Capabilities each persona gains with the target licensing that
            nothing they hold today delivers — the value the move adds beyond the cost story.
            Validate coverage on the Coverage Check step; ✨ Business narratives drafts why each
            matters for this customer.</p>
          {result.new_outcomes.map((n) => {
            const target = inScope.find((s) => s.persona_id === n.persona_id)?.target_sku_reference
            return (
              <div key={n.persona_id} className="popcheck">
                <b>{n.persona_name}</b> <span className="muted">({n.headcount}){target ? <> → {target}</> : null}</span>
                <div className="pill-list" style={{ marginTop: '.35rem' }}>
                  {n.outcomes.map((o) => (
                    <span key={o.id} className="badge pos" title={o.description || ''}>{o.name}</span>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {narratives && (
        <div className="card">
          <div className="flex-between">
            <h2>Business narratives</h2>
            <small className="src">AI draft per in-scope persona — review before you present. Advisory only.
              Stored on the engagement{narrativesAt ? ` · generated ${new Date(narrativesAt + 'Z').toLocaleString()}` : ''} —
              ✨ Business narratives regenerates.</small>
          </div>
          {narratives.length === 0
            ? <p className="muted">No in-scope scenarios to narrate yet — set target bundles on the Scenarios tab.</p>
            : narratives.map((n) => (
              <NarrativeBlock key={n.id || n.persona} n={n} eid={eid}
                onSaved={(r) => { setNarratives(r.narratives); setNarrativesAt(r.generated_at) }}
                onError={setErr} />
            ))}
        </div>
      )}

      <div className="card">
        <h2>How we get to the number</h2>
        <p className="hint">The new target Microsoft licensing, less the existing spend it
          retires (current Microsoft plus the third-party tooling those users stop paying
          for), building to the net change above — each line broken down per persona.
          The quick-win group reconciles exactly with the Quick wins card.</p>
        {(() => {
          const freed = r.freed_third_party || []
          const already = freed.filter((f) => f.already_covered)
          const newly = freed.filter((f) => !f.already_covered)
          // One column per in-scope persona plus Total. With a single persona the
          // total IS that persona, so columns only appear at two or more.
          const cols = inScope.length > 1 ? inScope : []
          // Offset field per product per persona; the group field on the product
          // rollup maps to the matching per-offset field.
          const OFFSET_FIELD = {
            redundant_today_annual: 'redundant_today_annual',
            move_unlocked_annual: 'move_unlocked_annual',
            credited_annual: 'credited_offset_annual',
          }
          const offsetOf = (s, pid, field) =>
            Number(s.offsets.find((o) => o.third_party_product_id === pid)?.[OFFSET_FIELD[field]] || 0)
          const fmt = (v, negate) => (negate ? (v ? `−${usd(v)}` : usd(0)) : usd(v))
          const cells = (values, total, { negate = false, cls = '' } = {}) => (
            <>
              {cols.map((s, i) => (
                <td key={s.scenario_id} className={`num ${cls}`}>{fmt(values[i], negate)}</td>
              ))}
              <td className={`num ${cls}`}>{fmt(total, negate)}</td>
            </>
          )
          // A bridge group: products contributing via `field`. GUI keeps the
          // operator hint for $0-credited tools (covered population not set);
          // the customer HTML drops those rows entirely.
          const block = (label, sub, arr, field) => {
            const items = arr.filter((f) => Number(f[field] || 0) || (field === 'credited_annual' && Number(f.credited_annual || 0) === 0 && !f.already_covered))
            if (!items.length) return null
            const total = items.reduce((t, f) => t + Number(f[field] || 0), 0)
            return (
              <React.Fragment key={label}>
                <tr>
                  <td>Less: {label} <small className="muted">{sub}</small></td>
                  {cells(cols.map((s) => items.reduce((t, f) => t + offsetOf(s, f.third_party_product_id, field), 0)), total, { negate: true, cls: 'pos' })}
                </tr>
                {items.map((f) => (
                  <tr key={f.third_party_product_id} className="bridge-sub">
                    <td>↳ {f.third_party_product_name}{Number(f.credited_annual || 0) === 0
                      ? <span className="muted"> — $0 credited (set its covered population to count its spend)</span>
                      : null}</td>
                    {cells(cols.map((s) => offsetOf(s, f.third_party_product_id, field)), f[field], { negate: true, cls: 'pos' })}
                  </tr>
                ))}
              </React.Fragment>
            )
          }
          return (
            <table className="bridge">
              {cols.length > 0 && (
                <thead>
                  <tr>
                    <th></th>
                    {cols.map((s) => (
                      <th key={s.scenario_id} className="num">{s.persona_name}{' '}
                        <small className="muted">→ {s.target_sku_reference}</small></th>
                    ))}
                    <th className="num">Total</th>
                  </tr>
                </thead>
              )}
              <tbody>
                <tr>
                  <td>Target Microsoft licensing <small className="muted">(new per-persona bundles)</small></td>
                  {cells(cols.map((s) => s.target_spend_annual), r.target_microsoft_annual)}
                </tr>
                <tr>
                  <td>Less: existing Microsoft licensing retired <small className="muted">(current assigned)</small></td>
                  {cells(cols.map((s) => s.current_microsoft_annual), r.existing_microsoft_annual, { negate: true, cls: 'pos' })}
                </tr>
                {freed.length === 0
                  ? (
                    <tr>
                      <td>Less: existing third-party tooling freed up <small className="muted">(none)</small></td>
                      {cells(cols.map(() => 0), 0, { negate: true, cls: 'pos' })}
                    </tr>
                  )
                  : <>
                    {block('third-party redundant today', '(the quick wins — no licensing change required)', already, 'redundant_today_annual')}
                    {block('further seats on those tools retired by the move', '(covered only once the target lands)', already, 'move_unlocked_annual')}
                    {block('third-party newly retired by the move', '(capability arrives with the target)', newly, 'credited_annual')}
                  </>}
                <tr className="bridge-total">
                  <td><b>Net change in run-rate</b> <small className="muted">{saving ? `(${usd0(r.net_tco_delta_annual)}/yr saved)` : r.net_tco_delta_annual > 0 ? `(${usd0(r.net_tco_delta_annual)}/yr added)` : '(no net change)'}</small></td>
                  {cols.map((s) => (
                    <td key={s.scenario_id} className={`num ${s.delta_annual < 0 ? 'pos' : ''}`}><b>{usd(s.delta_annual)}</b></td>
                  ))}
                  <td className={`num ${saving ? 'pos' : ''}`}><b>{usd(r.net_tco_delta_annual)}</b></td>
                </tr>
              </tbody>
            </table>
          )
        })()}
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
          <p className="hint">A licensing move, not a change of role: eligible users are moved onto
            Microsoft 365 Business Premium (which covers every outcome they require), filled up to the
            300-seat tenant cap. {result.bp_swap.eligible_count} eligible · {result.bp_swap.swapped_count} applied
            {result.bp_swap.capped_count > 0 && <> · {result.bp_swap.capped_count} eligible left on their plan (300-seat cap full)</>}.</p>
          <div className="popcheck">
            <b>{result.bp_swap.swapped_users}</b> users → Business Premium
            {result.bp_swap.cap && <> ({result.bp_swap.cap.committed_seats} of {result.bp_swap.cap.max} seats)</>} ·
            combined annual delta{' '}
            <b className={result.bp_swap.swap_delta_annual < 0 ? 'pos' : ''}>{usd(result.bp_swap.swap_delta_annual)}</b>
            {result.bp_swap.swap_delta_annual < 0 ? ' (saving)' : result.bp_swap.swap_delta_annual > 0 ? ' (cost increase)' : ''}.
          </div>
        </div>
      )}

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
                <td><OverrideCell d={d} onSet={setOverride} /></td>
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

// A stored narrative block: read view with a ✎ control that opens the three
// fields (today / what's new / value) for hand-editing. The AI output is a
// draft; the SA's reviewed wording is the record — saving re-tags the row
// Estimate (human-asserted), shown as an "edited" badge instead of "AI draft".
function NarrativeBlock({ n, eid, onSaved, onError }) {
  const [editing, setEditing] = useState(false)
  const [f, setF] = useState({ today: n.today, whats_new: n.whats_new, value: n.value })
  useEffect(() => setF({ today: n.today, whats_new: n.whats_new, value: n.value }), [n.id, n.today, n.whats_new, n.value])
  const aiDraft = n.source_tag === 'AISuggestedUnconfirmed'
  async function save() {
    try {
      const r = await api.patch(`/api/engagements/${eid}/narrative/${n.id}`, f)
      setEditing(false)
      onSaved(r)
    } catch (e) { onError(e.message) }
  }
  return (
    <div className="popcheck" style={{ marginTop: '.5rem' }}>
      <div className="flex-between">
        <span><b>{n.persona}</b>{' '}
          <span className={`badge ${aiDraft ? 'muted' : 'pos'}`}>{aiDraft ? 'AI draft' : 'edited'}</span></span>
        {!editing && n.id && (
          <button className="ghost sm" title="Edit this narrative by hand"
            onClick={() => setEditing(true)}>✎ Edit</button>
        )}
      </div>
      {!editing ? (
        <>
          {n.today && <p style={{ margin: '.3rem 0' }}><b>Today: </b>{n.today}</p>}
          {n.whats_new && <p style={{ margin: '.3rem 0' }}><b>What's new: </b>{n.whats_new}</p>}
          {n.value && <p style={{ margin: '.3rem 0' }}><b>Value: </b>{n.value}</p>}
        </>
      ) : (
        <div style={{ marginTop: '.4rem' }}>
          <label>Today</label>
          <textarea rows={2} value={f.today} onChange={(e) => setF({ ...f, today: e.target.value })} />
          <label>What's new</label>
          <textarea rows={2} value={f.whats_new} onChange={(e) => setF({ ...f, whats_new: e.target.value })} />
          <label>Value</label>
          <textarea rows={2} value={f.value} onChange={(e) => setF({ ...f, value: e.target.value })} />
          <div className="toolbar" style={{ marginTop: '.4rem' }}>
            <button className="sm" onClick={save}>Save</button>
            <button className="ghost sm" onClick={() => { setEditing(false); setF({ today: n.today, whats_new: n.whats_new, value: n.value }) }}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  )
}

// The moves under the headline: one plain line per in-scope persona, showing
// the move's OWN value (quick-win credit stripped, so ① and ② never
// double-count) — "Baseline (1000) → Microsoft 365 E5 (adds $22,560/yr)".
function MoveSummary({ scenarios }) {
  if (!scenarios.length) {
    return <div className="muted">No in-scope scenarios yet — set a target bundle per persona on the Scenarios tab.</div>
  }
  return (
    <ul className="moves">
      {scenarios.map((s) => {
        const v = Number(s.move_incremental_delta_annual ?? s.delta_annual) || 0
        return (
          <li key={s.scenario_id}>
            <b>{s.persona_name}</b> ({s.headcount}) → <b>{s.target_sku_reference}</b>{' '}
            <span className={v < 0 ? 'pos' : ''}>
              ({v < 0 ? `saves ${usd0(v)}/yr` : v > 0 ? `adds ${usd0(v)}/yr` : 'cost-neutral'})
            </span>
          </li>
        )
      })}
    </ul>
  )
}

// The current classification of a disposition row ('' = unclassified).
const classificationOf = (d) =>
  d.override === 'ForceFullElimination' ? 'force'
    : d.residual_intent === 'IntendedOutOfScope' ? 'residual' : ''

function ResidualClassifier({ d, onSet, inline, onDone }) {
  // Overrides are never locked in: the classifier opens ON the current choice
  // and offers "Clear classification" to return the row to unclassified.
  const current = classificationOf(d)
  const [reason, setReason] = useState(d.override_reason || '')
  const [mode, setMode] = useState(current)
  const apply = async () => {
    if (mode === 'residual') await onSet(d.third_party_product_id, { override: 'None', residual_intent: 'IntendedOutOfScope' })
    else if (mode === 'force' && reason.trim()) await onSet(d.third_party_product_id, { override: 'ForceFullElimination', override_reason: reason })
    else if (mode === 'clear') await onSet(d.third_party_product_id, { override: 'None', residual_intent: 'None' })
    else return
    onDone?.()
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
          {current && <option value="clear">Clear classification</option>}
        </select>
      </div>
      {mode === 'force' && (
        <div style={{ flex: 2 }}>
          {!inline && <label>Override reason (required)</label>}
          <input value={reason} placeholder="Override reason (prints on readout)"
            onChange={(e) => setReason(e.target.value)} />
        </div>
      )}
      {(mode !== current || mode === 'force') && mode && (
        <button className="sm" disabled={mode === 'force' && !reason.trim()} onClick={apply}>Apply</button>
      )}
      {onDone && <button className="ghost sm" onClick={onDone}>Cancel</button>}
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

// The Override cell of the dispositions table. A classified row shows its badge
// with a ✎ control that reopens the classifier (change or clear) — a recorded
// override is operator data, editable like any other, never locked in.
function OverrideCell({ d, onSet }) {
  const [editing, setEditing] = useState(false)
  const current = classificationOf(d)
  if (editing || (!current && d.requires_residual_classification)) {
    return <ResidualClassifier inline d={d} onSet={onSet}
      onDone={editing ? () => setEditing(false) : undefined} />
  }
  if (current === 'force') {
    return (
      <span className="badge neg" title={d.override_reason}>
        Forced · {d.override_reason.slice(0, 30)}
        <button className="ghost sm" style={{ marginLeft: 4, padding: '0 .3rem' }}
          title="Change or clear this override" onClick={() => setEditing(true)}>✎</button>
      </span>
    )
  }
  if (current === 'residual') {
    return (
      <span className="badge muted">
        Intended residual
        <button className="ghost sm" style={{ marginLeft: 4, padding: '0 .3rem' }}
          title="Change or clear this classification" onClick={() => setEditing(true)}>✎</button>
      </span>
    )
  }
  // No residual and no classification — nothing to override.
  return <span className="muted">—</span>
}
