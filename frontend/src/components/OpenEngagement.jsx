import React, { useEffect, useState } from 'react'
import { api } from '../api'

// The engagement picker. The app is run on a shared screen, so the list of
// customers stays closed: a drop-down reading "Select a customer…" shows the names
// only when someone opens it, and each option carries only the name
// (docs/WALKTHROUGH.md §2). Duplicate and delete live in the engagement's Tools menu.
const MAX_LISTED = 1000

export default function OpenEngagement({ onOpen }) {
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.get(`/api/engagements?limit=${MAX_LISTED}`)
      .then((r) => setRows([...r].sort((a, b) =>
        (a.customer_name || '').localeCompare(b.customer_name || '', undefined, { sensitivity: 'base' }))))
      .catch((e) => setErr(e.message))
  }, [])

  function pick(id) {
    const row = rows?.find((r) => r.id === id)
    if (row) onOpen(row)
  }

  return (
    <div className="card landing">
      <h2>Open an engagement</h2>
      <select value="" aria-label="Select a customer" disabled={!rows || rows.length === 0}
        onChange={(e) => pick(e.target.value)}>
        <option value="" disabled>
          {!rows ? 'Loading customers…' : rows.length === 0 ? 'No engagements yet' : 'Select a customer…'}
        </option>
        {(rows || []).map((r) => (
          <option key={r.id} value={r.id}>{r.customer_name || 'Untitled'}</option>
        ))}
      </select>
      {err && <div className="err">{err}</div>}
    </div>
  )
}
