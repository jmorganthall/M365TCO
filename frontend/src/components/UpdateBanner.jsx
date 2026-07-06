import React, { useEffect, useState } from 'react'
import { api } from '../api'

// "Update available" banner: shown when the running image isn't the latest
// published (best-effort, fail-silent on the backend). Dismissal is remembered
// per latest-version, so a newer update re-surfaces it.
export default function UpdateBanner() {
  const [info, setInfo] = useState(null)
  const [dismissed, setDismissed] = useState(false)
  useEffect(() => { api.get('/api/version').then(setInfo).catch(() => {}) }, [])

  const up = info?.update
  if (!up?.available) return null
  const key = `update-dismissed-${up.latest}`
  if (dismissed || localStorage.getItem(key)) return null
  const dismiss = () => { localStorage.setItem(key, '1'); setDismissed(true) }
  const running = info.running.short_sha || info.running.version || 'unknown'

  return (
    <div className="card" style={{ background: 'rgba(251,191,36,.14)', borderColor: 'var(--warn)', marginBottom: '1rem' }}>
      <div className="flex-between">
        <div>
          <b className="warn">Update available</b>{' '}
          <span className="muted" style={{ fontSize: '.82rem' }}>
            Running {running} · latest {up.latest} · pull the newest image to update.
          </span>
        </div>
        <div className="row" style={{ gap: '.4rem' }}>
          {up.url && <button className="sm" onClick={() => window.open(up.url, '_blank', 'noreferrer')}>View changes</button>}
          <button className="ghost sm" onClick={dismiss}>Dismiss</button>
        </div>
      </div>
    </div>
  )
}

// Persistent version line for the Settings panel.
export function VersionInfo() {
  const [info, setInfo] = useState(null)
  useEffect(() => { api.get('/api/version').then(setInfo).catch(() => {}) }, [])
  if (!info) return null
  const run = info.running
  const label = run.version || run.short_sha || 'dev (unversioned build)'
  const up = info.update
  return (
    <div className="card">
      <h2>Version</h2>
      <div className="muted" style={{ fontSize: '.85rem' }}>
        Running <b style={{ color: 'var(--ink)' }}>{label}</b>{run.ref ? ` · ${run.ref}` : ''}{' · '}
        {!run.known
          ? 'local/dev build — update check disabled'
          : up?.available
            ? <span className="warn">update available → latest {up.latest}{' '}
                {up.url && <a href={up.url} target="_blank" rel="noreferrer">view changes</a>}</span>
            : <span className="pos">up to date</span>}
      </div>
    </div>
  )
}
