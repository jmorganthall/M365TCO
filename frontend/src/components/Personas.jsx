import React, { useEffect, useState } from 'react'
import { api } from '../api'

// Inline (auto-saving) text field that holds local state and commits on blur or
// Enter — not per keystroke. The row's update() does a PATCH-and-reload, which
// would otherwise reset the input mid-typing and garble what you type. Re-syncs
// from the persisted value when the field isn't focused.
function TextInput({ value, onCommit, ...rest }) {
  const [v, setV] = useState(value ?? '')
  const [focused, setFocused] = useState(false)
  useEffect(() => { if (!focused) setV(value ?? '') }, [value, focused])
  const commit = () => { setFocused(false); if (v !== (value ?? '')) onCommit(v) }
  return (
    <input {...rest} value={v}
      onFocus={() => setFocused(true)}
      onChange={(e) => setV(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }} />
  )
}

// Order personas so every carve-out sits directly under the persona it came from.
// The lineage is the point of the feature: a split that renders as an unrelated
// row somewhere else in the list is exactly the confusion it exists to prevent.
function withCarveOutsUnderParents(items) {
  const children = new Map()
  for (const p of items) {
    if (!p.parent_persona_id) continue
    if (!children.has(p.parent_persona_id)) children.set(p.parent_persona_id, [])
    children.get(p.parent_persona_id).push(p)
  }
  const out = []
  for (const p of items) {
    // A carve-out whose parent is present is emitted with its parent, not twice.
    if (p.parent_persona_id && items.some((x) => x.id === p.parent_persona_id)) continue
    out.push(p)
    for (const c of children.get(p.id) || []) out.push(c)
  }
  return out
}

