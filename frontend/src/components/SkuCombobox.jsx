import React, { useEffect, useState } from 'react'
import { api } from '../api'

// Shared searchable SKU picker. Type a few characters (e.g. "O365 E5") and the
// list ranks by SIMILARITY — closest matches first — rather than requiring the
// query to be an exact substring. Value stays a plain string so seeded coverage
// shortcodes (F1/F3/E3/E5) and catalog titles both work.
//
// Basis-aware: when a `segment` / `term` / `billing` are supplied (from the
// license line's effective pricing basis), the list narrows to that priced
// variant so the price shown — and the one seeded on select — is the right one.
// Distinct-priced variants that share a title are NOT collapsed (that silent
// collapse is what let a cheaper same-titled SKU mis-seed the price); each
// distinct price is its own option with its price/segment/term visible.

// Module-level cache so many rows share a single catalog fetch. Returns the FULL
// catalog (limit is opt-in on the API now), so ranking sees every SKU.
let _skusPromise = null
export function loadSkus() {
  if (!_skusPromise) {
    _skusPromise = api.get('/api/catalog/skus').catch(() => [])
  }
  return _skusPromise
}

// Seeded coverage-library shortcodes that may not appear as catalog titles.
const SEED_REFS = ['F1', 'F3', 'E3', 'E5']

// Annual term is the common baseline; used only to order variants when no
// explicit term basis is given.
const TERM_RANK = { P1Y: 0, P1M: 1, P3Y: 2 }

const _norm = (s) => (s || '').toLowerCase().replace(/\s+/g, ' ').trim()
// Expand common license shorthand so "M365 E3" matches "Microsoft 365 E3".
const _expand = (s) => _norm(s)
  .replace(/\bm365\b/g, 'microsoft 365')
  .replace(/\bo365\b/g, 'office 365')
  .replace(/\bems\b/g, 'enterprise mobility security')
const _tokens = (s) => _expand(s).replace(/[^a-z0-9 ]+/g, ' ').split(/\s+/).filter(Boolean)

// Similarity score of a SKU against a query. Higher = closer; 0 = no signal.
// Tiered so stronger evidence dominates: exact title > substring > token
// overlap. E3/E5-style codes are short tokens, so token overlap alone ranks
// "…E5" candidates above unrelated ones without ever hard-requiring every token.
function scoreSku(sku, qNorm, qTokens) {
  if (!qNorm) return 0
  const title = _norm(sku.sku_title)
  const prod = _norm(sku.product_title)
  let score = 0
  if (title === qNorm || prod === qNorm) score = Math.max(score, 1000)
  if (title.includes(qNorm) || prod.includes(qNorm)) score = Math.max(score, 700 - title.length)
  if (qNorm.includes(title) && title) score = Math.max(score, 600 - (qNorm.length - title.length))
  if (qTokens.length) {
    for (const cand of [sku.sku_title, sku.product_title]) {
      const ct = _tokens(cand)
      if (!ct.length) continue
      const hit = qTokens.filter((t) => ct.includes(t)).length
      if (!hit) continue
      // Reward coverage of the query, penalize extra noise in the candidate.
      const overlap = hit * 100 - (ct.length - hit) * 4 - (qTokens.length - hit) * 30
      score = Math.max(score, overlap)
    }
  }
  // Prefer the annual-term, cheapest variant among equals so a base SKU wins a
  // tie over a costlier/odd-term one (deterministic, tiny nudge only).
  score -= (TERM_RANK[sku.term_duration] ?? 9) * 0.5
  return score
}

// Narrow the catalog to the effective pricing basis when one is supplied. A
// missing basis dimension is left unfiltered (so the picker still works before
// a basis is chosen). Never returns an empty set purely because of basis: if a
// filter would empty the list, it is relaxed so the user still sees candidates.
function applyBasis(skus, { segment, term, billing } = {}) {
  const step = (rows, pred) => {
    const next = rows.filter(pred)
    return next.length ? next : rows
  }
  let rows = skus
  if (segment) rows = step(rows, (s) => s.segment === segment)
  if (term) rows = step(rows, (s) => s.term_duration === term)
  if (billing) rows = step(rows, (s) => s.billing_plan === billing)
  return rows
}

