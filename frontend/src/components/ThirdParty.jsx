import React, { useEffect, useState } from 'react'
import { api, usd, pct } from '../api'

export default function ThirdParty({ engagement, meta }) {
  const base = `/api/engagements/${engagement.id}/third-party`
  const [items, setItems] = useState([])
  const [err, setErr] = useState('')
  const blank = {
    name: '', vendor: '', raw_cost: 0, cost_period: 'Annual', unit_basis: 'Users',
    covered_count: 0, renewal_date: '', is_managed: false, tooling_pct: '', source_tag: 'CustomerStated',
  }
  const [form, setForm] = useState(blank)

  const load = () => api.get(base).then(setItems).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [engagement.id])

  async function add() {
    if (!form.name.trim()) return
    try {
      await api.post(base, {
        ...form,
        raw_cost: Number(form.raw_cost),
        covered_count: Number(form.covered_count),
        renewal_date: form.renewal_date || null,
        tooling_pct: form.tooling_pct === '' ? null : Number(form.tooling_pct),
      })
      setForm(blank); load()
    } catch (e) { setErr(e.message) }
  }
  async function update(id, patch) {
    try { await api.patch(`${base}/${id}`, patch); load() } catch (e) { setErr(e.message) }
  }
  async function remove(id) {
    try { await api.del(`${base}/${id}`); load() } catch (e) { setErr(e.message) }
  }

  return (
    <div className="card">
      <h2>Third-party products</h2>
      <p className="hint">The managed split keeps management cost out of the comparison.
        An unmanaged product counts at 100%; a managed product counts at its tooling
        percentage (default {pct(meta?.default_tooling_pct)}). Effective cost is what feeds displacement math.</p>
      {err && <div className="err">{err}</div>}

      <table>
        <thead><tr>
          <th>Product</th><th className="num">Cost</th><th>Period</th><th className="num">Covers</th>
          <th>Managed</th><th className="num">Tooling%</th><th className="num">Effective $/yr</th>
          <th className="num">$/unit/yr</th><th>Renewal</th><th></th>
        </tr></thead>
        <tbody>
          {items.map((t) => (
            <tr key={t.id}>
              <td><input value={t.name} onChange={(e) => update(t.id, { name: e.target.value })} style={{ minWidth: 110 }} /></td>
              <td className="num"><input type="number" style={{ width: 90 }} value={t.raw_cost}
                onChange={(e) => update(t.id, { raw_cost: Number(e.target.value) })} /></td>
              <td>
                <select value={t.cost_period} onChange={(e) => update(t.id, { cost_period: e.target.value })}>
                  {(meta?.cost_periods || []).map((s) => <option key={s}>{s}</option>)}
                </select>
              </td>
              <td className="num"><input type="number" style={{ width: 70 }} value={t.covered_count}
                onChange={(e) => update(t.id, { covered_count: Number(e.target.value) })} /></td>
              <td><input type="checkbox" style={{ width: 'auto' }} checked={t.is_managed}
                onChange={(e) => update(t.id, { is_managed: e.target.checked })} /></td>
              <td className="num">
                {t.is_managed
                  ? <input type="number" step="0.05" style={{ width: 70 }} value={t.tooling_pct}
                      onChange={(e) => update(t.id, { tooling_pct: Number(e.target.value) })} />
                  : <span className="muted">—</span>}
              </td>
              <td className="num">{usd(t.effective_annual_cost)}</td>
              <td className="num">{usd(t.per_unit_annual_cost)}</td>
              <td><input type="date" value={t.renewal_date || ''} style={{ width: 130 }}
                onChange={(e) => update(t.id, { renewal_date: e.target.value || null })} /></td>
              <td className="num"><button className="danger sm" onClick={() => remove(t.id)}>Remove</button></td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="grid c4" style={{ marginTop: '.8rem' }}>
        <div><label>Name</label>
          <input value={form.name} placeholder="Okta"
            onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
        <div><label>Cost</label>
          <input type="number" value={form.raw_cost}
            onChange={(e) => setForm({ ...form, raw_cost: e.target.value })} /></div>
        <div><label>Period</label>
          <select value={form.cost_period} onChange={(e) => setForm({ ...form, cost_period: e.target.value })}>
            {(meta?.cost_periods || []).map((s) => <option key={s}>{s}</option>)}
          </select></div>
        <div><label>Covered count</label>
          <input type="number" value={form.covered_count}
            onChange={(e) => setForm({ ...form, covered_count: e.target.value })} /></div>
        <div><label><input type="checkbox" style={{ width: 'auto', marginRight: 6 }}
          checked={form.is_managed} onChange={(e) => setForm({ ...form, is_managed: e.target.checked })} />Managed (tool + management)</label></div>
        <div><label>Tooling % override</label>
          <input type="number" step="0.05" value={form.tooling_pct} placeholder="default"
            onChange={(e) => setForm({ ...form, tooling_pct: e.target.value })} /></div>
        <div><label>Renewal date</label>
          <input type="date" value={form.renewal_date}
            onChange={(e) => setForm({ ...form, renewal_date: e.target.value })} /></div>
        <div style={{ display: 'flex', alignItems: 'flex-end' }}>
          <button onClick={add}>Add product</button></div>
      </div>
    </div>
  )
}
