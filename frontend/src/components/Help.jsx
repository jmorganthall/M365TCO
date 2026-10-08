import React, { useEffect, useId, useState } from 'react'
import { api } from '../api'

// The walkthrough's help text (docs/WALKTHROUGH.md §9) comes from ONE reviewed
// file on the server — the same file the customer PDF's method page reads — so the
// screen and the paper can never disagree. Loaded once per page and shared.
let _help = null
let _pending = null
export function useHelpText() {
  const [help, setHelp] = useState(_help)
  useEffect(() => {
    if (_help) return
    _pending = _pending || api.get('/api/help-text').then((h) => { _help = h; return h })
    _pending.then(setHelp).catch(() => {})
  }, [])
  return help
}

// An ⓘ beside a question: what we're asking, and how the answer is used. Written
// for the customer to read, since the screen is shared. Opens on hover or focus
// (keyboard) and on tap (touch), and closes on Escape.
export default function Help({ k }) {
  const help = useHelpText()
  const [open, setOpen] = useState(false)
  const id = useId()
  const f = help?.fields?.[k]
  if (!f) return null
  return (
    <span className="help" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button type="button" className="help-btn" aria-label="What is this?"
        aria-expanded={open} aria-describedby={open ? id : undefined}
        onClick={(e) => { e.preventDefault(); setOpen((o) => !o) }}
        onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
        onKeyDown={(e) => { if (e.key === 'Escape') setOpen(false) }}>i</button>
      {open && (
        <span role="tooltip" id={id} className="help-pop">
          <span className="help-h">What we're asking</span>
          <span>{f.ask}</span>
          <span className="help-h">How it's used</span>
          <span>{f.why}</span>
        </span>
      )}
    </span>
  )
}

// The one-line introduction at the top of a walkthrough step.
export function StepIntro({ step }) {
  const help = useHelpText()
  const s = help?.steps?.[step]
  if (!s) return null
  return <p className="step-intro">{s.intro}</p>
}
