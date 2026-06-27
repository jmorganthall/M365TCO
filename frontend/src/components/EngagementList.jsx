import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function EngagementList({ onOpen }) {
  const [items, setItems] = useState([])
  const [name, setName] = useState('')
  const [tooling, setTooling] = useState(0.30)
  const [err, setErr] = useState('')

  async function load() {
    try { setItems(await api.get('/api/engagements')) } catch (e) { setErr(e.message) }
  }
  useEffect(() => { load() }, [])

  async function create() {
    if (!name.trim()) return
    try {
      const e = await api.post('/api/engagements', {
        customer_name: name, global_tooling_pct: Number(tooling),
      })
      setName('')
      await load()
      onOpen(e)
    } catch (e) { setErr(e.message) }
  }

  async function duplicate(id) {
    try { await api.post(`/api/engagements/${id}/duplicate`); load() } catch (e) { setErr(e.message) }
  }
  async function remove(id) {
    if (!confirm('Delete this engagement and all its data?')) return
    try { await api.del(`/api/engagements/${id}`); load() } catch (e) { setErr(e.message) }
  }

  return (
    <>
      <div className="card">
        <h2>New engagement</h2>
        <p className="hint">Creates an engagement and seeds the Blue Mantis outcome
          library + Microsoft SKU coverage. Everything is editable mid-workshop.</p>
        <div className="toolbar">
          <div style={{ flex: 2 }}>
            <label>Customer name</label>
            <input value={name} onChange={(e) => setName(e.target.value)}
              placeholder="Acme Corp" onKeyDown={(e) => e.key === 'Enter' && create()} />
          </div>
          <div>
            <label>Global tooling split</label>
            <input type="number" step="0.05" min="0" max="1" value={tooling}
              onChange={(e) => setTooling(e.target.value)} />
          </div>
          <button onClick={create}>Create</button>
        </div>
        {err && <div className="err">{err}</div>}
      </div>

      <div className="card">
        <h2>Engagements</h2>
        {items.length === 0 && <p className="muted">No engagements yet.</p>}
        {items.length > 0 && (
          <table>
            <thead><tr><th>Customer</th><th>Market</th><th>Tooling split</th><th></th></tr></thead>
            <tbody>
              {items.map((e) => (
                <tr key={e.id}>
                  <td><a onClick={() => onOpen(e)} style={{ cursor: 'pointer' }}>
                    {e.customer_name || 'Untitled'}</a></td>
                  <td>{e.market}/{e.currency}</td>
                  <td>{Math.round(e.global_tooling_pct * 100)}%</td>
                  <td className="num">
                    <button className="ghost sm" onClick={() => onOpen(e)}>Open</button>{' '}
                    <button className="ghost sm" onClick={() => duplicate(e.id)}>Duplicate</button>{' '}
                    <button className="danger sm" onClick={() => remove(e.id)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
