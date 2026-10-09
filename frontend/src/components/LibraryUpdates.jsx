import React, { useEffect, useState } from 'react'
import Help from './Help.jsx'
import { api } from '../api'

// Step 5 — Library updates (docs/WALKTHROUGH.md W13; TARGET_SCHEMA §4.7). This
// engagement keeps its own copy of the shared library. When the library changes
// later, each difference is listed here; nothing changes until someone clicks
// Apply, and "Not for this customer" is remembered (with an undo).
export default function LibraryUpdates({ engagement, onChange }) {
  const url = `/api/engagements/${engagement.id}/library-updates`
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  function load() {
    api.get(url).then(setData).catch((e) => setErr(e.message))
  }
  useEffect(() => { setData(null); setErr(''); load() }, [engagement.id])

  async function act(path, body) {
    setBusy(true); setErr('')
    try {
      await (body === undefined ? api.post(`${url}/${path}`) : api.post(`${url}/${path}`, body))
      load(); onChange?.()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  async function undo(id) {
    setBusy(true); setErr('')
    try { await api.del(`${url}/decisions/${id}`); load(); onChange?.() }
    catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  if (!data) return err ? <div className="card"><div className="err">{err}</div></div> : null
  if (!data.items.length && !data.declined.length) return null
  // Apply all leaves removals of rows of unknown origin for an individual click.
  const bulk = data.items.filter((i) => !i.origin_unknown).length
  const subject = (i) => ({ kind: i.kind, outcome_key: i.outcome_key, bundle_id: i.bundle_id, sku_reference: i.sku_reference })

  return (
    <div className="card">
      <div className="flex-between">
        <h2 style={{ margin: 0 }}>Library updates <Help k="gaps.library_updates" /></h2>
        {bulk > 1 && (
          <button className="sm" disabled={busy} onClick={() => act('apply-all')}>Apply all ({bulk})</button>
        )}
      </div>
      <p className="hint">The shared library has changed since this engagement was created. Each difference
        is listed here; nothing changes until you apply it, and applying moves the numbers. A PDF already
        presented keeps its own snapshot.</p>
      {err && <div className="err">{err}</div>}
      {data.items.length === 0 && <p className="muted" style={{ margin: 0 }}>Nothing waiting.</p>}
      {data.items.map((i) => (
        <div key={i.key} className="review-item">
          <span className={`badge ${i.kind === 'coverage_removed' ? 'warn' : 'pos'}`}>{BADGE[i.kind]}</span>
          <div className="grow">{describe(i)}</div>
          <div style={{ display: 'flex', gap: '.3rem' }}>
            <button className="sm" disabled={busy} onClick={() => act('apply', subject(i))}>Apply</button>
            <button className="ghost sm" disabled={busy} onClick={() => act('decline', subject(i))}>Not for this customer</button>
          </div>
        </div>
      ))}
      {data.declined.length > 0 && (
        <details className="more">
          <summary>{data.declined.length} declined for this customer</summary>
          {data.declined.map((d) => (
            <div key={d.id} className="review-item">
              <div className="grow">{d.label} <span className="muted">· {d.reason}
                {d.decided_at ? ` · ${new Date(d.decided_at + 'Z').toLocaleDateString()}` : ''}</span></div>
              <button className="ghost sm" disabled={busy} onClick={() => undo(d.id)}>Undo</button>
            </div>
          ))}
        </details>
      )}
    </div>
  )
}

const BADGE = {
  outcome_added: 'new outcome',
  coverage_added: 'plan',
  coverage_removed: 'removed',
  licence_in_library: 'licence',
}

const list = (xs) => xs.join(', ')
// A long plan list is cut to its first six; the full list is on hover.
const short = (xs) => (xs.length > 6 ? `${xs.slice(0, 6).join(', ')} and ${xs.length - 6} more` : list(xs))

function describe(i) {
  switch (i.kind) {
    case 'outcome_added':
      return <><b>New outcome: {i.name}</b>{i.plans.length > 0 && <> — <span title={list(i.plans)}>in {short(i.plans)}</span></>}
        {i.description && <div className="muted" style={{ fontSize: '.8rem' }}>{i.description}</div>}</>
    case 'coverage_added':
      return i.new_plan
        ? <>The library now has <b>{i.bundle_name}</b>: {list(i.outcomes)}</>
        : i.engagement_lists.length
          ? <><b>{i.bundle_name}</b> now includes {list(i.outcomes)}
              <div className="muted" style={{ fontSize: '.8rem' }}>This engagement lists {list(i.engagement_lists)}.</div></>
          : <><b>{i.bundle_name}</b> now includes {list(i.outcomes)}</>
    case 'coverage_removed':
      return <>The library no longer lists {list(i.outcomes)} for <b>{i.bundle_name}</b>. Apply removes
        {i.outcomes.length > 1 ? ' them' : ' it'} from this engagement.
        {i.origin_unknown && <div className="muted" style={{ fontSize: '.8rem' }}>This engagement predates
          recording where coverage came from, so this may have been added for this customer: check before
          applying. Apply all leaves it for you.</div>}</>
    case 'licence_in_library':
      return <>The library now knows <b>{i.sku_reference}</b> as <b>{i.bundle_name}</b>.
        <div className="muted" style={{ fontSize: '.8rem' }}>Answered in this workshop: {list(i.line_outcomes) || 'nothing'}.
          The plan delivers: {list(i.plan_outcomes) || 'nothing yet'}. Apply links the licence to the plan.</div></>
    default:
      return i.kind
  }
}
