import React, { useEffect, useRef, useState } from 'react'

// A date input for an auto-saving field. Holds local state so you can TYPE a
// date freely. While you type the year the browser reports partial years
// (0002, 0020, 0202 …); saving each one with the row's save-and-reload snapped
// the field back and made it un-typeable. So a date saves only once its year is
// real (1900+) and you've paused typing, or on blur / Enter (where clearing it
// saves null) — the pause means a date typed over an old one saves once, not
// once per keystroke that happens to form a valid date. While the field has
// focus a reload doesn't overwrite what you're typing; on blur it shows the
// stored value again.
const complete = (d) => !!d && Number(d.slice(0, 4)) >= 1900

export default function DateInput({ value, onCommit, style, disabled }) {
  const [v, setV] = useState(value || '')
  const focused = useRef(false)
  const sent = useRef(value || '')
  const timer = useRef(null)
  useEffect(() => () => clearTimeout(timer.current), [])
  useEffect(() => {
    sent.current = value || ''
    if (!focused.current) setV(value || '')
  }, [value])
  function send(d) {
    clearTimeout(timer.current)
    if (d === sent.current) return
    sent.current = d
    onCommit(d || null)
  }
  function change(d) {
    setV(d)
    clearTimeout(timer.current)
    if (complete(d)) timer.current = setTimeout(() => send(d), 700)
  }
  function blur() {
    focused.current = false
    if (v === '' || complete(v)) send(v)
    setV(sent.current)
  }
  return (
    <input type="date" style={style} disabled={disabled} value={v}
      onFocus={() => { focused.current = true }}
      onChange={(e) => change(e.target.value)}
      onBlur={blur}
      onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }} />
  )
}
