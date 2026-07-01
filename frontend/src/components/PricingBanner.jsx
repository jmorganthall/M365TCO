import React, { useEffect, useState } from 'react'
import { api } from '../api'

// Freshness banner (PRD FR-UI-1). Fresh shows nothing. Aging = amber. Stale =
// red with a sign-in-to-refresh prompt. Only shown when pricing sync is
// configured (CSV-only operators aren't nagged). The check is local, no API call.
export default function PricingBanner({ onOpenSettings }) {
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => { api.get('/api/pricesync/status').then(setStatus).catch(() => {}) }, [])

  async function refresh() {
    setBusy(true); setErr('')
    try {
      await api.post('/api/pricesync/refresh')  // CSP server-side token exchange
      const s = await api.get('/api/pricesync/status')
      setStatus(s)
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  if (!status || !status.configured || status.state === 'fresh') return null

  const stale = status.state === 'stale'
  const bg = stale ? 'rgba(248,113,113,.14)' : 'rgba(251,191,36,.14)'
  const border = stale ? 'var(--neg)' : 'var(--warn)'
  const text = stale
    ? 'Pricing may be outdated. Sign in to refresh.'
    : 'Refresh recommended — pricing is aging.'

  return (
    <div className="card" style={{ background: bg, borderColor: border, marginBottom: '1rem' }}>
      <div className="flex-between">
        <div>
          <b className={stale ? 'neg' : 'warn'}>{text}</b>{' '}
          <span className="muted" style={{ fontSize: '.82rem' }}>
            {status.data_month ? `Data month ${status.data_month}` : 'No cached sheet'}
            {status.age_days != null ? ` · ${status.age_days} days old` : ''}
          </span>
        </div>
        <div className="row" style={{ gap: '.4rem' }}>
          <button className="sm" disabled={busy} onClick={refresh}>Refresh pricing</button>
          {onOpenSettings && <button className="ghost sm" onClick={onOpenSettings}>Settings</button>}
        </div>
      </div>
      {err && <div className="err">{err}</div>}
    </div>
  )
}

// Small pricing-provenance badge for outputs derived from pricing (FR-UI-2).
export function PricingBadge() {
  const [status, setStatus] = useState(null)
  useEffect(() => { api.get('/api/pricesync/status').then(setStatus).catch(() => {}) }, [])
  if (!status) return null
  const cls = status.state === 'stale' ? 'neg' : status.state === 'aging' ? 'warn' : 'muted'
  return (
    <span className={`badge ${cls}`} title={(status.reasons || []).join(' ')}>
      Pricing: {status.data_month || 'not set'} · {status.state}
    </span>
  )
}
