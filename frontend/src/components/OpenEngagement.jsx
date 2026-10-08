import React, { useEffect, useState } from 'react'
import { api } from '../api'

// The engagement picker. The app is run on a shared screen, so it never shows a
// list of customers: nothing appears until the operator types part of the name
// they want, and each row carries only that name (docs/WALKTHROUGH.md §2).
const MIN_CHARS = 2

export default function OpenEngagement({ onOpen, onDuplicate, onDelete }) {
  const [q, setQ] = useState('')
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState('')
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    const term = q.trim()
    setErr('')
    if (term.length < MIN_CHARS) { setRows(null); return }
    let stale = false
    const t = setTimeout(() => {
      api.get(`/api/engagements?q=${encodeURIComponent(term)}&limit=8`)
        .then((r) => { if (!stale) setRows(r) })
        .catch((e) => { if (!stale) setErr(e.message) })
    }, 200)
    return () => { stale = true; clearTimeout(t) }
  }, [q, reloadKey])

  async function duplicate(id) { await onDuplicate(id) }
  async function remove(row) {
    if (await onDelete(row)) setReloadKey((k) => k + 1)
  }

  return (
    <div className="card landing">
      <h2>Open an engagement</h2>
      <p className="hint">Type part of the customer's name. Nothing is listed until you
        type, so other customers never appear on a shared screen.</p>
      <input type="search" autoComplete="off" spellCheck={false} value={q}
        placeholder="Customer name…" aria-label="Search engagements by customer name"
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && rows?.length === 1) onOpen(rows[0]) }} />
      {err && <div className="err">{err}</div>}
      {rows && rows.length === 0 && (
        <p className="muted" style={{ margin: '.6rem 0 0' }}>No engagement matches “{q.trim()}”.</p>
      )}
      {rows && rows.length > 0 && (
        <div className="picker-list">
          {rows.map((r) => (
            <div key={r.id} className="picker-item">
              <button className="picker-name" onClick={() => onOpen(r)}>{r.customer_name || 'Untitled'}</button>
              <span className="muted picker-date">updated {new Date(r.updated_at).toLocaleDateString()}</span>
              <div className="picker-actions">
                <button title="Duplicate" aria-label="Duplicate" onClick={() => duplicate(r.id)}>⧉</button>
                <button title="Delete" aria-label="Delete" onClick={() => remove(r)}>×</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
