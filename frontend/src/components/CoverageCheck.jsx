import React, { useEffect, useState } from 'react'
import { api } from '../api'

// Reusable $0 third party representing "covered by something out of scope".
const OOS_NAME = 'Covered elsewhere (out of scope)'

// Amber callout for the capability-honesty guards (unmapped current licensing /
// dropped outcomes).
const WARN_CALLOUT = {
  margin: '.5rem 0 0', padding: '.5rem .7rem',
  borderLeft: '3px solid var(--warn)', background: 'var(--bg)', borderRadius: 6,
}

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

  // "Covered elsewhere, out of scope": the outcome is delivered by something we
  // aren't costing. Recorded with existing objects only — a reusable $0
  // third-party ("Covered elsewhere (out of scope)") mapped to the outcome and
  // tagged to the persona. It drops off the gap list and never counts as a new
  // outcome, and its $0 cost keeps it out of the TCO math.
  async function markOutOfScope(persona, outcome) {
    setErr('')
    try {
      let sentinel = data.third_parties.find((t) => t.name === OOS_NAME)
      if (!sentinel) {
        const c = await api.post(`${base}/third-party`, {
          name: OOS_NAME, raw_cost: 0, cost_period: 'Annual',
        })
        sentinel = { id: c.id, name: c.name, persona_ids: c.persona_ids || [] }
      }
      await mapThirdParty(persona, outcome, sentinel.id)
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

          {/* Honesty guard #1: current licenses whose capability is unmapped, so a
              target can silently drop them. This is the multi-license persona trap —
              e.g. a persona on Office 365 E3 + EMS E3 where EMS maps to no bundle. */}
          {p.unmapped_current_licenses?.length > 0 && (
            <div style={WARN_CALLOUT}>
              <b className="warn">⚠ Unmapped current licensing</b>
              <div className="muted" style={{ fontSize: '.82rem', marginTop: '.25rem' }}>
                This persona holds {p.unmapped_current_licenses.length} current Microsoft
                license{p.unmapped_current_licenses.length > 1 ? 's' : ''} that deliver no mapped
                capability, so their outcomes are invisible to this comparison — a smaller target
                can drop them without it showing as a lost outcome:
              </div>
              <ul style={{ margin: '.3rem 0 0', paddingLeft: '1.1rem', fontSize: '.82rem' }}>
                {p.unmapped_current_licenses.map((u) => (
                  <li key={u.sku_reference}>
                    <b>{u.sku_reference}</b> — {u.resolves_to_bundle
                      ? 'a known bundle with no coverage in this engagement; add its outcomes in the Coverage map'
                      : 'not recognized as a bundle; map the SKU in Settings → Staple bundles'}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Honesty guard #2: a target that maps to no ratified coverage at all. It
              can neither add nor drop anything, so the whole capability story for this
              persona is a data gap — the New-outcomes readout would otherwise be blank. */}
          {p.target_unmapped && (
            <div style={WARN_CALLOUT}>
              <b className="warn">⚠ Target has no mapped capability</b>
              <div className="muted" style={{ fontSize: '.82rem', marginTop: '.25rem' }}>
                The proposed target delivers no mapped outcome in this engagement, so nothing
                can be compared — this persona shows no new outcomes and no trade-off:
              </div>
              <ul style={{ margin: '.3rem 0 0', paddingLeft: '1.1rem', fontSize: '.82rem' }}>
                {p.unmapped_target.map((u) => (
                  <li key={u.reference}>
                    <b>{u.reference}</b> — {u.resolves_to_bundle
                      ? 'a known bundle with no coverage in this engagement; add its outcomes in the Coverage map'
                      : 'not recognized as a bundle; map the SKU in Settings → Staple bundles'}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Honesty guard #3: untagged current licensing counts for EVERY persona, so
              a line someone else holds can make this persona's target look redundant. */}
          {p.org_wide_current_licenses?.length > 0 && (
            <div style={WARN_CALLOUT}>
              <b className="warn">⚠ Current licensing counted org-wide</b>
              <div className="muted" style={{ fontSize: '.82rem', marginTop: '.25rem' }}>
                {p.org_wide_current_licenses.length} current licence
                line{p.org_wide_current_licenses.length > 1 ? 's' : ''} carr
                {p.org_wide_current_licenses.length > 1 ? 'y' : 'ies'} no persona tag, so
                {p.org_wide_current_licenses.length > 1 ? ' they count' : ' it counts'} for
                every persona — and{' '}
                {p.org_wide_current_licenses.length > 1 ? 'they are' : 'it is'} what makes
                outcomes of this persona's target look already delivered. Tag{' '}
                {p.org_wide_current_licenses.length > 1 ? 'them' : 'it'} on the Current
                licensing tab for a per-persona value story:
              </div>
              <div className="pill-list" style={{ marginTop: '.35rem' }}>
                {p.org_wide_current_licenses.map((ref) => (
                  <span key={ref} className="badge warn">{ref}</span>
                ))}
              </div>
            </div>
          )}

          {/* Honesty guard #4: outcomes the current Microsoft licensing delivers that
              the target won't — the reverse of the "new outcomes" check below. */}
          {p.dropped_outcomes?.length > 0 && (
            <div style={WARN_CALLOUT}>
              <b className="warn">⚠ Target drops capability delivered today</b>
              <div className="muted" style={{ fontSize: '.82rem', marginTop: '.25rem' }}>
                The proposed target delivers {p.dropped_outcomes.length} fewer
                outcome{p.dropped_outcomes.length > 1 ? 's' : ''} than this persona's current
                Microsoft licensing. Confirm the downgrade is intended, or pick a target (or add-on)
                that preserves {p.dropped_outcomes.length > 1 ? 'them' : 'it'}:
              </div>
              <div className="pill-list" style={{ marginTop: '.35rem' }}>
                {p.dropped_outcomes.map((o) => (
                  <span key={o.id} className="badge warn" title={o.description}>{o.name}</span>
                ))}
              </div>
            </div>
          )}

          {!p.has_scenario ? (
            <p className="muted" style={{ margin: '.5rem 0 0' }}>No target scenario set — pick a target on the Scenarios tab to validate its new outcomes.</p>
          ) : p.target_unmapped ? (
            /* Nothing to validate — the target maps to no capability at all (guard above),
               which is a data gap, not a clean bill of health. */
            <p className="muted" style={{ margin: '.5rem 0 0' }}>Nothing to validate until the target's capability is mapped.</p>
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
                        if (v === '__oos') markOutOfScope(p, o)
                        else if (v === '__new') onNavigate && onNavigate('thirdparty')
                        else if (v) mapThirdParty(p, o, v)
                      }}>
                        <option value="">Leave as a new outcome (not covered today)</option>
                        {data.third_parties.filter((t) => t.name !== OOS_NAME).map((t) => (
                          <option key={t.id} value={t.id}>✓ Actually covered by: {t.name}</option>
                        ))}
                        <option value="__oos">✓ Covered elsewhere — out of scope (don't cost it)</option>
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