// Rank catalog SKUs by similarity to `text`, within the effective basis. Returns
// scored, distinct-priced options sorted best-first.
export function rankSkus(skus, text, basis = {}) {
  const qNorm = _norm(text)
  const qTokens = _tokens(text)
  const inBasis = applyBasis(skus || [], basis)
  const scored = inBasis
    .map((s) => ({ sku: s, score: scoreSku(s, qNorm, qTokens) }))
    .filter((r) => (qNorm ? r.score > 0 : true))
    .sort((a, b) => b.score - a.score || Number(a.sku.annual_unit_price) - Number(b.sku.annual_unit_price))
  return scored
}

// Resolve a free-text product string to the single best catalog SKU (or null),
// honoring the pricing basis. Back-compatible 2-arg signature; pass a basis to
// pull the variant that matches the line. Used for validation badges, AI-parse
// canonicalization, and seeding the correct list price.
export function matchSku(skus, text, basis = {}) {
  if (!text || !skus?.length) return null
  const ranked = rankSkus(skus, text, basis)
  return ranked.length ? ranked[0].sku : null
}

const _money = (n) => {
  const v = Number(n) || 0
  return v ? `$${(Math.round((v / 12) * 100) / 100).toLocaleString()}/mo` : '—'
}

// onSelectSku(sku|null) fires when an option is picked: the catalog row for a
// catalog title (basis-resolved, so its price is correct), or null for a
// free-typed / seeded-shortcode choice.
export default function SkuCombobox({
  value, onChange, onSelectSku, placeholder = 'type to filter SKUs…', style,
  segment, term, billing,
}) {
  const [skus, setSkus] = useState([])
  const [open, setOpen] = useState(false)
  useEffect(() => { loadSkus().then(setSkus) }, [])

  const basis = { segment, term, billing }
  const q = (value || '').trim()

  // Build option list: seed shortcodes first, then ranked catalog variants.
  // Dedup by title+price so identical rows collapse but distinct prices remain
  // (that's the fix — a cheaper same-titled variant no longer hides silently).
  const ranked = rankSkus(skus, q, basis)
  const seen = new Set()
  const catalogOpts = []
  for (const { sku } of ranked) {
    const key = `${_norm(sku.sku_title)}|${sku.annual_unit_price}|${sku.segment}`
    if (!sku.sku_title || seen.has(key)) continue
    seen.add(key)
    catalogOpts.push({
      title: sku.sku_title, sub: sku.product_title, sku,
      meta: `${_money(sku.annual_unit_price)} · ${sku.segment} · ${sku.term_duration} · ${sku.billing_plan}`,
    })
  }
  const seedOpts = (q
    ? SEED_REFS.filter((r) => r.toLowerCase().includes(q.toLowerCase()))
    : SEED_REFS
  ).map((r) => ({ title: r, sub: 'seeded coverage', sku: null, meta: '' }))
  const options = [...seedOpts, ...catalogOpts].slice(0, 60)

  return (
    <div className="combo">
      <input
        value={value || ''}
        placeholder={placeholder}
        style={style}
        onFocus={() => setOpen(true)}
        onChange={(e) => { onChange(e.target.value); setOpen(true) }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && options.length > 0 && (
        <div className="combo-list">
          {options.map((o, i) => (
            <div
              key={`${o.title}-${i}`}
              className={`combo-item ${o.title === value ? 'sel' : ''}`}
              onMouseDown={() => { onChange(o.title); onSelectSku && onSelectSku(o.sku); setOpen(false) }}
            >
              <span className="combo-id">{o.title}</span>
              {o.sub && o.sub !== o.title && <span className="combo-name">{o.sub}</span>}
              {o.meta && <span className="combo-name" style={{ marginLeft: 'auto', opacity: 0.8 }}>{o.meta}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
