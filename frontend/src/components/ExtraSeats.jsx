import React, { useState } from 'react'
import { api, money, useInterpreted } from '../api'
import Help from './Help.jsx'

// The seat-mismatch question (docs/GUIDED_PLAN.md G4): asked only when a tool the
// future plans replace covers more seats than the personas who hold it. Everything
// else is like for like — a persona whose plan delivers the tool's outcomes drops
// its share, one whose plan doesn't keeps it — so there is nothing else to ask.
// The answer describes today, so it is stored on the tool (extra_seats_answer).
export default function ExtraSeats({ engagement, moneyUnit = 'mo' }) {
  const eid = engagement.id
  const interp = useInterpreted(eid)
  const [err, setErr] = useState('')
  const asked = new Set(interp.findings.filter((f) => f.code === 'extra_seats_unanswered').map((f) => f.tool_id))
  // Asked, or already answered (so the answer can be changed).
  const shown = (interp.data?.tools || []).filter((t) => t.extra_seats > 0
    && (asked.has(t.id) || t.extra_seats_answer))

  async function answer(t, value) {
    setErr('')
    try {
      await api.patch(`/api/engagements/${eid}/third-party/${t.id}`, { extra_seats_answer: value })
    } catch (e) { setErr(e.message) }
  }

  if (shown.length === 0) return null
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Seats that don't line up <Help k="tools.extra_seats" /></h2>
      <p className="hint">The new plans replace these tools, but each covers more seats than the people
        in the groups who have it. If you say the extra seats aren't needed, the whole cost is shown as going away.</p>
      {err && <div className="err">{err}</div>}
      {shown.map((t) => (
        <div key={t.id} className="decision-row" style={{ padding: '.5rem 0', borderTop: '1px solid var(--line)' }}>
          <div><b>{t.name}</b> covers {t.stated_seats.toLocaleString()} seats; the groups given it have{' '}
            {t.people_held.toLocaleString()} people. What are the other {t.extra_seats.toLocaleString()}?</div>
          <div style={{ display: 'flex', gap: '.5rem', marginTop: '.35rem', flexWrap: 'wrap' }}>
            <button className={t.extra_seats_answer === 'ExtraSeats' ? '' : 'ghost'}
              onClick={() => answer(t, 'ExtraSeats')}>Extra seats nobody needs</button>
            <button className={t.extra_seats_answer === 'PeopleWhoStay' ? '' : 'ghost'}
              onClick={() => answer(t, 'PeopleWhoStay')}>People who stay on {t.name}</button>
          </div>
          <small className="src">{t.extra_seats_answer === 'ExtraSeats'
            ? 'The whole cost is spread over your people, so it can all go when they move.'
            : `${t.extra_seats_answer ? '' : 'Not answered yet, so '}counted as people who stay: ${money(t.standing_annual, moneyUnit)} that no move removes.`}</small>
        </div>
      ))}
    </div>
  )
}
