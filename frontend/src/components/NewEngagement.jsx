import React, { useState } from 'react'
import { api } from '../api'

// Landing create form. The tooling split is no longer here — it's a global
// default under Settings that new engagements inherit.
export default function NewEngagement({ onCreated }) {
  const [name, setName] = useState('')
  const [err, setErr] = useState('')

  async function create() {
    if (!name.trim()) return
    try {
      const e = await api.post('/api/engagements', { customer_name: name })
      setName('')
      onCreated(e)
    } catch (e) { setErr(e.message) }
  }

  return (
    <div className="card landing">
      <h2>New engagement</h2>
      <p className="hint">Creates an engagement and seeds the default outcome library
        + Microsoft SKU coverage. It inherits the global tooling split (editable under
        Settings). Everything is editable mid-workshop.</p>
      <div className="toolbar">
        <div style={{ flex: 2 }}>
          <label>Customer name</label>
          <input value={name} placeholder="Acme Corp"
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && create()} />
        </div>
        <button onClick={create}>Create</button>
      </div>
      {err && <div className="err">{err}</div>}
    </div>
  )
}
