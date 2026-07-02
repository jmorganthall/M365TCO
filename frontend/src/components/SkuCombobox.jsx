import React, { useEffect, useState } from 'react'
import { api } from '../api'

// Shared searchable SKU picker — same interaction as the AI model combobox:
// type a few characters (e.g. "E5") to filter the Product SKU catalog, or keep
// typing a free-text reference. Value stays a plain string so the seeded
// coverage shortcodes (F1/F3/E3/E5) and catalog titles both work.

// Module-level cache so many rows share a single catalog fetch.
let _skusPromise = null
function loadSkus() {
  if (!_skusPromise) {
    _skusPromise = api.get('/api/catalog/skus?limit=1000').catch(() => [])
  }
  return _skusPromise
}

// Seeded coverage-library shortcodes that may not appear as catalog titles.
const SEED_REFS = ['F1', 'F3', 'E3', 'E5']

export default function SkuCombobox({ value, onChange, placeholder = 'type to filter SKUs…', style }) {
  const [skus, setSkus] = useState([])
  const [open, setOpen] = useState(false)
  useEffect(() => { loadSkus().then(setSkus) }, [])

  // Build deduped option list: seed shortcodes first, then catalog titles.
  const seen = new Set()
  const options = []
  for (const o of [
    ...SEED_REFS.map((r) => ({ title: r, sub: 'seeded coverage' })),
    ...skus.map((s) => ({ title: s.sku_title, sub: s.product_title })),
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
              onMouseDown={() => { onChange(o.title); setOpen(false) }}
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
