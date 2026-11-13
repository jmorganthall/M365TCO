import React, { useEffect, useState } from 'react'
import { api, money, useDataVersion } from '../api'

// The Business seat plan (docs/GUIDED_PLAN.md G22): a seat-capped family (Microsoft
// 365 Business, 300 seats) shared across every group where each seat saves the
// most. Every number comes from the backend (GET …/seat-plan); Apply recomputes it
// there and sets the plans, splitting at most one group.
export default function SeatPlan({ engagement, moneyUnit = 'yr' }) {
  const eid = engagement.id
  const version = useDataVersion()
  const [plans, setPlans] = useState([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => { api.get(`/api/engagements/${eid}/seat-plan`).then(setPlans).catch(() => setPlans([])) }, [eid, version])
  async function apply() {
    setBusy(true); setErr('')
    try { await api.post(`/api/engagements/${eid}/seat-plan/apply`) } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  const shown = plans.filter((p) => p.groups.some((g) => g.capped_seats > 0 || g.capped_now > 0))
  if (shown.length === 0) return null
  return (
    <>
      {shown.map((p) => (
        <div key={p.cap_key} className="card">
          <h2 style={{ marginTop: 0 }}>{p.cap_name}: {p.seats_used.toLocaleString()} of {p.cap.toLocaleString()} seats</h2>
          <p className="hint">The seats go to the groups where each one saves the most
            {p.used_elsewhere > 0 && <> ({p.used_elsewhere.toLocaleString()} are already used by licences kept elsewhere)</>}.</p>
          {err && <div className="err">{err}</div>}
          <table className="guide-table">
            <thead><tr><th>Group</th><th className="num">On the capped plan</th><th className="num">On the other plan</th>
              <th className="num">Saves per seat</th><th className="num">Saves</th></tr></thead>
            <tbody>
              {p.groups.map((g) => (
                <tr key={g.persona_id}>
                  <td>{g.persona_name}<div className="muted">{g.people.toLocaleString()} people</div></td>
                  <td className="num">{g.capped_seats.toLocaleString()}
                    <div className="muted">{g.capped_plan.label}</div>
                    {g.changes && <div className="muted">now {g.capped_now.toLocaleString()}</div>}</td>
                  <td className="num">{g.other_seats.toLocaleString()}
                    {g.other_seats > 0 && <div className="muted">{g.other_plan ? g.other_plan.label : 'no plan fits'}</div>}</td>
                  <td className="num">{g.saving_per_seat_annual == null ? '—' : money(g.saving_per_seat_annual, moneyUnit)}</td>
                  <td className="num pos">{money(g.saving_annual, moneyUnit)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot><tr><td>Compared with no one on the capped plan</td><td /><td /><td />
              <td className="num pos">{money(p.saving_annual, moneyUnit)}</td></tr></tfoot>
          </table>
          <div className="toolbar" style={{ marginTop: 16 }}>
            {p.changes
              ? <button onClick={apply} disabled={busy}>{busy ? 'Applying…' : 'Apply this split'}</button>
              : <span className="muted">✓ The plans already share the seats this way.</span>}
          </div>
        </div>
      ))}
    </>
  )
}
