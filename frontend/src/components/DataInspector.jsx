import React, { useEffect, useState } from 'react'
import { api } from '../api'

// The Data inspector: a read-only, GUI-visible mirror of the whole engagement
// data model — every object, every persisted field (classified + labelled),
// references resolved and validated, plus the input → engine → output flow.
// This is what makes the data structure legible and traceable in the app.
export default function DataInspector({ engagement }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    setData(null)
    api.get(`/api/engagements/${engagement.id}/inspect`).then(setData).catch((e) => setErr(e.message))
  }, [engagement.id])

  if (err) return <div className="card"><div className="err">{err}</div></div>
  if (!data) return <div className="card"><p className="muted">Loading…</p></div>

  return (
    <>
      <div className="card">
        <h2>Data model</h2>
        <p className="hint">Every object and field in this engagement, with references
          resolved and provenance shown — a read-only mirror of exactly what the engine
          consumes. Click any line to expand its full detail.</p>
        <FlowStrip flow={data.flow} />
      </div>
      {data.objects.map((o) => <ObjectCard key={o.type} obj={o} />)}
    </>
  )
}

function FlowStrip({ flow }) {
  return (
    <div className="flow-strip">
      {flow.map((s, i) => (
        <React.Fragment key={s.stage}>
          <div className="flow-stage">
            <div className="flow-stage-name">{s.stage}</div>
            {s.items.map((it, j) => <div key={j} className="flow-item">{it}</div>)}
          </div>
          {i < flow.length - 1 && <div className="flow-arrow">→</div>}
        </React.Fragment>
      ))}
    </div>
  )
}

function ObjectCard({ obj }) {
  return (
    <div className="card">
      <div className="flex-between">
        <h2 style={{ margin: 0 }}>{obj.label} <span className="badge muted">{obj.count}</span></h2>
      </div>
      <p className="hint">{obj.description}</p>
      {obj.count === 0
        ? <p className="muted">None captured yet.</p>
        : obj.records.map((r) => <InspectRow key={r.id} obj={obj} rec={r} />)}
    </div>
  )
}

function InspectRow({ obj, rec }) {
  const [open, setOpen] = useState(false)
  const byKey = Object.fromEntries(obj.fields.map((f) => [f.key, f]))
  const primaries = obj.primary.map((k) => byKey[k]).filter(Boolean)
  return (
    <div className="inspect-row">
      <div className="inspect-row-head" onClick={() => setOpen(!open)}>
        <button className="ghost sm" style={{ minWidth: 24 }}>{open ? '▾' : '▸'}</button>
        {primaries.map((f) => (
          <span key={f.key} className="inspect-primary">
            <span className="inspect-plabel">{f.label}</span>
            <Cell f={f} cell={rec.cells[f.key]} />
          </span>
        ))}
      </div>
      {open && (
        <div className="inspect-detail">
          {obj.fields.map((f) => (
            <div key={f.key} className="inspect-field">
              <span className="inspect-flabel">{f.label}<KindTag kind={f.kind} /></span>
              <Cell f={f} cell={rec.cells[f.key]} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Cell({ f, cell }) {
  if (!cell) return <span className="muted">—</span>
  if (cell.ref) {
    return (
      <span className={`badge ${cell.ref.ok ? 'pos' : 'warn'}`} title={cell.display}>
        {cell.ref.ok ? '' : '⚠ '}{cell.display}
      </span>
    )
  }
  return <span className={f.kind === 'derived' || f.kind === 'system' ? 'muted' : ''}>{cell.display}</span>
}

function KindTag({ kind }) {
  if (kind === 'input') return null
  const label = { derived: 'derived', provenance: 'provenance', reference: 'ref', identity: 'id', system: 'system' }[kind] || kind
  return <span className={`kind-tag kind-${kind}`}>{label}</span>
}
