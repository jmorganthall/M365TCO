import React, { useEffect, useState } from 'react'

// A text/number input that saves once — on Enter or leaving the field — never per
// keystroke. Every save re-reads the calculated numbers, so saving per key would
// re-run the model on every character and let replies land out of order. Shows
// the server's value whenever the field is not being edited.
export function TextInput({ value, onCommit, ...rest }) {
  const [v, setV] = useState(value ?? '')
  const [focused, setFocused] = useState(false)
  useEffect(() => { if (!focused) setV(value ?? '') }, [value, focused])
  const commit = () => { setFocused(false); if (String(v) !== String(value ?? '')) onCommit(v) }
  return (
    <input {...rest} value={v}
      onFocus={() => setFocused(true)}
      onChange={(e) => setV(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }} />
  )
}
