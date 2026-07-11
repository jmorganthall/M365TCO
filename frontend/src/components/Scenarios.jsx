import React, { useEffect, useState } from 'react'
import { api, usd } from '../api'
import BundleAnalysis from './BundleAnalysis.jsx'
import SkuCombobox from './SkuCombobox.jsx'

// Target prices are stored annualized; the UI edits per-seat MONTHLY.
const annualToMonthly = (a) => (a ? Math.round((Number(a) / 12) * 100) / 100 : 0)
const monthlyToAnnual = (m) => Math.round(Number(m || 0) * 12 * 100) / 100

function MonthlyInput({ annual, onCommit, style }) {
  const [val, setVal] = useState(annualToMonthly(annual))
  useEffect(() => { setVal(annualToMonthly(annual)) }, [annual])
  return (
    <input type="number" style={style} value={val}
      onChange={(e) => setVal(e.target.value)}
      onBlur={() => onCommit(monthlyToAnnual(val))} />
  )
}

// net annual per seat = (base + add-ons) × (1 − discount), matching the engine.
const netAnnual = (s) => {
  const base = Number(s.target_unit_price_annual) || 0
  const addons = (s.addons || []).reduce((sum, a) => sum + (Number(a.unit_price_annual) || 0), 0)
  return (base + addons) * (1 - (Number(s.target_discount_pct) || 0))
}

