import React, { useEffect, useState } from 'react'
import { api } from '../api'

// Shared searchable SKU picker — same interaction as the AI model combobox:
// type a few characters (e.g. "E5") to filter the Product SKU catalog, or keep
// typing a free-text reference. Value stays a plain string so the seeded
// coverage shortcodes (F1/F3/E3/E5) and catalog titles both work.

// Module-level cache so many rows share a single catalog fetch.
let _skusPromise = null
export function loadSkus() {
  if (!_skusPromise) {
    _skusPromise = api.get('/api/catalog/skus?limit=1000').catch(() => [])
  }
  return _skusPromise
}

// Seeded coverage-library shortcodes that may not appear as catalog titles.
const SEED_REFS = ['F1', 'F3', 'E3', 'E5']

// When several priced variants share a SKU title, keep the annual-term one —
// its price is what auto-fill pulls, and annual commitment is the common baseline.
const TERM_RANK = { P1Y: 0, P1M: 1, P3Y: 2 }

const _norm = (s) => (s || '').toLowerCase().replace(/\s+/g, ' ').trim()

// Resolve a free-text product string to a catalog SKU row, or null. Tiered:
// exact title/product match, then a substring match either direction. Annual-term
// variants win. Callers use it to validate/canonicalize a pasted description.
export function matchSku(skus, text) {
  const q = _norm(text)
  if (!q || !skus?.length) return null
  const ranked = [...skus].sort(
    (a, b) => (TERM_RANK[a.term_duration] ?? 9) - (TERM_RANK[b.term_duration] ?? 9)
  )
  const exact = ranked.find((s) => _norm(s.sku_title) === q || _norm(s.product_title) === q)
  if (exact) return exact
  return ranked.find((s) => {
    const t = _norm(s.sku_title), p = _norm(s.product_title)
    return (t && (t.includes(q) || q.includes(t))) || (p && (p.includes(q) || q.includes(p)))
  }) || null
}

// onSelectSku(sku|null) fires when an option is picked: the catalog row for a
// catalog title, or null for a free-typed / seeded-shortcode choice. Callers use
// it to pull pricing from the selected SKU; omit it if you only need the string.
export default function SkuCombobox({ value, onChange, onSelectSku, placeholder = 'type to filter SKUs…', style }) {
  const [skus, setSkus] = useState([])
  const [open, setOpen] = useState(false)
  useEffect(() => { loadSkus().then(setSkus) }, [])

  // Build deduped option list: seed shortcodes first, then catalog titles
  // (annual-term variant winning each title so the pulled price is stable).
  const ranked = [...skus].sort(
    (a, b) => (TERM_RANK[a.term_duration] ?? 9) - (TERM_RANK[b.term_duration] ?? 9)
  )
  const seen = new Set()
  const options = []
  for (const o of [
    ...SEED_REFS.map((r) => ({ title: r, sub: 'seeded coverage', sku: null })),
    ...ranked.map((s) => ({ title: s.sku_title, sub: s.product_title, sku: s })),
  ]) {
    if (!o.title || seen.has(o.title)) continue
    seen.add(o.title)
    options.push(o)
  }

  const q = (value || '').trim().toLowerCase()
  const filtered = (q
    ? options.filter((o) => o.title.toLowerCase().includes(q) || (o.sub || '').toLowerCase().includes(q))
    : options
  ).slice(0, 60)

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
      {open && filtered.length > 0 && (
        <div className="combo-list">
          {filtered.map((o) => (
            <div
              key={o.title}
              className={`combo-item ${o.title === value ? 'sel' : ''}`}
              onMouseDown={() => { onChange(o.title); onSelectSku && onSelectSku(o.sku); setOpen(false) }}
            >
              <span className="combo-id">{o.title}</span>
              {o.sub && o.sub !== o.title && <span className="combo-name">{o.sub}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
