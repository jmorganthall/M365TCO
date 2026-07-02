import React, { useEffect, useState } from 'react'
import { api, usd } from '../api'
import SkuCombobox from './SkuCombobox.jsx'

export default function CurrentLicensing({ engagement, meta }) {
  const base = `/api/engagements/${engagement.id}/current-licenses`
  const [items, setItems] = useState([])
  const [personas, setPersonas] = useState([])
  const [err, setErr] = useState('')
  const blank = {
    sku_reference: '', quantity_purchased: 0, quantity_assigned: 0,
    unit_price_paid_annual: 0, price_basis: 'Unknown', persona_id: '', source_tag: 'CustomerStated',
  }
  const [form, setForm] = useState(blank)

  const load = () => {
    api.get(base).then(setItems).catch((e) => setErr(e.message))
    api.get(`/api/engagements/${engagement.id}/personas`).then(setPersonas)
  }
  useEffect(() => { load() }, [engagement.id])

  async function add() {
    if (!form.sku_reference.trim()) return
    try {
      await api.post(base, {
        ...form,
        persona_id: form.persona_id || null,
        quantity_purchased: Number(form.quantity_purchased),
        quantity_assigned: Number(form.quantity_assigned),
        unit_price_paid_annual: Number(form.unit_price_paid_annual),
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
  const personaName = (id) => personas.find((p) => p.id === id)?.name || '—'

  return (
    <div className="card">
      <h2>Current Microsoft licensing</h2>
      <p className="hint">Model on <b>assigned</b>, not purchased — shelfware is a savings
        source. Enter the actual price paid per seat (absolute, EA, CSP, or negotiated);
        don't assume ERP.</p>
      {err && <div className="err">{err}</div>}

      <table>
        <thead><tr>
          <th>SKU</th><th className="num">Purchased</th><th className="num">Assigned</th>
          <th className="num">Annual $/seat</th><th>Basis</th><th>Persona</th><th></th>
        </tr></thead>
        <tbody>
          {items.map((l) => (
            <tr key={l.id}>
              <td><SkuCombobox value={l.sku_reference} onChange={(v) => update(l.id, { sku_reference: v })} /></td>
              <td className="num"><input type="number" style={{ width: 80 }} value={l.quantity_purchased}
                onChange={(e) => update(l.id, { quantity_purchased: Number(e.target.value) })} /></td>
              <td className="num"><input type="number" style={{ width: 80 }} value={l.quantity_assigned}
                onChange={(e) => update(l.id, { quantity_assigned: Number(e.target.value) })} /></td>
              <td className="num"><input type="number" style={{ width: 100 }} value={l.unit_price_paid_annual}
                onChange={(e) => update(l.id, { unit_price_paid_annual: Number(e.target.value) })} /></td>
              <td>
                <select value={l.price_basis} onChange={(e) => update(l.id, { price_basis: e.target.value })}>
                  {(meta?.price_basis || []).map((s) => <option key={s}>{s}</option>)}
                </select>
              </td>
              <td>
                <select value={l.persona_id || ''} onChange={(e) => update(l.id, { persona_id: e.target.value || null })}>
                  <option value="">—</option>
                  {personas.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </td>
              <td className="num"><button className="danger sm" onClick={() => remove(l.id)}>Remove</button></td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="grid c4" style={{ marginTop: '.8rem' }}>
        <div><label>SKU reference</label>
          <SkuCombobox value={form.sku_reference} placeholder="Microsoft 365 E3"
            onChange={(v) => setForm({ ...form, sku_reference: v })} /></div>
        <div><label>Assigned</label>
          <input type="number" value={form.quantity_assigned}
            onChange={(e) => setForm({ ...form, quantity_assigned: e.target.value })} /></div>
        <div><label>Annual $/seat paid</label>
          <input type="number" value={form.unit_price_paid_annual}
            onChange={(e) => setForm({ ...form, unit_price_paid_annual: e.target.value })} /></div>
        <div><label>Persona</label>
          <select value={form.persona_id} onChange={(e) => setForm({ ...form, persona_id: e.target.value })}>
            <option value="">—</option>
            {personas.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select></div>
      </div>
      <button style={{ marginTop: '.6rem' }} onClick={add}>Add license line</button>
    </div>
  )
}
