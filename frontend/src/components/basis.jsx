import React, { useEffect, useState } from 'react'
import { api } from '../api'

// THE single home of the pricing-basis vocabulary and pickers (segment ×
// term × billing plan). Every surface that shows or edits a basis — Settings'
// global defaults, the engagement editors on Customer Info / Current Licensing,
// and the line-level overrides on license lines and scenarios — renders from
// here, so option lists, labels, and the segments fetch can never drift apart
// again.

// Pretty labels over the RAW sheet values. Unknown values still render — a
// generated label for P<n>Y/P<n>M terms, or the raw value itself — so a new
// value in a future price sheet needs no code change to be usable.
export const TERM_LABELS = { P1M: 'Month-to-month', P1Y: '1-year commit', P3Y: '3-year commit' }
export const BILLING_LABELS = { Monthly: 'Pay monthly', Annual: 'Pay yearly', Triennial: 'Pay every 3 years' }
export const termLabel = (v) => {
  if (TERM_LABELS[v]) return TERM_LABELS[v]
  const m = /^P(\d+)([MY])$/.exec(v || '')
  if (m) return m[2] === 'Y' ? `${m[1]}-year commit` : `${m[1]}-month commit`
  return v
}
export const billingLabel = (v) => BILLING_LABELS[v] || v

// The picker value lists come from the DATA — distinct values in the loaded
// price sheet (∪ known defaults), via /api/catalog/basis-options. Module-level
// cache: every picker shares one fetch per page load.
let _optionsPromise = null
export function useBasisOptions() {
  const [options, setOptions] = useState({ segments: [], terms: [], billing_plans: [] })
  useEffect(() => {
    if (!_optionsPromise) {
      _optionsPromise = api.get('/api/catalog/basis-options')
        .catch(() => ({ segments: [], terms: [], billing_plans: [] }))
    }
    _optionsPromise.then(setOptions)
  }, [])
  return options
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
  const data = useBasisOptions()
  // Data-driven lists first; static fallbacks only cover the moment before the
  // options fetch resolves (or an offline failure).
  const fallback = kind === 'segment'
    ? [inheritFrom || value].filter(Boolean)
    : kind === 'term'
      ? (meta?.term_durations || Object.keys(TERM_LABELS))
      : (meta?.billing_plans || Object.keys(BILLING_LABELS))
  const fetched = kind === 'segment' ? data.segments
    : kind === 'term' ? data.terms : data.billing_plans
  let options = fetched.length ? fetched : fallback
  // A stored value outside the list (e.g. from an older sheet) stays selectable.
  if (value && !options.includes(value)) options = [...options, value]
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
