import React, { useEffect, useState } from 'react'
import { api } from '../api'

// THE single home of the pricing-basis vocabulary and pickers (segment ×
// term × billing plan). Every surface that shows or edits a basis — Settings'
// global defaults, the engagement editors on Customer Info / Current Licensing,
// and the line-level overrides on license lines and scenarios — renders from
// here, so option lists, labels, and the segments fetch can never drift apart
// again.

export const TERM_LABELS = { P1M: 'Month-to-month', P1Y: '1-year commit', P3Y: '3-year commit' }
export const BILLING_LABELS = { Monthly: 'Pay monthly', Annual: 'Pay yearly', Triennial: 'Pay every 3 years' }
export const termLabel = (v) => TERM_LABELS[v] || v
export const billingLabel = (v) => BILLING_LABELS[v] || v

// Module-level cache: every picker shares one segments fetch per page load.
let _segmentsPromise = null
export function useSegments() {
  const [segments, setSegments] = useState([])
  useEffect(() => {
    if (!_segmentsPromise) {
      _segmentsPromise = api.get('/api/catalog/segments')
        .then((r) => r.segments || []).catch(() => [])
    }
    _segmentsPromise.then(setSegments)
  }, [])
  return segments
}

// The effective basis for a line-level object (license line / scenario):
// its own override, else the engagement default.
export const effectiveBasis = (line, eng) => ({
  segment: line?.segment || eng.default_segment,
  term: line?.term_duration || eng.default_term_duration,
  billing: line?.billing_plan || eng.default_billing_plan,
})

// One select for one basis dimension. kind: 'segment' | 'term' | 'billing'.
// Pass `inheritFrom` (the parent's effective value) for line-level use: it
// renders a leading "Default (…)" option and commits null to mean "inherit".
export function BasisSelect({ kind, value, onChange, meta, inheritFrom, style }) {
  const segments = useSegments()
  const options = kind === 'segment'
    ? (segments.length ? segments : [inheritFrom || value].filter(Boolean))
    : kind === 'term'
      ? (meta?.term_durations || Object.keys(TERM_LABELS))
      : (meta?.billing_plans || Object.keys(BILLING_LABELS))
  const label = kind === 'term' ? termLabel : kind === 'billing' ? billingLabel : (s) => s
  const inherit = inheritFrom !== undefined
  return (
    <select style={style} value={value || ''}
      onChange={(e) => onChange(inherit ? (e.target.value || null) : e.target.value)}>
      {inherit && <option value="">Default ({label(inheritFrom)})</option>}
      {options.filter((v) => !inherit || v !== inheritFrom)
        .map((v) => <option key={v} value={v}>{label(v)}</option>)}
    </select>
  )
}

// The engagement-level basis editor (level 2 of the global → engagement → line
// hierarchy): three selects that PATCH the engagement and report the updated
// engagement back via onUpdate so App refreshes it EVERYWHERE — every tab's
// SKU lookups and quotes follow the same object.
export function EngagementBasisEditor({ engagement, meta, onUpdate, onError }) {
  async function commit(field, value) {
    try {
      const updated = await api.patch(`/api/engagements/${engagement.id}`, { [field]: value })
      onUpdate?.(updated)
    } catch (e) { onError?.(e.message) }
  }
  return (
    <div className="grid c3">
      <div><label>Segment</label>
        <BasisSelect kind="segment" value={engagement.default_segment}
          onChange={(v) => commit('default_segment', v)} /></div>
      <div><label>Term</label>
        <BasisSelect kind="term" value={engagement.default_term_duration} meta={meta}
          onChange={(v) => commit('default_term_duration', v)} /></div>
      <div><label>Payment</label>
        <BasisSelect kind="billing" value={engagement.default_billing_plan} meta={meta}
          onChange={(v) => commit('default_billing_plan', v)} /></div>
    </div>
  )
}
