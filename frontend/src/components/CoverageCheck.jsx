import React, { useEffect, useState } from 'react'
import { api } from '../api'
import Help from './Help.jsx'

// The $0 placeholder tool older versions created for "covered elsewhere". It is
// no longer created (gap answers replaced it) but stays readable, so it is kept
// out of the "actually covered by" choices.
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

  // The customer's answer about a gap (TARGET_SCHEMA §4.8). "Not delivered today"
  // confirms it — the target lights it up as a new outcome. "Covered outside this
  // inventory" — something we aren't costing delivers it: never new, never costed.
  // No answer — not yet claimed as new.
  async function answer(persona, outcome, value) {
    setErr('')
    try {
      await api.put(`${base}/coverage-gap-answers`, {
        persona_id: persona.persona_id, outcome_id: outcome.id, answer: value,
      })
      load()
    } catch (e) { setErr(e.message) }
  }
  async function clearAnswer(outcome) {
    if (!outcome.answer_id) return
    setErr('')
    try { await api.del(`${base}/coverage-gap-answers/${outcome.answer_id}`); load() }
    catch (e) { setErr(e.message) }
  }

  if (!data) return <div className="card"><p className="muted">Loading…</p></div>

  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Coverage check</h2>
      <p className="hint">For each persona, the capabilities their <b>proposed target</b> would deliver
        that nothing in this inventory delivers today. Ask the customer about each: <i>"You don't have
        this today — is that expected, or is it covered somehow outside this inventory?"</i> Only a gap
        the customer confirms is shown as a <b>new outcome</b>; one covered outside the inventory is
        never claimed or costed; an unanswered one is left out until answered.</p>
      {err && <div className="err">{err}</div>}
      {data.personas.length === 0 && <p className="muted">No personas yet — add personas first.</p>}
      <UnreadLicences personas={data.personas} onNavigate={onNavigate} />

      {data.personas.map((p) => (
        <div key={p.persona_id} className="card" style={{ background: 'var(--panel2)' }}>
          <div className="flex-between">
            <b>{p.persona_name} <span className="muted">· {p.headcount} users</span></b>
            {p.has_scenario && (
              <span className="muted">{p.covered_of_target}/{p.target_outcome_count} target outcomes already delivered today</span>
            )}
          </div>

          {/* Honesty guard #1: a current licence nobody can read (TARGET_SCHEMA D23).
              What the group has today is unknown, so its capability changes are left
              out until the licence is answered on Other tools. */}
          {unread(p) && (
            <div style={WARN_CALLOUT}>
              <b className="warn">⚠ Capability changes left out</b>
              <div className="muted" style={{ fontSize: '.82rem', marginTop: '.25rem' }}>
                We don't know what {names(p.unmapped_current_licenses)} include
                {p.unmapped_current_licenses.length > 1 ? '' : 's'}, so nothing can be shown as gained or
                given up for this group. Its cost still counts.
              </div>
              <FixLink step="tools" label="Answer it on Other tools" onNavigate={onNavigate} />
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
                      ? 'a library plan with no capabilities in this engagement: add them in the capability library below, or pick another plan'
                      : 'not a plan the library knows: pick a library plan'}
                  </li>
                ))}
              </ul>
              <FixLink step="future" label="Pick the plan on Future state" onNavigate={onNavigate} />
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
                outcomes of this persona's target look already delivered. Say which groups get{' '}
                {p.org_wide_current_licenses.length > 1 ? 'them' : 'it'} for a per-group value story:
              </div>
              <div className="pill-list" style={{ marginTop: '.35rem' }}>
                {p.org_wide_current_licenses.map((ref) => (
                  <span key={ref} className="badge warn">{ref}</span>
                ))}
              </div>
              <FixLink step="groups" label="Tag them on Groups & licences" onNavigate={onNavigate} />
            </div>
          )}

          {/* Honesty guard #4: outcomes the current Microsoft licensing delivers that
              the target won't — the reverse of the "new outcomes" check below. */}
          {p.dropped_outcomes?.length > 0 && !unread(p) && (
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
              <FixLink step="future" label="Change the plan or add-ons on Future state" onNavigate={onNavigate} />
            </div>
          )}

          {!p.has_scenario ? (
            <p className="muted" style={{ margin: '.5rem 0 0' }}>No future plan yet, so there is nothing to check.{' '}
              <FixLink step="future" label="Pick one on Future state" onNavigate={onNavigate} inline /></p>
          ) : unread(p) ? (
            <p className="muted" style={{ margin: '.5rem 0 0' }}>Nothing to ask until its licences are answered.</p>
          ) : p.target_unmapped ? (
            /* Nothing to validate — the target maps to no capability at all (guard above),
               which is a data gap, not a clean bill of health. */
            <p className="muted" style={{ margin: '.5rem 0 0' }}>Nothing to validate until the target's capability is mapped.</p>
          ) : p.uncovered_outcomes.length === 0 && !(p.covered_outside_outcomes?.length) ? (
            <p className="pos" style={{ margin: '.5rem 0 0' }}>✓ Every outcome the target delivers is already accounted for.</p>
          ) : (
            <>
              {p.uncovered_outcomes.length > 0 && (
                <table>
                  <thead><tr><th>Not delivered by anything in the inventory</th><th style={{ width: 340 }}>Customer's answer <Help k="gaps.answer" /></th></tr></thead>
                  <tbody>
                    {p.uncovered_outcomes.map((o) => (
                      <tr key={o.id}>
                        <td title={o.description}>{o.name}{' '}
                          {o.answer === 'NotDeliveredToday'
                            ? <span className="badge pos">new outcome</span>
                            : <span className="badge warn">not answered</span>}</td>
                        <td>
                          <select value={o.answer || ''} onChange={(e) => {
                            const v = e.target.value
                            if (v === '') clearAnswer(o)
                            else if (v === 'NotDeliveredToday' || v === 'CoveredOutsideInventory') answer(p, o, v)
                            else if (v === '__new') onNavigate && onNavigate('tools')
                            else { clearAnswer(o); mapThirdParty(p, o, v) }
                          }}>
                            <option value="">Not answered yet</option>
                            <option value="NotDeliveredToday">Not delivered today — a new outcome</option>
                            <option value="CoveredOutsideInventory">Covered outside this inventory (don't cost it)</option>
                            {data.third_parties.filter((t) => t.name !== OOS_NAME).map((t) => (
                              <option key={t.id} value={t.id}>Actually covered by: {t.name}</option>
                            ))}
                            <option value="__new">+ Add a third-party solution…</option>
                          </select>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {p.covered_outside_outcomes?.length > 0 && (
                <div style={{ marginTop: '.5rem' }}>
                  <span className="muted" style={{ fontSize: '.82rem' }}>Covered outside this inventory
                    (not costed, not claimed as new):</span>
                  <div className="pill-list" style={{ marginTop: '.3rem' }}>
                    {p.covered_outside_outcomes.map((o) => (
                      <span key={o.id} className="badge muted" title={o.description}>{o.name}{' '}
                        <button className="ghost sm" style={{ padding: '0 .3rem' }}
                          title="Clear this answer" onClick={() => clearAnswer(o)}>×</button></span>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      ))}
    </div>
  )
}

const unread = (p) => p.unmapped_current_licenses?.length > 0
const names = (list) => list.map((u) => u.sku_reference).join(', ')

// Every warning on this step names its fix: a link to the step where it is answered.
function FixLink({ step, label, onNavigate, inline = false }) {
  return (
    <button className="ghost sm" style={inline ? {} : { marginTop: '.4rem' }}
      onClick={() => onNavigate?.(step)}>{label} ›</button>
  )
}

// Shown first, and loudly (WALKTHROUGH step 5): Microsoft licences the library
// can't read, once per name with the groups holding each, linked to their card.
function UnreadLicences({ personas, onNavigate }) {
  const byName = {}
  personas.forEach((p) => (p.unmapped_current_licenses || []).forEach((u) => {
    const key = u.sku_reference.toLowerCase().split(/\s+/).join(' ')
    byName[key] = byName[key] || { name: u.sku_reference, groups: [] }
    byName[key].groups.push(p.persona_name)
  }))
  const list = Object.values(byName)
  if (!list.length) return null
  return (
    <div className="card card-loud" style={{ background: 'var(--panel2)' }}>
      <b className="warn">⚠ {list.length} Microsoft licence{list.length > 1 ? 's' : ''} we can't read yet</b>
      <p className="hint" style={{ margin: '.3rem 0 .4rem' }}>Until each is answered, its cost counts but the
        capability changes of the groups holding it are left out of the report.</p>
      <ul style={{ margin: 0, paddingLeft: '1.1rem', fontSize: '.85rem' }}>
        {list.map((l) => <li key={l.name}><b>{l.name}</b> <span className="muted">— {l.groups.join(', ')}</span></li>)}
      </ul>
      <FixLink step="tools" label="Answer them on Other tools" onNavigate={onNavigate} />
    </div>
  )
}
