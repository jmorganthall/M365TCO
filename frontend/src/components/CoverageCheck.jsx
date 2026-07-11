import React, { useEffect, useState } from 'react'
import { api } from '../api'

// Coverage validation, between Scenarios and Readout. Per persona, the outcomes
// NOT delivered today by their current Microsoft licensing or a tagged third
// party. The operator resolves each gap using EXISTING relationships — map a
// third party that actually delivers it (adds the coverage entry + tags the
// product to the persona), add a new third party, or leave it as a genuine gap
// the target scenario will light up as a "new outcome". No new data is invented.
export default function CoverageCheck({ engagement, onNavigate }) {
  const eid = engagement.id
  const base = `/api/engagements/${eid}`
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')

  function load() {
    api.get(`${base}/coverage-gaps`).then(setData).catch((e) => setErr(e.message))
  }
  useEffect(load, [eid])

  async function mapThirdParty(persona, outcome, tpId) {
    setErr('')
    try {
      // Existing relationship #1: this third party delivers this outcome.
      await api.post(`${base}/coverage`, {
        outcome_id: outcome.id, product_kind: 'ThirdParty',
        third_party_product_id: tpId, coverage: 'Full', ratified: true,
      })
      // Existing relationship #2: ensure the product is tagged to this persona,
      // so the coverage counts for them.
      const tp = data.third_parties.find((t) => t.id === tpId)
      if (tp && !tp.persona_ids.includes(persona.persona_id)) {
        await api.patch(`${base}/third-party/${tpId}`, {
          persona_ids: [...tp.persona_ids, persona.persona_id],
        })
      }
      load()
    } catch (e) { setErr(e.message) }
  }

  if (!data) return <div className="card"><p className="muted">Loading…</p></div>

  return (
    <div className="card">
      <h2>Coverage check — confirm the target's new outcomes</h2>
      <p className="hint">For each persona, the outcomes their <b>proposed target scenario</b> would
        deliver that <b>aren't</b> delivered today (by their current Microsoft licensing or a mapped
        third party). Resolve each: pick a third party that actually delivers it (we didn't map it),
        add a new one, or leave it — a genuine gap the target lights up as a <b>new outcome</b>. This
        keeps the value story honest and avoids costing something already covered elsewhere.</p>
      {err && <div className="err">{err}</div>}
      {data.personas.length === 0 && <p className="muted">No personas yet — add personas first.</p>}

      {data.personas.map((p) => (
        <div key={p.persona_id} className="card" style={{ background: 'var(--panel2)' }}>
          <div className="flex-between">
            <b>{p.persona_name} <span className="muted">· {p.headcount} users</span></b>
            {p.has_scenario && (
              <span className="muted">{p.covered_of_target}/{p.target_outcome_count} target outcomes already delivered today</span>
            )}
          </div>
          {!p.has_scenario ? (
            <p className="muted" style={{ margin: '.5rem 0 0' }}>No target scenario set — pick a target on the Scenarios tab to validate its new outcomes.</p>
          ) : p.uncovered_outcomes.length === 0 ? (
            <p className="pos" style={{ margin: '.5rem 0 0' }}>✓ Every outcome the target delivers is already accounted for.</p>
          ) : (
            <table>
              <thead><tr><th>Uncovered outcome</th><th style={{ width: 320 }}>Resolve</th></tr></thead>
              <tbody>
                {p.uncovered_outcomes.map((o) => (
                  <tr key={o.id}>
                    <td>{o.name}</td>
                    <td>
                      <select value="" onChange={(e) => {
                        const v = e.target.value
                        if (v === '__new') onNavigate && onNavigate('thirdparty')
                        else if (v) mapThirdParty(p, o, v)
                      }}>
                        <option value="">Leave as a new outcome (not covered today)</option>
                        {data.third_parties.map((t) => (
                          <option key={t.id} value={t.id}>✓ Actually covered by: {t.name}</option>
                        ))}
                        <option value="__new">+ Add a third-party solution…</option>
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ))}
    </div>
  )
}
