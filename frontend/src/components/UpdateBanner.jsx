import React, { useEffect, useState } from 'react'
import { api } from '../api'

// Shared "Update now" control. Shown only when the backend reports the update is
// wired up (a Watchtower sidecar URL + API token are configured). Clicking it
// POSTs /api/update; Watchtower then pulls the new image and recreates THIS
// container, so the connection drops briefly — we poll /api/version and reload
// once the running build changes (or the server comes back).
function UpdateNow({ prevSha }) {
  const [status, setStatus] = useState(null)
  const [phase, setPhase] = useState('idle')   // idle | working | error
  const [detail, setDetail] = useState('')
  useEffect(() => { api.get('/api/update/status').then(setStatus).catch(() => {}) }, [])

  async function waitForRestart() {
    // ~2 min: tolerate the server being unreachable mid-recreate, then reload
    // when it returns (ideally on a new sha).
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, 3000))
      try {
        const v = await api.get('/api/version')
        if (v?.running?.sha && v.running.sha !== prevSha) { window.location.reload(); return }
      } catch { /* container restarting — keep waiting */ }
    }
    window.location.reload()
  }

  async function run() {
    setPhase('working'); setDetail('')
    try {
      const r = await api.post('/api/update')
      if (!r.ok) { setPhase('error'); setDetail(r.detail || 'Update failed.'); return }
      setDetail(r.detail || 'Update triggered.')
      waitForRestart()
    } catch (e) { setPhase('error'); setDetail(e.message) }
  }

  if (!status?.configured) return null
  if (phase === 'working') {
    return <span className="muted" style={{ fontSize: '.82rem' }}>⏳ Updating — reconnecting when the container restarts…</span>
  }
  return (
    <span className="row" style={{ gap: '.4rem', alignItems: 'center' }}>
      <button className="sm" onClick={run}>Update now</button>
      {phase === 'error' && <span className="err" style={{ fontSize: '.8rem' }}>{detail}</span>}
    </span>
  )
}

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
            Running {running} · latest {up.latest} · one-click update it below, or pull the newest image.
          </span>
        </div>
        <div className="row" style={{ gap: '.4rem', alignItems: 'center' }}>
          <UpdateNow prevSha={info.running.sha} />
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
      {up?.available && (
        <div style={{ marginTop: '.5rem' }}><UpdateNow prevSha={run.sha} /></div>
      )}
    </div>
  )
}
