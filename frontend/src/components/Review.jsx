import React, { useEffect, useState } from 'react'
import { api } from '../api'
import Help, { useHelpText } from './Help.jsx'

// The AI sanity check's last result per engagement, kept across step changes
// without a data field (it is advisory and never stored).
const _sanityCache = {}

function timeAgo(ms) {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000))
  if (s < 60) return `${s}s ago`
  const m = Math.round(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60)
  return h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`
}

// Step 6 — Review (docs/WALKTHROUGH.md §5–§6). Every check that works without AI,
// each with the step where it's fixed, and the list of what is being left out
// because it wasn't answered. The AI sanity check is optional and advisory.
export default function Review({ engagement, onNavigate }) {
  const eid = engagement.id
  const help = useHelpText()
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [aiEnabled, setAiEnabled] = useState(false)
  const [checking, setChecking] = useState(false)
  const [sanity, setSanity] = useState(_sanityCache[eid] || null)

  useEffect(() => {
    setData(null); setErr('')
    api.get(`/api/engagements/${eid}/review`).then(setData).catch((e) => setErr(e.message))
    api.get('/api/admin/ai/status').then((s) => setAiEnabled(s.enabled)).catch(() => {})
    setSanity(_sanityCache[eid] || null)
  }, [eid])

  async function runSanity() {
    setChecking(true); setErr('')
    try {
      const res = await api.post(`/api/engagements/${eid}/sanity-check`)
      const entry = { ...res, at: Date.now() }
      _sanityCache[eid] = entry
      setSanity(entry)
    } catch (e) { setErr(e.message) } finally { setChecking(false) }
  }

  const stepTitle = (k) => help?.steps?.[k]?.title || k
  if (err) return <div className="card"><div className="err">{err}</div></div>
  if (!data) return <div className="card"><p className="muted">Checking…</p></div>

  const warns = data.checks.filter((c) => c.severity === 'warn')
  const infos = data.checks.filter((c) => c.severity !== 'warn')
  const item = (c, i) => (
    <div key={i} className="review-item">
      <span className={`badge ${c.severity === 'warn' ? 'warn' : 'muted'}`}>{c.severity === 'warn' ? 'to answer' : 'note'}</span>
      <div className="grow">{c.message}</div>
      <button className="ghost sm" onClick={() => onNavigate?.(c.step)}>{stepTitle(c.step)} ›</button>
    </div>
  )

  return (
    <>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Review <Help k="review.checks" /></h2>
        {data.checks.length === 0
          ? <p className="pos" style={{ margin: 0 }}>✓ Everything lines up. Nothing is being left out.</p>
          : <>
              {warns.map(item)}
              {infos.length > 0 && (
                <details className="more" open={warns.length === 0}>
                  <summary>{infos.length} note{infos.length === 1 ? '' : 's'} (assumptions and things worth a second look)</summary>
                  {infos.map(item)}
                </details>
              )}
            </>}
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Left out of the numbers</h2>
        {data.left_out.length === 0
          ? <p className="muted" style={{ margin: 0 }}>Nothing — every item has an answer.</p>
          : <>
              <p className="hint">These aren't counted and won't appear in the report until they're answered.
                Nothing is estimated in their place.</p>
              <table>
                <thead><tr><th>What</th><th>Why</th><th></th></tr></thead>
                <tbody>
                  {data.left_out.map((x, i) => (
                    <tr key={i}><td>{x.what}</td><td className="muted">{x.why}</td>
                      <td className="num"><button className="ghost sm" onClick={() => onNavigate?.(x.step)}>{stepTitle(x.step)} ›</button></td></tr>
                  ))}
                </tbody>
              </table>
            </>}
      </div>

      {aiEnabled && (
        <details className="card">
          <summary style={{ cursor: 'pointer', listStyle: 'revert' }}>
            <b>AI sanity check</b> <Help k="review.sanity" />{' '}
            {sanity
              ? <small className="muted">— last run {timeAgo(sanity.at)} · {sanity.findings.length === 0 ? 'no issues' : `${sanity.findings.length} finding(s)`}</small>
              : <small className="muted">— optional, not run yet</small>}
          </summary>
          <div style={{ marginTop: '.6rem' }}>
            <div className="flex-between">
              <small className="src">Advisory only — never edits your data.{sanity ? ` Model: ${sanity.model}` : ''}</small>
              <button className="ghost sm" onClick={runSanity} disabled={checking}>
                {checking ? 'Checking…' : sanity ? '↻ Re-run' : 'Run sanity check'}</button>
            </div>
            {sanity && sanity.findings.length === 0 && (
              <div className="muted" style={{ marginTop: '.4rem' }}>✓ No issues flagged.</div>
            )}
            {sanity && sanity.findings.length > 0 && (
              <ul style={{ margin: '.4rem 0 0', paddingLeft: '1.1rem' }}>
                {sanity.findings.map((f, i) => (
                  <li key={i} style={{ marginBottom: '.25rem' }}>
                    <span className={`badge ${f.severity === 'error' ? 'neg' : f.severity === 'warn' ? 'warn' : 'muted'}`}>
                      {f.severity}</span>{' '}
                    {f.field && <b>{f.field}: </b>}{f.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </details>
      )}
    </>
  )
}
