import React, { useEffect, useState } from 'react'
import { api, money, pct } from '../api'
import Help from './Help.jsx'

// A number input for an inline (auto-saving) line-item field. Holds local text
// state so you can clear it and TYPE a new number freely; it commits on blur or
// Enter — not on every keystroke (which, with the row's save-and-reload, snapped
// the value back and made the field un-typeable). `allowEmpty` commits null.
function NumInput({ value, onCommit, style, step, disabled, placeholder, allowEmpty }) {
  const [v, setV] = useState(value ?? '')
  useEffect(() => { setV(value ?? '') }, [value])
  function commit() {
    if (allowEmpty && String(v).trim() === '') { onCommit(null); return }
    const n = Number(v)
    onCommit(Number.isFinite(n) ? n : 0)
  }
  return (
    <input type="number" style={style} step={step} disabled={disabled} placeholder={placeholder}
      value={v}
      onChange={(e) => setV(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }} />
  )
}

// Who uses a tool: tag whole groups (the default reading), or "All groups".
// Untagged with no covers number means no users — the tool is left out.
function UsedBy({ personas, tagIds, onChange, dimmed }) {
  const toggle = (pid) => onChange(tagIds.includes(pid) ? tagIds.filter((x) => x !== pid) : [...tagIds, pid])
  const all = personas.length > 0 && personas.every((p) => tagIds.includes(p.id))
  return (
    <div className="pill-list">
      {personas.map((p) => (
        <button key={p.id} type="button" className={`tag-toggle ${tagIds.includes(p.id) ? 'on' : ''} ${dimmed ? 'inactive' : ''}`}
          onClick={() => toggle(p.id)}>{p.name}</button>
      ))}
      {personas.length > 1 && (
        <button type="button" className="ghost sm" onClick={() => onChange(all ? [] : personas.map((p) => p.id))}>
          {all ? 'none' : 'all groups'}</button>
      )}
      {personas.length === 0 && <span className="muted">Add groups first.</span>}
    </div>
  )
}