export default function Personas({ engagement, meta }) {
  const base = `/api/engagements/${engagement.id}/personas`
  const [items, setItems] = useState([])
  const [outcomes, setOutcomes] = useState([])
  const [bundles, setBundles] = useState([])
  const [open, setOpen] = useState({})
  const [carving, setCarving] = useState(null)   // persona id with the carve form open
  const [carve, setCarve] = useState({ seats: '', name: '', target_sku_reference: '' })
  const [form, setForm] = useState({ name: '', headcount: 0, description: '', source_tag: 'CustomerStated' })
  const [err, setErr] = useState('')

  const load = () => api.get(base).then(setItems).catch((e) => setErr(e.message))
  useEffect(() => {
    load()
    api.get(`/api/engagements/${engagement.id}/outcomes`).then(setOutcomes).catch(() => {})
    api.get(`/api/catalog/bundles?engagement_id=${engagement.id}`).then(setBundles).catch(() => {})
  }, [engagement.id])

  async function add() {
    if (!form.name.trim()) return
    try {
      await api.post(base, { ...form, headcount: Number(form.headcount) })
      setForm({ name: '', headcount: 0, description: '', source_tag: 'CustomerStated' })
      load()
    } catch (e) { setErr(e.message) }
  }
  async function update(id, patch) {
    try { await api.patch(`${base}/${id}`, patch); load() } catch (e) { setErr(e.message) }
  }
  async function remove(id) {
    try { await api.del(`${base}/${id}`); load() } catch (e) { setErr(e.message) }
  }
  // Carve seats out of a persona into a child with its own target. The seats MOVE
  // (the parent shrinks), so the modelled population never changes behind the
  // operator's back.
  async function doCarve(parent) {
    const seats = Number(carve.seats)
    if (!seats) return
    try {
      await api.post(`${base}/${parent.id}/carve`, {
        seats,
        name: carve.name.trim(),
        target_sku_reference: carve.target_sku_reference,
      })
      setCarving(null)
      setCarve({ seats: '', name: '', target_sku_reference: '' })
      load()
    } catch (e) { setErr(e.message) }
  }

  function toggleRequirement(p, outcomeId) {
    const have = new Set(p.required_outcome_ids || [])
    have.has(outcomeId) ? have.delete(outcomeId) : have.add(outcomeId)
    update(p.id, { required_outcome_ids: [...have] })
  }

  return (
    <div className="card">
      <h2>Personas and headcounts</h2>
      <p className="hint">Define the populations you will model. Each persona gets one target
        scenario later. Expand a persona to set the capabilities it <b>requires</b> (e.g. Desktop
        Software, Full-Size Cloud Storage) — recommend-a-path flags a gap if a target bundle
        misses one, keeping Frontline personas off mainline bundles they don't need and vice versa.</p>
      {err && <div className="err">{err}</div>}

      <table className="resp-table">
        <thead><tr>
          <th></th><th>Name</th><th className="num">Headcount</th>
          <th className="num">Requires</th><th></th>
        </tr></thead>
        <tbody>
          {withCarveOutsUnderParents(items).map((p) => {
            const reqs = p.required_outcome_ids || []
            const parent = p.parent_persona_id
              ? items.find((x) => x.id === p.parent_persona_id)
              : null
            const carveOuts = items.filter((x) => x.parent_persona_id === p.id)
            return (
              <React.Fragment key={p.id}>
                <tr className={parent ? 'carve-row' : undefined}>
                  <td><button className="ghost sm" onClick={() => setOpen({ ...open, [p.id]: !open[p.id] })}>
                    {open[p.id] ? '▾' : '▸'}</button></td>
                  <td data-label="Name">
                    {/* The indent + elbow is the visual tie: a carve-out reads as
                        part of its parent, never as a stray population. */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: '.4rem',
                                  paddingLeft: parent ? '1.1rem' : 0 }}>
                      {parent && <span className="carve-elbow" aria-hidden="true">└</span>}
                      <TextInput value={p.name} onCommit={(v) => update(p.id, { name: v })} />
                    </div>
                    {parent && (
                      <div className="pill-list" style={{ marginTop: 3, paddingLeft: '1.5rem' }}>
                        <span className="badge muted"
                          title={`These ${p.headcount} seats were carved out of ${parent.name}. Removing this persona returns them.`}>
                          carved from {parent.name}</span>
                      </div>
                    )}
                    {carveOuts.length > 0 && (
                      <div className="pill-list" style={{ marginTop: 3 }}>
                        <span className="badge muted"
                          title={`${carveOuts.reduce((n, c) => n + c.headcount, 0)} seats now sit in carve-outs of this persona`}>
                          {carveOuts.length} carve-out{carveOuts.length > 1 ? 's' : ''}
                          {' '}({carveOuts.reduce((n, c) => n + c.headcount, 0)} seats)</span>
                      </div>
                    )}
                  </td>
                  <td className="num" data-label="Headcount"><input type="number" value={p.headcount}
                    onChange={(e) => update(p.id, { headcount: Number(e.target.value) })} style={{ width: 90 }} /></td>
                  <td className="num" data-label="Requires">{reqs.length || <span className="muted">—</span>}</td>
                  <td className="num">
                    {!parent && (
                      <>
                        <button className="ghost sm" title="Move some of these seats onto their own plan"
                          onClick={() => {
                            setCarving(carving === p.id ? null : p.id)
                            setCarve({ seats: '', name: '', target_sku_reference: '' })
                          }}>Carve out…</button>{' '}
                      </>
                    )}
                    <button className="danger sm" onClick={() => remove(p.id)}>Remove</button>
                  </td>
                </tr>
                {carving === p.id && (
                  <tr className="detail-row">
                    <td></td>
                    <td colSpan={4} style={{ background: 'var(--panel2)' }}>
                      <div className="grid c4" style={{ padding: '.3rem 0' }}>
                        <div><label>Seats to carve out</label>
                          <input type="number" value={carve.seats} placeholder="300"
                            onChange={(e) => setCarve({ ...carve, seats: e.target.value })} />
                          <small className="src">
                            Moved out of <b>{p.name}</b>, leaving{' '}
                            <b>{Math.max(p.headcount - (Number(carve.seats) || 0), 0)}</b>. Total
                            headcount is unchanged.
                          </small></div>
                        <div><label>Their target plan</label>
                          <select value={carve.target_sku_reference}
                            onChange={(e) => setCarve({ ...carve, target_sku_reference: e.target.value })}>
                            <option value="">Same as {p.name}</option>
                            {bundles.map((b) => <option key={b.id || b.name} value={b.name}>{b.name}</option>)}
                          </select>
                          <small className="src">The carve-out gets its own scenario at this target.</small></div>
                        <div style={{ gridColumn: 'span 2' }}><label>Name</label>
                          <input value={carve.name}
                            placeholder={`${p.name} — ${carve.target_sku_reference || 'carve-out'}`}
                            onChange={(e) => setCarve({ ...carve, name: e.target.value })} />
                          <small className="src">
                            Inherits {p.name}'s current licensing, third-party tools and required
                            capabilities — only the target differs.
                          </small></div>
                      </div>
                      <div className="toolbar" style={{ marginTop: '.2rem' }}>
                        <button onClick={() => doCarve(p)}
                          disabled={!Number(carve.seats) || Number(carve.seats) >= p.headcount}>
                          Carve out {Number(carve.seats) ? `${Number(carve.seats)} seats` : ''}
                        </button>
                        <button className="ghost" onClick={() => setCarving(null)}>Cancel</button>
                      </div>
                    </td>
                  </tr>
                )}
                {open[p.id] && (
                  <tr className="detail-row">
                    <td></td>
                    <td colSpan={4} style={{ background: 'var(--panel2)' }}>
                      <div className="grid c4" style={{ padding: '.2rem 0 .5rem' }}>
                        <div><label>Source</label>
                          <select value={p.source_tag} onChange={(e) => update(p.id, { source_tag: e.target.value })}>
                            {(meta?.source_tags || []).map((s) => <option key={s}>{s}</option>)}
                          </select>
                          <small className="src">Provenance of the headcount — informational; doesn't affect the math.</small></div>
                        <div style={{ gridColumn: 'span 3' }}><label>Description</label>
                          <TextInput value={p.description || ''} placeholder="Optional notes about this population"
                            onCommit={(v) => update(p.id, { description: v })} /></div>
                      </div>
                      <label style={{ display: 'block', marginBottom: '.3rem' }}>Required capabilities</label>
                      <div className="pill-list">
                        {outcomes.map((o) => (
                          <button key={o.id} type="button"
                            className={`tag-toggle ${reqs.includes(o.id) ? 'on' : ''}`}
                            onClick={() => toggleRequirement(p, o.id)}>{o.name}</button>
                        ))}
                        {outcomes.length === 0 && <span className="muted">No outcomes defined yet.</span>}
                      </div>
                      <small className="src">These count as required in recommend-a-path even if no current license delivers them.</small>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            )
          })}
        </tbody>
      </table>

      <div className="toolbar" style={{ marginTop: '.8rem' }}>
        <div style={{ flex: 2 }}>
          <label>New persona</label>
          <input value={form.name} placeholder="Knowledge Worker"
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            onKeyDown={(e) => e.key === 'Enter' && add()} />
        </div>
        <div>
          <label>Headcount</label>
          <input type="number" value={form.headcount}
            onChange={(e) => setForm({ ...form, headcount: e.target.value })} />
        </div>
        <button onClick={add}>Add persona</button>
      </div>
    </div>
  )
}
