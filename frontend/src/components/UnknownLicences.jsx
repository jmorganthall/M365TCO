import React, { useEffect, useRef, useState } from 'react'
import Help from './Help.jsx'
import { AddCoverageRow } from './CoverageMap.jsx'
import { api } from '../api'

// Step 3 — "Microsoft licences we can't read yet" (docs/WALKTHROUGH.md step 3,
// TARGET_SCHEMA §4.4 "Reading a line", D23). Each licence name from step 2 that the
// library doesn't know, or this engagement has no coverage for, once per name. One
// answer is written to every line of that name: the same as a library plan, the
// outcomes it delivers (ticked, or AI-suggested and confirmed — the same controls
// as a tool's uses), the library's list, or out of scope. Until answered, its cost
// counts but its groups' capability changes are left out.
export default function UnknownLicences({ engagement }) {
  const eid = engagement.id
  const base = `/api/engagements/${eid}`
  const [items, setItems] = useState(null)
  const [outcomes, setOutcomes] = useState([])
  const [plans, setPlans] = useState([])
  const [aiEnabled, setAiEnabled] = useState(false)
  const [open, setOpen] = useState({})          // answered name -> editor open
  const [busy, setBusy] = useState({})          // name -> AI suggest in flight
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  // Names keep the order they were first shown in, so a licence doesn't jump away
  // from under the cursor when it is answered (the server lists unread first).
  const order = useRef([])

  function load() {
    api.get(`${base}/licence-names`).then(setItems).catch((e) => setErr(e.message))
    api.get(`${base}/outcomes`).then(setOutcomes).catch((e) => setErr(e.message))
  }
  useEffect(() => {
    setItems(null); setErr(''); setMsg(''); setOpen({}); order.current = []
    load()
    api.get('/api/catalog/bundles').then(setPlans).catch(() => {})
    api.get('/api/admin/ai/status').then((s) => setAiEnabled(s.enabled)).catch(() => {})
  }, [eid])

  async function answer(item, value, bundleId) {
    setErr(''); setMsg('')
    try {
      const r = await api.put(`${base}/licence-names/answer`,
        { sku_reference: item.sku_reference, answer: value, bundle_id: bundleId || null })
      if (r?.outcomes_added?.length || r?.coverage_added) {
        setMsg(`${item.sku_reference || 'The licence'}: copied the library's list`
          + (r.outcomes_added.length ? `, adding ${r.outcomes_added.join(', ')} to this engagement` : '') + '.')
      }
      setOpen((o) => ({ ...o, [item.sku_reference]: false }))
      load()
    } catch (e) { setErr(e.message) }
  }
  async function tick(item, outcomeId, action) {
    setErr(''); setMsg('')
    // Ticking is answering as you go: keep the editor open for the next tick.
    setOpen((o) => ({ ...o, [item.sku_reference]: true }))
    try {
      await api.put(`${base}/licence-names/outcome`,
        { sku_reference: item.sku_reference, outcome_id: outcomeId, action })
      load()
    } catch (e) { setErr(e.message) }
  }
  async function suggest(item) {
    const name = item.sku_reference
    if (busy[name]) return
    setErr(''); setMsg('')
    setOpen((o) => ({ ...o, [name]: true }))
    setBusy((b) => ({ ...b, [name]: true }))
    try {
      const r = await api.post(`/api/admin/engagements/${eid}/ai/suggest-licence-outcomes`, { sku_reference: name })
      if (!r.suggested?.length) setMsg(`${name}: the AI matched it to no outcomes.`)
      load()
    } catch (e) { setErr(e.message) } finally {
      setBusy((b) => { const next = { ...b }; delete next[name]; return next })
    }
  }

  if (items === null) return err ? <div className="card"><div className="err">{err}</div></div> : null
  if (items.length === 0) return null
  items.forEach((i) => { if (!order.current.includes(i.sku_reference)) order.current.push(i.sku_reference) })
  const sorted = [...items].sort((a, b) => order.current.indexOf(a.sku_reference) - order.current.indexOf(b.sku_reference))
  const unread = items.filter((i) => i.state === 'unread')

  const editor = (item) => (
    <div className="licence-editor">
      <div className="toolbar" style={{ gap: '.5rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <SameAs plans={plans} current={item.bundle_id || item.resolved_bundle_id}
          onPick={(id) => answer(item, 'same_as', id)} />
        {item.library_offer && (
          <button className="sm" onClick={() => answer(item, 'library')}
            title={`The library lists: ${item.library_offer.outcomes.join(', ')}`}>
            Use the library's list for {item.library_offer.bundle_name}</button>
        )}
        <button className="ghost sm" onClick={() => answer(item, 'out_of_scope')}>
          Out of scope for this workshop</button>
      </div>
      {item.library_offer && (
        <p className="hint" style={{ margin: '.3rem 0 0' }}>The library lists for {item.library_offer.bundle_name}:{' '}
          {item.library_offer.outcomes.join(', ')}. This engagement was created before the library knew it.</p>
      )}
      <div style={{ marginTop: '.5rem' }}>
        <div className="flex-between">
          <label style={{ margin: 0 }}>Or tick what it delivers</label>
          {aiEnabled && (
            <button className="ghost sm" onClick={() => suggest(item)} disabled={!!busy[item.sku_reference]}>
              {busy[item.sku_reference] ? 'Suggesting…' : '✨ AI suggest'}</button>
          )}
        </div>
        <div className="pill-list" style={{ margin: '.4rem 0' }}>
          {item.outcomes.map((t) => (
            <span key={t.outcome_id} className={`badge ${t.ratified ? 'pos' : 'warn'}`}>
              {t.name}{t.ai_suggested && !t.ratified && ' · AI'}
              {!t.ratified && <button className="sm ghost" style={{ marginLeft: 6 }}
                onClick={() => tick(item, t.outcome_id, 'confirm')}>Confirm</button>}
              <button className="sm danger" style={{ marginLeft: 4 }}
                onClick={() => tick(item, t.outcome_id, 'remove')}>×</button>
            </span>
          ))}
          {item.outcomes.length === 0 && <span className="muted">Nothing ticked.</span>}
        </div>
        <AddCoverageRow outcomes={outcomes} existing={item.outcomes.map((t) => t.outcome_id)}
          onAdd={(oid) => tick(item, oid, 'add')} />
      </div>
    </div>
  )

  const heading = (item) => (
    <>
      <b>{item.sku_reference || 'A licence line with no name'}</b>
      <span className="muted"> · {item.groups.join(', ') || 'no group'} · {item.seats.toLocaleString()} seats</span>
    </>
  )

  return (
    <div className={`card${unread.length ? ' card-loud' : ''}`} id="unknown-licences">
      <h2 style={{ marginTop: 0 }}>Microsoft licences we can't read yet <Help k="licences.unknown" /></h2>
      <p className="hint">The library doesn't know what these licences include, so we can't tell what
        their groups gain or give up. Their cost still counts. Answer each once; the answer applies to
        every line with that name. {aiEnabled && <><b>AI suggest</b> pre-ticks likely outcomes; confirm each
        with the customer — a suggestion counts only once confirmed.</>}</p>
      {err && <div className="err">{err}</div>}
      {msg && <div className="popcheck" style={{ margin: '.4rem 0' }}>{msg}</div>}

      {unread.length > 0 && <p className="warn" style={{ margin: '0 0 .5rem' }}>
        <b>{unread.length} to answer.</b> Until then, the capability changes of the groups holding{' '}
        {unread.length === 1 ? 'it' : 'them'} are left out of the report.</p>}
      {sorted.map((item) => item.state === 'unread' ? (
        <div key={item.sku_reference} className="card" style={{ background: 'var(--panel2)' }}>
          <div className="flex-between">
            <div>{heading(item)}</div>
            <span className="badge warn">not answered</span>
          </div>
          {item.bundle_name && <p className="hint" style={{ margin: '.3rem 0 0' }}>Linked to {item.bundle_name},
            which delivers nothing in this engagement yet.</p>}
          <p className="hint" style={{ margin: '.4rem 0 .3rem' }}>What does it include? It's the same as a
            library plan, it delivers the outcomes you tick, or it's not part of this workshop.</p>
          {editor(item)}
        </div>
      ) : (
        <div key={item.sku_reference} className="answered-licence">
          <div className="flex-between" style={{ gap: '.5rem' }}>
            <div className="grow">
              {heading(item)} <span className="badge pos">answered</span>
              <div style={{ fontSize: '.82rem', marginTop: '.15rem' }}>{summary(item)}</div>
            </div>
            <div style={{ display: 'flex', gap: '.3rem' }}>
              <button className="ghost sm" onClick={() => setOpen((o) => ({ ...o, [item.sku_reference]: !o[item.sku_reference] }))}>
                {open[item.sku_reference] ? 'Done' : 'Change'}</button>
              {item.state !== 'named' && item.state !== 'resolved' && (
                <button className="ghost sm" title="Remove this answer"
                  onClick={() => answer(item, 'clear')}>Clear</button>
              )}
            </div>
          </div>
          {open[item.sku_reference] && editor(item)}
        </div>
      ))}
    </div>
  )
}

function summary(item) {
  const delivers = item.delivers.length ? item.delivers.join(', ') : 'nothing'
  switch (item.state) {
    case 'linked': return <>Same as <b>{item.bundle_name}</b> <span className="muted">— delivers {delivers}</span></>
    case 'mapped': return <>Delivers <b>{delivers}</b> <span className="muted">(answered in this workshop)</span></>
    case 'named': return <>Delivers <b>{delivers}</b> <span className="muted">(mapped by name on the Coverage map)</span></>
    case 'out_of_scope': return <span className="muted">Out of scope for this workshop — in no number.</span>
    default: return <span className="muted">Read as {item.resolved_bundle_name || 'a library plan'}.</span>
  }
}

// "It's the same as": a library plan, plans first, then add-ons and stand-alones.
function SameAs({ plans, current, onPick }) {
  const [pick, setPick] = useState(current || '')
  useEffect(() => { setPick(current || '') }, [current])
  const bases = plans.filter((p) => p.kind !== 'addon')
  const addons = plans.filter((p) => p.kind === 'addon')
  return (
    <div style={{ display: 'flex', gap: '.3rem', alignItems: 'flex-end' }}>
      <div>
        <label style={{ margin: 0 }}>It's the same as</label>
        <select value={pick} onChange={(e) => setPick(e.target.value)} style={{ minWidth: 240 }}>
          <option value="">Pick a library plan…</option>
          <optgroup label="Plans">{bases.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</optgroup>
          <optgroup label="Add-ons and stand-alones">{addons.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</optgroup>
        </select>
      </div>
      <button className="sm" disabled={!pick} onClick={() => onPick(pick)}>Use</button>
    </div>
  )
}
