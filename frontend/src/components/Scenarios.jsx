import React, { useEffect, useState } from 'react'
import { api, usd } from '../api'

export default function Scenarios({ engagement, meta }) {
  const eid = engagement.id
  const [personas, setPersonas] = useState([])
  const [scenarios, setScenarios] = useState([])
  const [skus, setSkus] = useState([])
  const [result, setResult] = useState(null)
  const [err, setErr] = useState('')

  function load() {
    api.get(`/api/engagements/${eid}/personas`).then(setPersonas)
    api.get(`/api/engagements/${eid}/scenarios`).then(setScenarios)
    api.get('/api/catalog/skus?limit=500').then(setSkus).catch(() => {})
  }
  useEffect(() => { load() }, [eid])

  const scenarioFor = (pid) => scenarios.find((s) => s.persona_id === pid)
  const resultFor = (sid) => result?.scenarios.find((r) => r.scenario_id === sid)

  async function createScenario(pid) {
    try { await api.post(`/api/engagements/${eid}/scenarios`, { persona_id: pid, target_sku_reference: '', target_unit_price_annual: 0 }); load() }
    catch (e) { setErr(e.message) }
  }
  async function update(id, patch) {
    try { await api.patch(`/api/engagements/${eid}/scenarios/${id}`, patch); load() } catch (e) { setErr(e.message) }
  }
  async function remove(id) {
    try { await api.del(`/api/engagements/${eid}/scenarios/${id}`); load() } catch (e) { setErr(e.message) }
  }
  async function compute() {
    setErr('')
    try { setResult(await api.post(`/api/engagements/${eid}/compute`)) } catch (e) { setErr(e.message) }
  }

  // SKU references known to the seeded coverage library + catalog titles.
  const skuOptions = [...new Set(['F1', 'F3', 'E3', 'E5', ...skus.map((s) => s.sku_title)])]

  return (
    <div className="card">
      <div className="flex-between">
        <h2>Persona scenarios</h2>
        <button onClick={compute}>Recompute</button>
      </div>
      <p className="hint">One target-state plan per persona. Target price defaults to ERP
        retail; override with a CSP offer per scenario. Toggle in/out of scope and recompute —
        dispositions recompute totally.</p>
      {err && <div className="err">{err}</div>}

      <table>
        <thead><tr>
          <th>Persona</th><th className="num">HC</th><th>Target SKU</th><th className="num">Target $/seat/yr</th>
          <th>Scope</th><th className="num">Current</th><th className="num">Target</th><th className="num">Delta</th><th></th>
        </tr></thead>
        <tbody>
          {personas.map((p) => {
            const s = scenarioFor(p.id)
            if (!s) return (
              <tr key={p.id}>
                <td>{p.name}</td><td className="num">{p.headcount}</td>
                <td colSpan={6}><button className="sm" onClick={() => createScenario(p.id)}>+ Create scenario</button></td>
              </tr>
            )
            const r = resultFor(s.id)
            return (
              <tr key={p.id}>
                <td>{p.name}</td>
                <td className="num">{p.headcount}</td>
                <td>
                  <input list="sku-list" value={s.target_sku_reference} style={{ minWidth: 130 }}
                    onChange={(e) => update(s.id, { target_sku_reference: e.target.value })} />
                </td>
                <td className="num"><input type="number" style={{ width: 110 }} value={s.target_unit_price_annual}
                  onChange={(e) => update(s.id, { target_unit_price_annual: Number(e.target.value) })} /></td>
                <td><input type="checkbox" style={{ width: 'auto' }} checked={s.in_scope}
                  onChange={(e) => update(s.id, { in_scope: e.target.checked })} /></td>
                <td className="num">{r ? usd(r.current_spend_annual) : '—'}</td>
                <td className="num">{r ? usd(r.target_spend_annual) : '—'}</td>
                <td className={`num ${r && r.delta_annual >= 0 ? 'pos' : 'neg'}`}>{r ? usd(r.delta_annual) : '—'}</td>
                <td className="num"><button className="danger sm" onClick={() => remove(s.id)}>Remove</button></td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <datalist id="sku-list">
        {skuOptions.map((o) => <option key={o} value={o} />)}
      </datalist>

      {result && (
        <div className="popcheck" style={{ marginTop: '1rem' }}>
          <b>Net TCO delta (in-scope):</b>{' '}
          <span className={result.rollup.net_tco_delta_annual >= 0 ? 'pos' : 'neg'}>
            {usd(result.rollup.net_tco_delta_annual)}
          </span>
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