// One tool as an expandable line item: the walkthrough's questions on the row
// (name, cost, renewal, who uses it, managed), the unusual details — vendor, the
// managed service's software share, a covers number that differs from the
// groups' headcount — in the expander (docs/WALKTHROUGH.md §3, step 3).
function ProductRow({ t, meta, personas, moneyUnit, update, remove }) {
  const [open, setOpen] = useState(false)
  const tagIds = t.persona_ids || []
  const overridden = t.covered_count_override != null
  const leftOut = !(Number(t.raw_cost) > 0) || !(t.covered_count > 0)
  return (
    <>
      <tr>
        <td><button className="ghost sm" title="Details" onClick={() => setOpen(!open)}>{open ? '▾' : '▸'}</button></td>
        <td data-label="Tool"><input value={t.name} style={{ minWidth: 120 }} onChange={(e) => update(t.id, { name: e.target.value })} />
          {leftOut && <div><span className="badge warn" title="A tool with no cost or no users is left out of the numbers">
            left out — {!(Number(t.raw_cost) > 0) ? 'no cost' : 'no users'}</span></div>}</td>
        <td className="num" data-label="Cost"><NumInput value={t.raw_cost} style={{ width: 90 }}
          onCommit={(n) => update(t.id, { raw_cost: n })} /></td>
        <td data-label="Per">
          <select value={t.cost_period} onChange={(e) => update(t.id, { cost_period: e.target.value })}>
            {(meta?.cost_periods || []).map((s) => <option key={s}>{s}</option>)}
          </select>
        </td>
        <td data-label="Renews"><input type="date" value={t.renewal_date || ''} style={{ width: 140 }}
          onChange={(e) => update(t.id, { renewal_date: e.target.value || null })} />
          {!t.renewal_date && <div className="src warn" style={{ fontSize: '.72rem' }}>assumed in a year</div>}</td>
        <td data-label="Used by"><UsedBy personas={personas} tagIds={tagIds} dimmed={overridden}
          onChange={(ids) => update(t.id, { persona_ids: ids })} />
          {overridden && <div className="src" style={{ fontSize: '.72rem' }}>covers {t.covered_count_override} (set in details)</div>}</td>
        <td data-label="Managed"><input type="checkbox" style={{ width: 'auto' }} checked={t.is_managed}
          title="Bought as part of a managed service"
          onChange={(e) => update(t.id, { is_managed: e.target.checked })} />
          {t.is_managed && <span className="muted" style={{ fontSize: '.75rem', marginLeft: 4 }}>{pct(t.tooling_pct)}</span>}</td>
        <td className="num" data-label="Counted">{money(t.effective_annual_cost, moneyUnit)}</td>
        <td className="num"><button className="danger sm" onClick={() => remove(t.id)}>Remove</button></td>
      </tr>
      {open && (
        <tr className="detail-row">
          <td></td>
          <td colSpan={8} style={{ background: 'var(--panel2)' }}>
            <div className="grid c4" style={{ padding: '.4rem 0' }}>
              <div><label>Vendor <Help k="tools.vendor" /></label>
                <input value={t.vendor || ''} onChange={(e) => update(t.id, { vendor: e.target.value })} /></div>
              <div><label>Software share of a managed service <Help k="tools.tooling_pct" /></label>
                <NumInput value={t.tooling_pct} step="0.05" disabled={!t.is_managed}
                  onCommit={(n) => update(t.id, { tooling_pct: n })} />
                <small className="src">{t.is_managed ? 'A fraction: 0.30 = 30%.' : 'Only when bought as a managed service.'}</small></div>
              <div><label>People covered <Help k="tools.covers" /></label>
                <NumInput value={t.covered_count_override} allowEmpty
                  placeholder={String(t.persona_covered_count ?? 0)}
                  onCommit={(n) => update(t.id, { covered_count_override: n })} />
                <small className="src">Blank = the groups' headcount ({t.persona_covered_count}).</small></div>
              <div><label>Counted · per person</label>
                <div className="muted" style={{ paddingTop: '.35rem' }}>{t.covered_count} people · {money(t.effective_annual_cost, moneyUnit)} · {money(t.per_unit_annual_cost, moneyUnit)} each</div>
                <small className="src">The cost counted (after any managed-service share), split per person covered.</small></div>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

export default function ThirdParty({ engagement, meta, moneyUnit = 'mo' }) {
  const base = `/api/engagements/${engagement.id}/third-party`
  const [items, setItems] = useState([])
  const [err, setErr] = useState('')
  const blank = {
    name: '', raw_cost: '', cost_period: 'Annual', renewal_date: '', is_managed: false,
    persona_ids: [], source_tag: 'CustomerStated',
  }
  const [form, setForm] = useState(blank)
  const [personas, setPersonas] = useState([])
  // AI paste-to-parse state.
  const [aiEnabled, setAiEnabled] = useState(false)
  const [rawText, setRawText] = useState('')
  const [parsing, setParsing] = useState(false)
  const [parsed, setParsed] = useState(null)

  const load = () => api.get(base).then(setItems).catch((e) => setErr(e.message))
  useEffect(() => {
    load()
    api.get('/api/admin/ai/status').then((s) => setAiEnabled(s.enabled)).catch(() => {})
    api.get(`/api/engagements/${engagement.id}/personas`).then(setPersonas).catch(() => {})
  }, [engagement.id])

  async function parseText() {
    if (!rawText.trim()) return
    setParsing(true); setErr('')
    try {
      const res = await api.post(`/api/admin/engagements/${engagement.id}/ai/parse-third-party`, { raw_text: rawText })
      setParsed((res.rows || []).map((r) => ({ ...r, _include: true })))
    } catch (e) { setErr(e.message) } finally { setParsing(false) }
  }
  const setParsedField = (i, patch) =>
    setParsed((rows) => rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  async function addParsed() {
    const rows = (parsed || []).filter((r) => r._include && r.name.trim())
    setErr('')
    try {
      for (const r of rows) {
        await api.post(base, {
          name: r.name, vendor: r.vendor || '', raw_cost: Number(r.raw_cost) || 0,
          cost_period: r.cost_period,
          covered_count_override: Number(r.covered_count) || null, renewal_date: null,
          is_managed: !!r.is_managed, tooling_pct: null, source_tag: 'CustomerStated',
        })
      }
      setParsed(null); setRawText(''); load()
    } catch (e) { setErr(e.message) }
  }

  async function add() {
    if (!form.name.trim()) return
    try {
      await api.post(base, {
        ...form,
        raw_cost: Number(form.raw_cost) || 0,
        renewal_date: form.renewal_date || null,
      })
      setForm(blank); load()
    } catch (e) { setErr(e.message) }
  }
  async function update(id, patch) {
    try { await api.patch(`${base}/${id}`, patch); load() } catch (e) { setErr(e.message) }
  }
  async function remove(id) {
    try { await api.del(`${base}/${id}`); load() } catch (e) { setErr(e.message) }
  }

  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Other tools</h2>
      {err && <div className="err">{err}</div>}

      {aiEnabled && (
        <div className="card" style={{ background: 'var(--panel2)', marginBottom: '.8rem' }}>
          <div className="flex-between">
            <b>Paste from customer (AI)</b>
            <small className="src">Parsed into rows you review before anything is added.</small>
          </div>
          <textarea rows={4} value={rawText} placeholder={'Paste a budget table or vendor list, e.g.\nSentinelONE\t$102,000\nOkta\t$215,000'}
            style={{ width: '100%', marginTop: '.4rem', fontFamily: 'inherit' }}
            onChange={(e) => setRawText(e.target.value)} />
          <button className="sm" disabled={parsing || !rawText.trim()} onClick={parseText}>
            {parsing ? 'Formatting…' : '✨ Format with AI'}
          </button>

          {parsed && (
            <div style={{ marginTop: '.6rem' }}>
              {parsed.length === 0 && <p className="muted">No products found in that text.</p>}
              {parsed.length > 0 && (
                <>
                  <table>
                    <thead><tr>
                      <th>Add</th><th>Product</th><th>Vendor</th><th className="num">Cost</th>
                      <th>Period</th><th className="num">Covers</th><th>Managed</th>
                    </tr></thead>
                    <tbody>
                      {parsed.map((r, i) => (
                        <tr key={i} style={r._include ? {} : { opacity: 0.45 }}>
                          <td><input type="checkbox" style={{ width: 'auto' }} checked={r._include}
                            onChange={(e) => setParsedField(i, { _include: e.target.checked })} /></td>
                          <td><input value={r.name} style={{ minWidth: 140 }}
                            onChange={(e) => setParsedField(i, { name: e.target.value })} /></td>
                          <td><input value={r.vendor} style={{ width: 90 }}
                            onChange={(e) => setParsedField(i, { vendor: e.target.value })} /></td>
                          <td className="num"><input type="number" style={{ width: 90 }} value={r.raw_cost}
                            onChange={(e) => setParsedField(i, { raw_cost: e.target.value })} /></td>
                          <td>
                            <select value={r.cost_period} onChange={(e) => setParsedField(i, { cost_period: e.target.value })}>
                              {(meta?.cost_periods || ['Annual', 'Monthly']).map((s) => <option key={s}>{s}</option>)}
                            </select>
                          </td>
                          <td className="num"><input type="number" style={{ width: 70 }} value={r.covered_count}
                            onChange={(e) => setParsedField(i, { covered_count: e.target.value })} /></td>
                          <td><input type="checkbox" style={{ width: 'auto' }} checked={!!r.is_managed}
                            onChange={(e) => setParsedField(i, { is_managed: e.target.checked })} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="toolbar" style={{ marginTop: '.5rem' }}>
                    <button className="sm" onClick={addParsed}>
                      Add {parsed.filter((r) => r._include && r.name.trim()).length} selected
                    </button>
                    <button className="ghost sm" onClick={() => setParsed(null)}>Discard</button>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      )}

      <table className="resp-table">
        <thead><tr>
          <th></th><th>Tool <Help k="tools.name" /></th><th className="num">Cost <Help k="tools.cost" /></th><th>Per</th>
          <th>Renews <Help k="tools.renewal" /></th><th>Used by <Help k="tools.used_by" /></th>
          <th>Managed <Help k="tools.managed" /></th><th className="num">Counted</th><th></th>
        </tr></thead>
        <tbody>
          {items.map((t) => (
            <ProductRow key={t.id} t={t} meta={meta} personas={personas} moneyUnit={moneyUnit}
              update={update} remove={remove} />
          ))}
        </tbody>
      </table>
      <small className="src">What each tool is used for is ticked below, under <b>What each tool is used for</b>.</small>

      <div className="grid c4" style={{ marginTop: '.8rem' }}>
        <div><label>Tool</label>
          <input value={form.name} placeholder="Tool name" autoComplete="off"
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            onKeyDown={(e) => e.key === 'Enter' && add()} /></div>
        <div><label>Cost</label>
          <div style={{ display: 'flex', gap: '.3rem' }}>
            <input type="number" value={form.raw_cost} placeholder="0"
              onChange={(e) => setForm({ ...form, raw_cost: e.target.value })} />
            <select value={form.cost_period} onChange={(e) => setForm({ ...form, cost_period: e.target.value })}>
              {(meta?.cost_periods || []).map((s) => <option key={s}>{s}</option>)}
            </select></div></div>
        <div><label>Renews</label>
          <input type="date" value={form.renewal_date}
            onChange={(e) => setForm({ ...form, renewal_date: e.target.value })} /></div>
        <div><label><input type="checkbox" style={{ width: 'auto', marginRight: 6 }}
          checked={form.is_managed} onChange={(e) => setForm({ ...form, is_managed: e.target.checked })} />Managed service</label></div>
        <div style={{ gridColumn: 'span 3' }}><label>Used by</label>
          <UsedBy personas={personas} tagIds={form.persona_ids}
            onChange={(ids) => setForm({ ...form, persona_ids: ids })} /></div>
        <div style={{ display: 'flex', alignItems: 'flex-end' }}>
          <button onClick={add}>Add tool</button></div>
      </div>
    </div>
  )
}
