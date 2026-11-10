import React, { useEffect, useState } from 'react'
import { api, useDataVersion, useInterpreted, useResult, usd } from '../api'

// Future-state cards shown on the detail view's Future state step (moved out of the
// guided flow, which only collects data). Every number comes from the backend.

function useList(path) {
  const version = useDataVersion()
  const [rows, setRows] = useState(null)
  useEffect(() => {
    let live = true
    api.get(path).then((r) => { if (live) setRows(r) }).catch(() => {})
    return () => { live = false }
  }, [path, version])
  return rows
}

// Each group's change: per user per month, per year and over the engagement's
// modelling horizon (full yearly rate), with a total row (views.persona_totals).
export function PlanTable({ engagement }) {
  const base = `/api/engagements/${engagement.id}`
  const personas = useList(`${base}/personas`) || []
  const { result } = useResult(engagement.id)
  const view = Object.fromEntries((result?.views?.personas || []).map((v) => [v.persona_id, v]))
  const totals = result?.views?.persona_totals
  const years = totals?.horizon_years || engagement.modeling_horizon_years || 3
  if (!personas.length) return null
  const cls = (n) => `num ${n < 0 ? 'pos' : ''}`
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Change per group</h2>
      <p className="hint">Each group's all-in cost today and after its plan. Negative is a saving. Over {years} years is at
        the full yearly rate; the summary's headline times each saving from the renewal that unlocks it.</p>
      <table className="guide-table">
        <thead><tr><th>Group</th><th>Plan</th><th className="num">Today / user / mo</th>
          <th className="num">After / user / mo</th><th className="num">Change / user / mo</th>
          <th className="num">Per year</th><th className="num">Over {years} years</th></tr></thead>
        <tbody>
          {personas.map((p) => {
            const v = view[p.id]
            return (
              <tr key={p.id}><td>{p.name}</td><td>{v?.plan || <span className="muted">no plan yet</span>}</td>
                <td className="num">{v ? usd(v.today_per_user_monthly) : '…'}</td>
                <td className="num">{v ? usd(v.after_per_user_monthly) : '…'}</td>
                <td className={v ? cls(v.delta_per_user_monthly) : 'num'}>{v ? usd(v.delta_per_user_monthly) : '…'}</td>
                <td className={v ? cls(v.delta_annual) : 'num'}>{v ? usd(v.delta_annual) : '…'}</td>
                <td className={v ? cls(v.delta_horizon) : 'num'}>{v ? usd(v.delta_horizon) : '…'}</td></tr>
            )
          })}
        </tbody>
        {totals && <tfoot><tr><td>All groups</td><td /><td /><td /><td />
          <td className={cls(totals.delta_annual)}>{usd(totals.delta_annual)}</td>
          <td className={cls(totals.delta_horizon)}>{usd(totals.delta_horizon)}</td></tr></tfoot>}
      </table>
    </div>
  )
}

// What each group's new plan adds that nothing delivers today: confirm it as new
// (it then counts as gained) or mark one they already have another way.
export function WhatsNew({ engagement }) {
  const base = `/api/engagements/${engagement.id}`
  const personas = useList(`${base}/personas`) || []
  const outcomes = useList(`${base}/outcomes`) || []
  const stored = useList(`${base}/coverage-gap-answers`) || []
  const interp = useInterpreted(engagement.id)
  const { result } = useResult(engagement.id)
  const [err, setErr] = useState('')
  const name = Object.fromEntries(outcomes.map((o) => [o.id, o.name]))
  const answers = Object.fromEntries(stored.map((a) => [`${a.persona_id}|${a.outcome_id}`, a.answer]))
  const plan = Object.fromEntries((result?.views?.personas || []).map((v) => [v.persona_id, v.plan]))
  const groups = personas.filter((p) => p.headcount > 0).map((p) => ({
    p,
    items: (interp.data?.proposed || []).filter((r) => r.persona_id === p.id && r.in_scope && r.by_target
      && (!r.delivered_today || answers[`${p.id}|${r.outcome_id}`] === 'CoveredOutsideInventory'))
      .map((r) => ({ id: r.outcome_id, answer: answers[`${p.id}|${r.outcome_id}`] || null })),
  })).filter((g) => g.items.length > 0)
  async function put(p, ids, answer) {
    setErr('')
    try { for (const id of ids) await api.put(`${base}/coverage-gap-answers`, { persona_id: p.id, outcome_id: id, answer }) }
    catch (e) { setErr(e.message) }
  }
  if (!groups.length) return null
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>What's new for each group</h2>
      <p className="hint">What each group's plan adds that nothing gives them today. Confirm it's new and it counts as
        gained; if they already get one some other way, mark it with ✕.</p>
      {err && <div className="err">{err}</div>}
      {groups.map(({ p, items }) => {
        const open = items.filter((i) => !i.answer).map((i) => i.id)
        return (
          <div key={p.id} className="guide-block">
            <div className="guide-block-title">{p.name}{plan[p.id] && <span className="muted"> · {plan[p.id]} adds</span>}</div>
            <div className="guide-chips">
              {items.map((i) => (
                <span key={i.id} className={`badge ${i.answer === 'NotDeliveredToday' ? 'pos' : ''}`}
                  style={i.answer === 'CoveredOutsideInventory' ? { textDecoration: 'line-through', opacity: .6 } : undefined}
                  title={i.answer === 'CoveredOutsideInventory' ? 'They already have this another way' : i.answer ? 'Confirmed new' : 'Not confirmed yet'}>
                  {name[i.id] || 'A capability'}
                  {i.answer === 'CoveredOutsideInventory'
                    ? <button className="chip-x" title="Actually new for them" onClick={() => put(p, [i.id], 'NotDeliveredToday')}>↺</button>
                    : <button className="chip-x" title="They already have this another way" onClick={() => put(p, [i.id], 'CoveredOutsideInventory')}>✕</button>}
                </span>
              ))}
            </div>
            <div className="guide-chips">
              {open.length > 0
                ? <button className="ghost sm" onClick={() => put(p, open, 'NotDeliveredToday')}>These are all new for {p.name}</button>
                : <span className="src pos">Confirmed.</span>}
            </div>
          </div>
        )
      })}
    </div>
  )
}
