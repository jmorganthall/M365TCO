import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api'

// Deleting a workshop removes everything in it and can't be undone, so the person
// types the customer's name exactly (the word "delete" when it has none). The
// server checks the same thing (DELETE …?confirm_name=), so nothing else can
// delete a workshop by accident.
export default function DeleteEngagement({ engagement, onDeleted, onCancel }) {
  const phrase = (engagement.customer_name || '').trim() || 'delete'
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const box = useRef(null)
  useEffect(() => { box.current?.focus() }, [])
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onCancel() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])
  const matches = typed.trim() === phrase

  async function remove() {
    if (!matches || busy) return
    setBusy(true); setErr('')
    try {
      await api.del(`/api/engagements/${engagement.id}?confirm_name=${encodeURIComponent(typed.trim())}`)
      onDeleted()
    } catch (e) { setErr(e.message); setBusy(false) }
  }

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="delete-title">
        <h2 id="delete-title">Delete {engagement.customer_name || 'this untitled workshop'}?</h2>
        <p>This permanently deletes the workshop and everything in it: groups, licences, tools, plans,
          answers, snapshots and the delivered record. It can't be undone.</p>
        <label htmlFor="delete-confirm">Type <b className="modal-phrase">{phrase}</b> to confirm</label>
        <input id="delete-confirm" ref={box} value={typed} autoComplete="off" spellCheck={false}
          onChange={(e) => setTyped(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && remove()} />
        {typed && !matches && <div className="muted modal-hint">That doesn't match yet. It must be exactly as shown, including capitals.</div>}
        {err && <div className="err">{err}</div>}
        <div className="modal-actions">
          <button className="ghost" onClick={onCancel}>Keep it</button>
          <button className="destructive" disabled={!matches || busy} onClick={remove}>
            {busy ? 'Deleting…' : 'Delete workshop'}</button>
        </div>
      </div>
    </div>
  )
}
