import React, { useEffect, useState } from 'react'
import { api, usd, pct } from '../api'
import SkuCombobox from './SkuCombobox.jsx'

// Prices are stored annualized (the engine works in annual USD); the UI edits
// per-seat MONTHLY. Convert at the boundary only.
const annualToMonthly = (a) => (a ? Math.round((Number(a) / 12) * 100) / 100 : 0)
const monthlyToAnnual = (m) => Math.round(Number(m || 0) * 12 * 100) / 100

// Monthly $/seat cell: holds local text so decimals type cleanly, commits the
// annualized value on blur. Resyncs when the stored value changes (e.g. a SKU
// pick auto-fills the price).
function MonthlyPriceInput({ annual, onCommit, style }) {
  const [val, setVal] = useState(annualToMonthly(annual))
  useEffect(() => { setVal(annualToMonthly(annual)) }, [annual])
  return (
    <input type="number" style={style} value={val}
      onChange={(e) => setVal(e.target.value)}
      onBlur={() => onCommit(monthlyToAnnual(val))} />
  )
}

// One license line. The row shows the common case (fully assigned); the ▸
// expander reveals the non-standard modifiers — shelfware (assigned below
// purchased), discount, price basis, persona — so they don't clutter the row.
function LicenseRow({ l, meta, personas, update, remove }) {
  const [open, setOpen] = useState(false)
  const fullyAssigned = l.quantity_assigned === l.quantity_purchased
  const personaName = personas.find((p) => p.id === l.persona_id)?.name
  const chips = []
  if (!fullyAssigned) chips.push(<span key="a" className="badge warn">{l.quantity_assigned}/{l.quantity_purchased} assigned</span>)
  if (l.discount_pct) chips.push(<span key="d" className="badge muted">−{pct(l.discount_pct)}</span>)
  if (l.price_basis && l.price_basis !== 'Unknown') chips.push(<span key="b" className="badge muted">{l.price_basis}</span>)
  if (personaName) chips.push(<span key="p" className="badge muted">{personaName}</span>)

  // Editing quantity keeps a fully-assigned line fully assigned; once shelfware
  // is set (assigned ≠ purchased), the two move independently.
  const setQty = (v) => {
    const q = Number(v)
    const patch = { quantity_purchased: q }
    if (fullyAssigned) patch.quantity_assigned = q
    update(l.id, patch)
  }

  return (
    <>
      <tr>
        <td><button className="ghost sm" title="Adjustments" onClick={() => setOpen(!open)}>{open ? '▾' : '▸'}</button></td>
        <td><SkuCombobox value={l.sku_reference}
          onChange={(v) => update(l.id, { sku_reference: v })}
          onSelectSku={(sku) => sku && update(l.id, { unit_price_paid_annual: sku.annual_unit_price, source_tag: 'ListPrice' })} /></td>
        <td className="num"><input type="number" style={{ width: 80 }} value={l.quantity_purchased}
          onChange={(e) => setQty(e.target.value)} /></td>
        <td className="num"><MonthlyPriceInput annual={l.unit_price_paid_annual} style={{ width: 100 }}
          onCommit={(annual) => update(l.id, { unit_price_paid_annual: annual })} /></td>
        <td><div className="pill-list">
          {chips.length ? chips : <span className="muted" style={{ fontSize: '.75rem' }}>fully assigned</span>}
        </div></td>
        <td className="num"><button className="danger sm" onClick={() => remove(l.id)}>Remove</button></td>
      </tr>
      {open && (
        <tr>
          <td></td>
          <td colSpan={5} style={{ background: 'var(--panel2)' }}>
            <div className="grid c4" style={{ padding: '.4rem 0' }}>
              <div><label>Assigned (deployed)</label>
                <input type="number" value={l.quantity_assigned}
                  onChange={(e) => update(l.id, { quantity_assigned: Number(e.target.value) })} />
                <small className="src">Below purchased = shelfware.</small></div>
              <div><label>Discount</label>
                <input type="number" step="0.05" value={l.discount_pct ?? ''} placeholder="e.g. 0.15"
                  onChange={(e) => update(l.id, { discount_pct: e.target.value === '' ? null : Number(e.target.value) })} />
                <small className="src">Fraction off list (0.15 = 15%). Recorded on the readout.</small></div>
              <div><label>Price basis</label>
                <select value={l.price_basis} onChange={(e) => update(l.id, { price_basis: e.target.value })}>
                  {(meta?.price_basis || []).map((s) => <option key={s}>{s}</option>)}
                </select></div>
              <div><label>Persona</label>
                <select value={l.persona_id || ''} onChange={(e) => update(l.id, { persona_id: e.target.value || null })}>
                  <option value="">—</option>
                  {personas.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select></div>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

export default function CurrentLicensing({ engagement, meta }) {
  const base = `/api/engagements/${engagement.id}/current-licenses`
  const [items, setItems] = useState([])
  const [personas, setPersonas] = useState([])
  const [err, setErr] = useState('')
  const blank = {
    sku_reference: '', quantity: 0,
    unit_price_paid_annual: 0, price_basis: 'Unknown', persona_id: '', source_tag: 'CustomerStated',
  }
  const [form, setForm] = useState(blank)
  // AI paste-to-parse state.
  const [aiEnabled, setAiEnabled] = useState(false)
  const [rawText, setRawText] = useState('')
  const [parsing, setParsing] = useState(false)
  const [parsed, setParsed] = useState(null)

  const load = () => {
    api.get(base).then(setItems).catch((e) => setErr(e.message))
    api.get(`/api/engagements/${engagement.id}/personas`).then(setPersonas)
  }
  useEffect(() => {
    load()
    api.get('/api/admin/ai/status').then((s) => setAiEnabled(s.enabled)).catch(() => {})
  }, [engagement.id])

  // Normalize a parsed row's stated price/period/scope to annual per-seat.
  const annualPerSeat = (r) => {
    const factor = r.price_period === 'Monthly' ? 12 : r.price_period === 'Quarterly' ? 4 : 1
    let annual = (Number(r.price) || 0) * factor
    const qty = Number(r.license_quantity) || 0
    if (r.price_scope === 'Total' && qty > 0) annual = annual / qty
    return Math.round(annual * 10000) / 10000
  }

  async function parseText() {
    if (!rawText.trim()) return
    setParsing(true); setErr('')
    try {
      const res = await api.post(`/api/admin/engagements/${engagement.id}/ai/parse-current-licenses`, { raw_text: rawText })
      setParsed((res.rows || []).map((r) => ({ ...r, _include: true })))
    } catch (e) { setErr(e.message) } finally { setParsing(false) }
  }
  const setParsedField = (i, patch) =>
    setParsed((rows) => rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  async function addParsed() {
    const rows = (parsed || []).filter((r) => r._include && (r.product_description || '').trim())
    setErr('')
    try {
      for (const r of rows) {
        const qty = Number(r.license_quantity) || 0
        await api.post(base, {
          sku_reference: r.product_description, quantity_purchased: qty, quantity_assigned: qty,
          unit_price_paid_annual: annualPerSeat(r), price_basis: 'Unknown',
          persona_id: null, source_tag: 'CustomerStated',
        })
      }
      setParsed(null); setRawText(''); load()
    } catch (e) { setErr(e.message) }
  }

  async function add() {
    if (!form.sku_reference.trim()) return
    try {
      const qty = Number(form.quantity) || 0
      await api.post(base, {
        sku_reference: form.sku_reference,
        // Default fully assigned; expand a line to model shelfware.
        quantity_purchased: qty, quantity_assigned: qty,
        unit_price_paid_annual: Number(form.unit_price_paid_annual),
        price_basis: form.price_basis, persona_id: form.persona_id || null,
        source_tag: form.source_tag,
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
      <h2>Current Microsoft licensing</h2>
      <p className="hint">Model on <b>assigned</b>, not purchased — shelfware is a savings
        source. Enter the actual price paid per seat (absolute, EA, CSP, or negotiated);
        don't assume ERP.</p>
      {err && <div className="err">{err}</div>}

      {aiEnabled && (
        <div className="card" style={{ background: 'var(--panel2)', marginBottom: '.8rem' }}>
          <div className="flex-between">
            <b>Paste from customer (AI)</b>
            <small className="src">Prices normalize to monthly $/seat; review before adding.</small>
          </div>
          <textarea rows={4} value={rawText}
            placeholder={'Paste a license statement or renewal quote, e.g.\nMicrosoft 365 E3\t250\t$32.00 / user / mo\nMicrosoft 365 E5\t60\t$57.00 / user / mo'}
            style={{ width: '100%', marginTop: '.4rem', fontFamily: 'inherit' }}
            onChange={(e) => setRawText(e.target.value)} />
          <button className="sm" disabled={parsing || !rawText.trim()} onClick={parseText}>
            {parsing ? 'Formatting…' : '✨ Format with AI'}
          </button>

          {parsed && (
            <div style={{ marginTop: '.6rem' }}>
              {parsed.length === 0 && <p className="muted">No license lines found in that text.</p>}
              {parsed.length > 0 && (
                <>
                  <table>
                    <thead><tr>
                      <th>Add</th><th>Product</th><th className="num">Qty</th><th className="num">Price</th>
                      <th>Period</th><th>Scope</th><th className="num">→ $/seat/mo</th>
                    </tr></thead>
                    <tbody>
                      {parsed.map((r, i) => (
                        <tr key={i} style={r._include ? {} : { opacity: 0.45 }}>
                          <td><input type="checkbox" style={{ width: 'auto' }} checked={r._include}
                            onChange={(e) => setParsedField(i, { _include: e.target.checked })} /></td>
                          <td><input value={r.product_description} style={{ minWidth: 150 }}
                            onChange={(e) => setParsedField(i, { product_description: e.target.value })} /></td>
                          <td className="num"><input type="number" style={{ width: 70 }} value={r.license_quantity}
                            onChange={(e) => setParsedField(i, { license_quantity: e.target.value })} /></td>
                          <td className="num"><input type="number" style={{ width: 90 }} value={r.price}
                            onChange={(e) => setParsedField(i, { price: e.target.value })} /></td>
                          <td>
                            <select value={r.price_period} onChange={(e) => setParsedField(i, { price_period: e.target.value })}>
                              {['Monthly', 'Quarterly', 'Annual'].map((s) => <option key={s}>{s}</option>)}
                            </select>
                          </td>
                          <td>
                            <select value={r.price_scope} onChange={(e) => setParsedField(i, { price_scope: e.target.value })}>
                              <option value="PerSeat">Per seat</option>
                              <option value="Total">Total</option>
                            </select>
                          </td>
                          <td className="num">{usd(annualPerSeat(r) / 12)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="toolbar" style={{ marginTop: '.5rem' }}>
                    <button className="sm" onClick={addParsed}>
                      Add {parsed.filter((r) => r._include && (r.product_description || '').trim()).length} selected
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
          <th></th><th>SKU</th><th className="num">Qty</th>
          <th className="num">Monthly $/seat</th><th>Adjustments</th><th></th>
        </tr></thead>
        <tbody>
          {items.map((l) => (
            <LicenseRow key={l.id} l={l} meta={meta} personas={personas} update={update} remove={remove} />
          ))}
        </tbody>
      </table>

      <div className="grid c4" style={{ marginTop: '.8rem' }}>
        <div><label>SKU reference</label>
          <SkuCombobox value={form.sku_reference} placeholder="Microsoft 365 E3"
            onChange={(v) => setForm((f) => ({ ...f, sku_reference: v }))}
            onSelectSku={(sku) => sku && setForm((f) => ({
              ...f, unit_price_paid_annual: sku.annual_unit_price, source_tag: 'ListPrice',
            }))} /></div>
        <div><label>Quantity</label>
          <input type="number" value={form.quantity}
            onChange={(e) => setForm({ ...form, quantity: e.target.value })} />
          <small className="src">Assumed fully assigned; expand a line for shelfware.</small></div>
        <div><label>Monthly $/seat paid</label>
          <MonthlyPriceInput annual={form.unit_price_paid_annual}
            onCommit={(annual) => setForm((f) => ({ ...f, unit_price_paid_annual: annual }))} /></div>
        <div style={{ display: 'flex', alignItems: 'flex-end' }}>
          <button onClick={add}>Add license line</button></div>
      </div>
    </div>
  )
}
