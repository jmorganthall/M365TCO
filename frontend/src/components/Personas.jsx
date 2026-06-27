import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Personas({ engagement, meta }) {
  const base = `/api/engagements/${engagement.id}/personas`
  const [items, setItems] = useState([])
  const [form, setForm] = useState({ name: '', headcount: 0, description: '', source_tag: 'CustomerStated' })
  const [err, setErr] = useState('')

  const load = () => api.get(base).then(setItems).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [engagement.id])

  async function add() {
    if (!form.name.trim()) return
    try {
      await api.post(base, { ...form, headcount: Number(form.headcount) })
      setForm({ name: '', headcount: 0, description: '', source_tag: 'CustomerStated' })
      load()
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
      <h2>Personas and headcounts</h2>
      <p className="hint">Define the populations you will model. Each persona gets one
        target scenario later.</p>
      {err && <div className="err">{err}</div>}

      <table>
        <thead><tr><th>Name</th><th className="num">Headcount</th><th>Source</th><th></th></tr></thead>
        <tbody>
          {items.map((p) => (
            <tr key={p.id}>
              <td><input value={p.name} onChange={(e) => update(p.id, { name: e.target.value })} /></td>
              <td className="num"><input type="number" value={p.headcount}
                onChange={(e) => update(p.id, { headcount: Number(e.target.value) })} style={{ width: 90 }} /></td>
              <td>
                <select value={p.source_tag} onChange={(e) => update(p.id, { source_tag: e.target.value })}>
                  {(meta?.source_tags || []).map((s) => <option key={s}>{s}</option>)}
                </select>
              </td>
              <td className="num"><button className="danger sm" onClick={() => remove(p.id)}>Remove</button></td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="toolbar" style={{ marginTop: '.8rem' }}>
        <div style={{ flex: 2 }}>
          <label>New persona</label>
          <input value={form.name} placeholder="Knowledge Worker"
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            onKeyDown={(e) => e.key === 'Enter' && add()} />
        </div>
        <div>
          <label>Headcount</label>
          <input type="number" value={form.headcount}
            onChange={(e) => setForm({ ...form, headcount: e.target.value })} />
        </div>
        <button onClick={add}>Add persona</button>
      </div>
    </div>
  )
}