// One scenario as an expandable line item: base bundle + net $/seat/mo up top;
// base price, discount, and add-on bundles (composed) in the expander.
function ScenarioRow({ p, s, r, bundles, basis, update, remove, onAnalyze, swapEnabled, swapRow }) {
  const [open, setOpen] = useState(false)
  const bundleName = (id) => bundles.find((b) => b.id === id)?.name || id
  const payload = () => (s.addons || []).map((a) => ({ bundle_id: a.bundle_id, unit_price_annual: a.unit_price_annual }))
  // Resolve the scenario's base bundle (by name/key) so only add-ons ELIGIBLE for
  // that base are offered (the composition logic layer). À-la-carte add-ons layer
  // onto anything; if the base can't be resolved yet, don't filter.
  const baseBundle = bundles.find((b) => b.kind === 'bundle'
    && (b.name === s.target_sku_reference || b.key === s.target_sku_reference))
  const eligibleForBase = (b) => b.alacarte || !baseBundle
    || (b.eligible_base_ids || []).includes(baseBundle.id)
  const available = bundles.filter((b) => b.kind === 'addon'
    && !(s.addons || []).some((a) => a.bundle_id === b.id) && eligibleForBase(b))

  const addAddon = (bid) => {
    const b = bundles.find((x) => x.id === bid)
    update(s.id, { addons: [...payload(), { bundle_id: bid, unit_price_annual: b?.list_price_annual || 0 }] })
  }
  const removeAddon = (bid) => update(s.id, { addons: payload().filter((a) => a.bundle_id !== bid) })
  const setAddonPrice = (bid, annual) =>
    update(s.id, { addons: payload().map((a) => (a.bundle_id === bid ? { ...a, unit_price_annual: annual } : a)) })

  return (
    <>
      <tr>
        <td><button className="ghost sm" onClick={() => setOpen(!open)}>{open ? '▾' : '▸'}</button></td>
        <td>{p.name}</td>
        <td className="num">{p.headcount}</td>
        <td>
          <SkuCombobox value={s.target_sku_reference} style={{ minWidth: 130 }}
            segment={basis.segment} term={basis.term} billing={basis.billing}
            onChange={(v) => update(s.id, { target_sku_reference: v })}
            onSelectSku={(sku) => sku && update(s.id, { target_unit_price_annual: sku.annual_unit_price })} />
          {(s.addons || []).length > 0 && (
            <div className="pill-list" style={{ marginTop: 3 }}>
              {s.addons.map((a) => <span key={a.bundle_id} className="badge muted">+ {bundleName(a.bundle_id)}</span>)}
            </div>
          )}
          {swapEnabled && swapRow && (
            <div style={{ marginTop: 3, fontSize: '.74rem' }}>
              {swapRow.applied ? (
                <span className="badge pos">
                  → Business Premium (swap)
                  <button className="ghost sm" style={{ marginLeft: 4, padding: '0 .3rem' }}
                    title="Keep this persona's own target instead"
                    onClick={() => update(s.id, { bp_swap_optout: true })}>opt out</button>
                </span>
              ) : swapRow.eligible ? (
                <span className="badge muted">swap opted out{' '}
                  <button className="ghost sm" style={{ marginLeft: 4, padding: '0 .3rem' }}
                    onClick={() => update(s.id, { bp_swap_optout: false })}>re-include</button>
                </span>
              ) : (
                <span className="muted" title="Business Premium doesn't cover every outcome this persona requires">not BP-eligible</span>
              )}
            </div>
          )}
        </td>
        <td className="num">{usd(netAnnual(s) / 12)}</td>
        <td><input type="checkbox" style={{ width: 'auto' }} checked={s.in_scope}
          onChange={(e) => update(s.id, { in_scope: e.target.checked })} /></td>
        <td className="num">{r ? usd(r.current_spend_annual) : '—'}</td>
        <td className="num">{r ? usd(r.target_spend_annual) : '—'}</td>
        <td className={`num ${r && r.delta_annual < 0 ? 'pos' : ''}`}>{r ? usd(r.delta_annual) : '—'}</td>
        <td className="num">
          <button className="ghost sm" onClick={onAnalyze}>⚡</button>{' '}
          <button className="danger sm" onClick={() => remove(s.id)}>Remove</button>
        </td>
      </tr>
      {open && (
        <tr>
          <td></td>
          <td colSpan={9} style={{ background: 'var(--panel2)' }}>
            <div className="grid c3" style={{ padding: '.4rem 0' }}>
              <div><label>Base $/seat/mo</label>
                <MonthlyInput annual={s.target_unit_price_annual}
                  onCommit={(annual) => update(s.id, { target_unit_price_annual: annual })} />
                <small className="src">Auto-filled from the selected SKU's list price.</small></div>
              <div><label>Discount</label>
                <input type="number" step="0.05" value={s.target_discount_pct ?? ''} placeholder="e.g. 0.15"
                  onChange={(e) => update(s.id, { target_discount_pct: e.target.value === '' ? null : Number(e.target.value) })} />
                <small className="src">Fraction off the composed list (0.15 = 15%).</small></div>
              <div><label>Net $/seat/mo</label>
                <div className="muted" style={{ paddingTop: '.35rem', fontSize: '.95rem' }}>{usd(netAnnual(s) / 12)}</div>
                <small className="src">(base + add-ons) × (1 − discount).</small></div>
            </div>
            <div style={{ marginTop: '.3rem' }}>
              <label>Add-ons (layer onto the base — outcomes union, prices sum)</label>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '.35rem' }}>
                {(s.addons || []).map((a) => (
                  <div key={a.bundle_id} className="toolbar" style={{ gap: '.4rem' }}>
                    <span className="badge muted" style={{ minWidth: 190 }}>{bundleName(a.bundle_id)}</span>
                    <MonthlyInput annual={a.unit_price_annual} style={{ width: 110 }}
                      onCommit={(annual) => setAddonPrice(a.bundle_id, annual)} />
                    <span className="muted" style={{ fontSize: '.72rem' }}>$/seat/mo</span>
                    <button className="danger sm" onClick={() => removeAddon(a.bundle_id)}>×</button>
                  </div>
                ))}
                {(s.addons || []).length === 0 && <span className="muted" style={{ fontSize: '.78rem' }}>No add-ons.</span>}
                {available.length > 0 && (
                  <select value="" onChange={(e) => e.target.value && addAddon(e.target.value)} style={{ maxWidth: 280 }}>
                    <option value="">+ add an add-on bundle…</option>
                    {available.map((b) => <option key={b.id} value={b.id}>{b.name}{b.base_name ? ` (→ ${b.base_name})` : ''}</option>)}
                  </select>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

export default function Scenarios({ engagement, meta }) {
  const eid = engagement.id
  const [personas, setPersonas] = useState([])
  const [scenarios, setScenarios] = useState([])
  const [bundles, setBundles] = useState([])
  const [result, setResult] = useState(null)
  const [err, setErr] = useState('')
  const [analyzePersona, setAnalyzePersona] = useState(null)
  const [swapEnabled, setSwapEnabled] = useState(!!engagement.bp_swap_enabled)
  const [capEnabled, setCapEnabled] = useState(!!engagement.business_cap_enabled)

  function load() {
    api.get(`/api/engagements/${eid}/personas`).then(setPersonas)
    api.get(`/api/engagements/${eid}/scenarios`).then(setScenarios)
  }
  useEffect(() => {
    load()
    api.get('/api/catalog/bundles').then(setBundles).catch(() => {})
    compute()
  }, [eid])

  const scenarioFor = (pid) => scenarios.find((s) => s.persona_id === pid)
  const resultFor = (sid) => result?.scenarios.find((r) => r.scenario_id === sid)
  const swapFor = (sid) => result?.bp_swap?.scenarios?.find((x) => x.scenario_id === sid)

  async function toggleSwap(on) {
    setSwapEnabled(on)  // optimistic
    setErr('')
    try { await api.patch(`/api/engagements/${eid}`, { bp_swap_enabled: on }); compute() }
    catch (e) { setErr(e.message); setSwapEnabled(!on) }
  }
  async function toggleCap(on) {
    setCapEnabled(on)  // optimistic
    setErr('')
    try { await api.patch(`/api/engagements/${eid}`, { business_cap_enabled: on }); compute() }
    catch (e) { setErr(e.message); setCapEnabled(!on) }
  }
  // The tenant seat caps (Business ≤ 300) as evaluated by the readout, for the
  // "how many Business seats are already recommended" indicator next to the toggle.
  const seatCaps = (result?.license_limits || []).filter((l) => l.limit_type === 'max_total_seats')
  // Engagement pricing-basis default, so a target bundle resolves to the right
  // segment/term variant's list price (same chain as Current Licensing).
  const basis = {
    segment: engagement.default_segment, term: engagement.default_term_duration,
    billing: engagement.default_billing_plan,
  }

  async function createScenario(pid) {
    try { await api.post(`/api/engagements/${eid}/scenarios`, { persona_id: pid, target_sku_reference: '', target_unit_price_annual: 0 }); load() }
    catch (e) { setErr(e.message) }
  }
  async function update(id, patch) {
    try { await api.patch(`/api/engagements/${eid}/scenarios/${id}`, patch); await load(); compute() }
    catch (e) { setErr(e.message) }
  }
  async function remove(id) {
    try { await api.del(`/api/engagements/${eid}/scenarios/${id}`); load() } catch (e) { setErr(e.message) }
  }
  async function compute() {
    setErr('')
    try { setResult(await api.post(`/api/engagements/${eid}/compute`)) } catch (e) { setErr(e.message) }
  }

  async function applyBundle(persona, { sku_reference, price, addons }) {
    setErr('')
    try {
      const existing = scenarioFor(persona.id)
      const body = {
        target_sku_reference: sku_reference, target_unit_price_annual: price,
        in_scope: true, addons: addons || [],
      }
      if (existing) await api.patch(`/api/engagements/${eid}/scenarios/${existing.id}`, body)
      else await api.post(`/api/engagements/${eid}/scenarios`, { persona_id: persona.id, ...body })
      setAnalyzePersona(null)
      await load()
      compute()
    } catch (e) { setErr(e.message) }
  }

  return (
    <div className="card">
      <div className="flex-between">
        <h2>Persona scenarios</h2>
        <button onClick={compute}>Recompute</button>
      </div>
      <p className="hint">One target-state plan per persona. The future state is a base bundle
        plus optional add-ons (E5 Security, etc.) — the engine unions their outcomes and sums
        their prices; a discount applies to the total. Prices are per-seat monthly.</p>

      <div className="popcheck" style={{ display: 'flex', alignItems: 'center', gap: '.5rem', flexWrap: 'wrap' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: '.4rem', margin: 0 }}>
          <input type="checkbox" checked={capEnabled} onChange={(e) => toggleCap(e.target.checked)} />
          <b>Use Microsoft 365 Business plans, capped at the tenant seat limit</b>
        </label>
        <span className="muted" style={{ fontSize: '.8rem' }}>
          Best-bundle recommendations respect the Business seat cap (Basic/Standard/Premium ≤ 300/tenant);
          a persona that would push the tenant over gets the next-best plan instead.
          {capEnabled && seatCaps.map((l) => (
            <span key={l.id}> · Business seats recommended:{' '}
              <b className={l.target_over_by > 0 ? 'neg' : ''}>{l.target_seats}</b> of {l.max_quantity}</span>
          ))}
        </span>
      </div>

      <div className="popcheck" style={{ display: 'flex', alignItems: 'center', gap: '.5rem', flexWrap: 'wrap' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: '.4rem', margin: 0 }}>
          <input type="checkbox" checked={swapEnabled} onChange={(e) => toggleSwap(e.target.checked)} />
          <b>Swap eligible users to Microsoft 365 Business Premium to save</b>
        </label>
        <span className="muted" style={{ fontSize: '.8rem' }}>
          Every capability-eligible persona inherits the swap; deselect per persona below. Bounded by the Business seat cap.
          {swapEnabled && result?.bp_swap && (
            <> · <b>{result.bp_swap.swapped_count}</b> swapped ({result.bp_swap.swapped_users} users),
              combined delta <b className={result.bp_swap.swap_delta_annual < 0 ? 'pos' : ''}>{usd(result.bp_swap.swap_delta_annual)}</b>/yr</>
          )}
        </span>
      </div>
      {err && <div className="err">{err}</div>}

      <table>
        <thead><tr>
          <th></th><th>Persona</th><th className="num">HC</th><th>Base bundle</th>
          <th className="num">Net $/seat/mo</th><th>Scope</th><th className="num">Current</th>
          <th className="num">Target</th><th className="num">Delta</th><th></th>
        </tr></thead>
        <tbody>
          {personas.map((p) => {
            const s = scenarioFor(p.id)
            if (!s) return (
              <tr key={p.id}>
                <td></td><td>{p.name}</td><td className="num">{p.headcount}</td>
                <td colSpan={7}>
                  <button className="sm" onClick={() => createScenario(p.id)}>+ Create scenario</button>{' '}
                  <button className="ghost sm" onClick={() => setAnalyzePersona(p)}>⚡ Best bundle</button>
                </td>
              </tr>
            )
            return (
              <ScenarioRow key={p.id} p={p} s={s} r={resultFor(s.id)} bundles={bundles} basis={basis}
                update={update} remove={remove} onAnalyze={() => setAnalyzePersona(p)}
                swapEnabled={swapEnabled} swapRow={swapFor(s.id)} />
            )
          })}
        </tbody>
      </table>

      {analyzePersona && (
        <BundleAnalysis engagement={engagement} persona={analyzePersona}
          onApply={(composed) => applyBundle(analyzePersona, composed)}
          onClose={() => setAnalyzePersona(null)} />
      )}
      {result && (
        <div className="popcheck" style={{ marginTop: '1rem' }}>
          <b>Net TCO delta (in-scope):</b>{' '}
          <span className={result.rollup.net_tco_delta_annual < 0 ? 'pos' : ''}>
            {usd(result.rollup.net_tco_delta_annual)}
          </span>{' '}
          <small className="muted">{result.rollup.net_tco_delta_annual < 0 ? '(saving)' : result.rollup.net_tco_delta_annual > 0 ? '(cost increase)' : ''}</small>
          {' · '}In-scope headcount {result.rollup.population_check.in_scope_persona_headcount}
          {' · '}covered population {result.rollup.population_check.third_party_covered_population}
        </div>
      )}

      {result && result.scenarios.some((s) => s.offsets?.length > 0) && (
        <div style={{ marginTop: '1rem' }}>
          <h2 style={{ fontSize: '.95rem' }}>Offset detail</h2>
          {result.scenarios.filter((s) => s.offsets?.length).map((s) => (
            <div key={s.scenario_id} className="muted" style={{ fontSize: '.82rem' }}>
              <b style={{ color: 'var(--ink)' }}>{s.persona_name}</b> displaces:{' '}
              {s.offsets.map((o) => `${o.third_party_product_name} (${o.credited_units} × ${usd(o.per_unit_annual_cost)} = ${usd(o.credited_offset_annual)})`).join(', ')}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
