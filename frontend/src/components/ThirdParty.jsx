import React, { useEffect, useState } from 'react'
import { api, usd, pct } from '../api'

export default function ThirdParty({ engagement, meta }) {
  const base = `/api/engagements/${engagement.id}/third-party`
  const [items, setItems] = useState([])
  const [err, setErr] = useState('')
  const blank = {
    name: '', vendor: '', raw_cost: 0, cost_period: 'Annual', unit_basis: 'Users',
    covered_count: 0, renewal_date: '', is_managed: false, tooling_pct: '', source_tag: 'CustomerStated',
  }
  const [form, setForm] = useState(blank)
  // AI paste-to-parse state.
  const [aiEnabled, setAiEnabled] = useState(false)
  const [rawText, setRawText] = useState('')
  const [parsing, setParsing] = useState(false)
  const [parsed, setParsed] = useState(null)

  const load = () => api.get(base).then(setItems).catch((e) => setErr(e.message))
  useEffect(() => {
    load()
    api.get('/api/admin/ai/status').then((s) => setAiEnabled(s.enabled)).catch(() => {})
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
          cost_period: r.cost_period, unit_basis: 'Users',
          covered_count: Number(r.covered_count) || 0, renewal_date: null,
          is_managed: false, tooling_pct: null, source_tag: 'CustomerStated',
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
        raw_cost: Number(form.raw_cost),
        covered_count: Number(form.covered_count),
        renewal_date: form.renewal_date || null,
        tooling_pct: form.tooling_pct === '' ? null : Number(form.tooling_pct),
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
      <h2>Third-party products</h2>
      <p className="hint">The managed split keeps management cost out of the comparison.
        An unmanaged product counts at 100%; a managed product counts at its tooling
        percentage (default {pct(meta?.default_tooling_pct)}). Effective cost is what feeds displacement math.</p>
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
                      <th>Period</th><th className="num">Covers</th>
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

      <table>
        <thead><tr>
          <th>Product</th><th className="num">Cost</th><th>Period</th><th className="num">Covers</th>
          <th>Managed</th><th className="num">Tooling%</th><th className="num">Effective $/yr</th>
          <th className="num">$/unit/yr</th><th>Renewal</th><th></th>
        </tr></thead>
        <tbody>
          {items.map((t) => (
            <tr key={t.id}>
              <td><input value={t.name} onChange={(e) => update(t.id, { name: e.target.value })} style={{ minWidth: 110 }} /></td>
              <td className="num"><input type="number" style={{ width: 90 }} value={t.raw_cost}
                onChange={(e) => update(t.id, { raw_cost: Number(e.target.value) })} /></td>
              <td>
                <select value={t.cost_period} onChange={(e) => update(t.id, { cost_period: e.target.value })}>
                  {(meta?.cost_periods || []).map((s) => <option key={s}>{s}</option>)}
                </select>
              </td>
              <td className="num"><input type="number" style={{ width: 70 }} value={t.covered_count}
                onChange={(e) => update(t.id, { covered_count: Number(e.target.value) })} /></td>
              <td><input type="checkbox" style={{ width: 'auto' }} checked={t.is_managed}
                onChange={(e) => update(t.id, { is_managed: e.target.checked })} /></td>
              <td className="num">
                {t.is_managed
                  ? <input type="number" step="0.05" style={{ width: 70 }} value={t.tooling_pct}
                      onChange={(e) => update(t.id, { tooling_pct: Number(e.target.value) })} />
                  : <span className="muted">—</span>}
              </td>
              <td className="num">{usd(t.effective_annual_cost)}</td>
              <td className="num">{usd(t.per_unit_annual_cost)}</td>
              <td><input type="date" value={t.renewal_date || ''} style={{ width: 130 }}
                onChange={(e) => update(t.id, { renewal_date: e.target.value || null })} /></td>
              <td className="num"><button className="danger sm" onClick={() => remove(t.id)}>Remove</button></td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="grid c4" style={{ marginTop: '.8rem' }}>
        <div><label>Name</label>
          <input value={form.name} placeholder="Okta"
            onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
        <div><label>Cost</label>
          <input type="number" value={form.raw_cost}
            onChange={(e) => setForm({ ...form, raw_cost: e.target.value })} /></div>
        <div><label>Period</label>
          <select value={form.cost_period} onChange={(e) => setForm({ ...form, cost_period: e.target.value })}>
            {(meta?.cost_periods || []).map((s) => <option key={s}>{s}</option>)}
          </select></div>
        <div><label>Covered count</label>
          <input type="number" value={form.covered_count}
            onChange={(e) => setForm({ ...form, covered_count: e.target.value })} /></div>
        <div><label><input type="checkbox" style={{ width: 'auto', marginRight: 6 }}
          checked={form.is_managed} onChange={(e) => setForm({ ...form, is_managed: e.target.checked })} />Managed (tool + management)</label></div>
        <div><label>Tooling % override</label>
          <input type="number" step="0.05" value={form.tooling_pct} placeholder="default"
            onChange={(e) => setForm({ ...form, tooling_pct: e.target.value })} /></div>
        <div><label>Renewal date</label>
          <input type="date" value={form.renewal_date}
            onChange={(e) => setForm({ ...form, renewal_date: e.target.value })} /></div>
        <div style={{ display: 'flex', alignItems: 'flex-end' }}>
          <button onClick={add}>Add product</button></div>
      </div>
    </div>
  )
}
