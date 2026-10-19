import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api'

// Settings → Readout branding (TARGET_SCHEMA §3.5, D25): how the practice brands
// the customer readout — one logo, three colours and four short blocks of
// wording, set once for every engagement. Each field left empty uses the
// built-in default (shown greyed), which is never written back. The server
// sanitizes the wording to bold, italic, links and the highlight colour.

const COLORS = [
  ['primary_color', 'Primary', 'Headings, the cover band and the headline band.'],
  ['accent_color', 'Accent', 'Labels, rules and the bar for tools the new plans retire.'],
  ['highlight_color', 'Highlight', 'The headline figure on the band, highlighted words, the cover rule.'],
]
const BLOCKS = [
  ['tagline', 'Tagline', 'On the cover, under the brand rule. Use Highlight for the accent word.'],
  ['cover_subtitle', 'Cover subtitle', 'Under the customer’s name on the cover.'],
  ['closing_block', 'Closing block', 'Ends the readout, after the next steps — e.g. your team and how to reach it.'],
  ['footer_disclaimer', 'Footer disclaimer', 'On every page’s footer, in small print.'],
]
const LOGO_TYPES = /^image\/(png|jpeg|gif|webp|svg\+xml)$/

export default function ReadoutBranding({ onMsg, onErr }) {
  const [b, setB] = useState(null)
  const load = () => api.get('/api/admin/branding').then(setB).catch((e) => onErr(e.message))
  useEffect(() => { load() }, [])

  async function save(changes) {
    try {
      setB(await api.put('/api/admin/branding', changes))
      window.dispatchEvent(new Event('branding-saved'))  // the top bar shows the logo
      onMsg('Readout branding saved.')
    } catch (e) { onErr(e.message) }
  }

  function onLogo(file) {
    if (!file) return
    if (!LOGO_TYPES.test(file.type)) { onErr('The logo must be a PNG, JPEG, GIF, WebP or SVG image.'); return }
    if (file.size > 2 * 1024 * 1024) { onErr('The logo must be 2 MB or smaller.'); return }
    const reader = new FileReader()
    reader.onload = () => save({ logo_data_url: reader.result })
    reader.readAsDataURL(file)
  }

  if (!b) return <div className="card"><h2>Readout branding</h2><p className="hint">Loading…</p></div>
  const r = b.resolved || {}
  return (
    <div className="card">
      <h2>Readout branding</h2>
      <p className="hint">How your practice brands the customer readout and PDF, for every
        engagement; your logo also appears in the app's top bar. The customer’s own logo is
        set on each engagement’s Customer step.
        Leave a field empty to use the built-in default.
        {b.updated_at && <> Last saved <b>{new Date(b.updated_at + 'Z').toLocaleString()}</b>.</>}</p>

      <label>Your logo</label>
      <div className="brand-logo-row">
        {b.logo_data_url
          ? <><img src={b.logo_data_url} alt="Practice logo" className="brand-logo-preview" />
              <button className="ghost sm" onClick={() => save({ logo_data_url: '' })}>Remove</button></>
          : <span className="muted">No logo — the cover shows the customer only.</span>}
        <input type="file" accept="image/png,image/jpeg,image/gif,image/webp,image/svg+xml"
          onChange={(e) => onLogo(e.target.files?.[0])} />
      </div>
      <small className="src">One version is enough: it is only placed on white. SVG or a
        transparent PNG looks sharpest.</small>

      <div className="grid c3" style={{ marginTop: '.8rem' }}>
        {COLORS.map(([k, label, hint]) => (
          <div key={k}>
            <label>{label} colour</label>
            <div className="brand-color-row">
              <input type="color" value={b[k] || r[k] || '#000000'}
                onChange={(e) => setB({ ...b, [k]: e.target.value })}
                onBlur={(e) => save({ [k]: e.target.value })} />
              <code className={b[k] ? '' : 'muted'}>{b[k] || `${r[k]} (default)`}</code>
              {b[k] && <button className="ghost sm" onClick={() => save({ [k]: '' })}>Default</button>}
            </div>
            <small className="src">{hint}</small>
          </div>
        ))}
      </div>

      {BLOCKS.map(([k, label, hint]) => (
        <div key={k} style={{ marginTop: '.8rem' }}>
          <label>{label}</label>
          <RichText value={b[k] || ''} placeholder={b[k] ? '' : plain(r[k])}
            highlight={b.highlight_color || r.highlight_color}
            onSave={(html) => { if (html !== (b[k] || '')) save({ [k]: html }) }} />
          <small className="src">{hint}</small>
        </div>
      ))}
    </div>
  )
}

function plain(html) {
  const d = document.createElement('div')
  d.innerHTML = html || ''
  return d.textContent || ''
}

// A small rich-text field: bold, italic, highlight and link. It edits HTML in
// place and saves on blur; the server keeps only those four kinds of markup.
function RichText({ value, placeholder, highlight, onSave }) {
  const ref = useRef(null)
  useEffect(() => {
    if (ref.current && document.activeElement !== ref.current) ref.current.innerHTML = value
  }, [value])

  const run = (cmd, arg) => { ref.current?.focus(); document.execCommand(cmd, false, arg) }
  function toggleHighlight() {
    const sel = window.getSelection()
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return
    const node = sel.anchorNode?.parentElement?.closest('span.hl')
    if (node && ref.current?.contains(node)) {
      node.replaceWith(...node.childNodes)            // un-highlight
      return
    }
    const range = sel.getRangeAt(0)
    if (!ref.current?.contains(range.commonAncestorContainer)) return
    const span = document.createElement('span')
    span.className = 'hl'
    span.appendChild(range.extractContents())
    range.insertNode(span)
  }
  function link() {
    const url = prompt('Link address (https://… or mailto:…)')
    if (url && /^(https?:\/\/|mailto:)/i.test(url.trim())) run('createLink', url.trim())
  }
  return (
    <div className="richtext" style={{ '--hl': highlight }}>
      <div className="richtext-bar">
        <button type="button" className="ghost sm" title="Bold" onMouseDown={(e) => { e.preventDefault(); run('bold') }}><b>B</b></button>
        <button type="button" className="ghost sm" title="Italic" onMouseDown={(e) => { e.preventDefault(); run('italic') }}><i>I</i></button>
        <button type="button" className="ghost sm" title="Highlight colour" onMouseDown={(e) => { e.preventDefault(); toggleHighlight() }}>
          <span className="hl">A</span></button>
        <button type="button" className="ghost sm" title="Link" onMouseDown={(e) => { e.preventDefault(); link() }}>Link</button>
        <button type="button" className="ghost sm" title="Remove formatting" onMouseDown={(e) => { e.preventDefault(); run('removeFormat') }}>Clear</button>
      </div>
      <div ref={ref} className="richtext-body" contentEditable suppressContentEditableWarning
        data-placeholder={placeholder}
        onBlur={() => onSave(ref.current.innerHTML.replace(/^(<br>)+$/, ''))} />
    </div>
  )
}
