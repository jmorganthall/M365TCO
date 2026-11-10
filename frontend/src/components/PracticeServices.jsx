import React, { useEffect, useState } from 'react'
import { api, money, usd, useDataVersion, useInterpreted, useResult } from '../api'

// The practice's own services in each group's future state (docs/GUIDED_PLAN.md
// G13): add one to a group's plan and its outcomes count in what the move
// replaces; its price is part of the future cost. The services themselves are
// entered in Settings → Practice services (names are runtime data, never seeded).
export function useServices() {
  const version = useDataVersion()
  const [rows, setRows] = useState([])
  useEffect(() => { api.get('/api/admin/practice-services').then(setRows).catch(() => {}) }, [version])
  return rows
}

export const KIND_LABEL = { ManagedSecurity: 'Managed security', Calling: 'Calling', Other: 'Other services' }

export function ServicePicker({ engagement, kind, moneyUnit = 'mo' }) {
  const eid = engagement.id
  const version = useDataVersion()
  const services = useServices().filter((s) => !kind || s.kind === kind)
  const [personas, setPersonas] = useState([])
  const [scenarios, setScenarios] = useState([])
  const [quotes, setQuotes] = useState([])
  const [err, setErr] = useState('')
  const interp = useInterpreted(eid)
  // A service whose price in Settings changed after it was added here (G13): the
  // engagement keeps the price it was given until a person applies the new one.
  const changed = interp.findings.filter((f) => f.code === 'service_price_changed'
    && (!kind || services.some((s) => s.id === f.facts.service_id)))
  async function applyPrice(sid) {
    setErr('')
    try { await api.post(`/api/engagements/${eid}/practice-services/${sid}/apply-price`) } catch (e) { setErr(e.message) }
  }
  useEffect(() => {
    api.get(`/api/engagements/${eid}/personas`).then(setPersonas).catch(() => {})
    api.get(`/api/engagements/${eid}/scenarios`).then(setScenarios).catch(() => {})
    api.get(`/api/engagements/${eid}/service-quotes`).then(setQuotes).catch(() => {})
  }, [eid, version])
  async function save(fn) {
    setErr('')
    try { await fn() } catch (e) { setErr(e.message) }
  }

  async function toggle(sc, sid) {
    setErr('')
    const ids = new Set(sc.service_ids || [])
    ids.has(sid) ? ids.delete(sid) : ids.add(sid)
    try { await api.put(`/api/engagements/${eid}/scenarios/${sc.id}/services`, { service_ids: [...ids] }) }
    catch (e) { setErr(e.message) }
  }

  if (services.length === 0) {
    return <p className="muted">No {kind ? KIND_LABEL[kind].toLowerCase() : ''} services are set up yet. An admin adds
      them in Settings → Practice services, with their price per user and what they deliver.</p>
  }
  return (
    <div>
      {err && <div className="err">{err}</div>}
      {changed.map((f) => (
        <div key={f.facts.service_id} className="library-notice">
          <span>{f.facts.name}'s price changed to {usd(f.facts.current_monthly)} per user per month. This engagement
            uses {usd(f.facts.locked_monthly)}, the price when it was added.</span>
          <button className="ghost sm" onClick={() => applyPrice(f.facts.service_id)}>Apply the new price</button>
        </div>
      ))}
      <table className="guide-table">
        <thead><tr><th>Group</th>{services.map((s) => (
          <th key={s.id} title={s.description}>{s.name}<div className="muted" style={{ fontWeight: 400 }}>
            {usd(s.price_monthly)} per user / mo now</div></th>
        ))}</tr></thead>
        <tbody>
          {personas.map((p) => {
            const sc = scenarios.find((x) => x.persona_id === p.id)
            return (
              <tr key={p.id}><td>{p.name}<div className="muted">{p.headcount.toLocaleString()} people</div></td>
                {services.map((s) => (
                  <td key={s.id}>{sc
                    ? <label style={{ display: 'flex', gap: '.4rem', alignItems: 'center' }}>
                        <input type="checkbox" style={{ width: 'auto' }} checked={(sc.service_ids || []).includes(s.id)}
                          onChange={() => toggle(sc, s.id)} /> Include</label>
                    : <span className="muted">needs a plan first</span>}</td>
                ))}</tr>
            )
          })}
        </tbody>
      </table>
      {services.map((s) => (
        <QuoteRow key={s.id} eid={eid} service={s} quote={quotes.find((q) => q.service_id === s.id)}
          derived={(interp.data?.service_quotes || []).find((q) => q.service_id === s.id)} save={save} />
      ))}
    </div>
  )
}

// A service's quote for this customer (GUIDED_PLAN G13a): the cost and the users
// it covers, as the practice delivering it quoted them. The backend spreads the
// cost over the people in the groups ticked above; this only shows its figure.
function QuoteRow({ eid, service, quote, derived, save }) {
  const base = `/api/engagements/${eid}/service-quotes`
  const patch = (body) => save(() => api.patch(`${base}/${quote.id}`, body))
  if (!quote) {
    return (
      <div className="quote-row quote-empty">
        <span><b>{service.name}</b>: priced at {usd(service.price_monthly)} per user per month from Settings.</span>
        <button className="ghost sm" onClick={() => save(() => api.post(base, { service_id: service.id }))}>
          Enter this customer's quote</button>
      </div>
    )
  }
  return (
    <div className="quote-row">
      <div><b>{service.name}</b>: quote for this customer</div>
      <div className="quote-fields">
        <div><label htmlFor={`qc-${quote.id}`}>Quoted cost</label>
          <QuoteField id={`qc-${quote.id}`} value={Number(quote.total_cost)} placeholder="e.g. 12000"
            onSave={(v) => patch({ total_cost: Number(v) || 0 })} /></div>
        <div><label htmlFor={`qp-${quote.id}`}>Per</label>
          <select id={`qp-${quote.id}`} value={quote.cost_period} onChange={(e) => patch({ cost_period: e.target.value })}>
            <option value="Monthly">Month</option><option value="Annual">Year</option></select></div>
        <div><label htmlFor={`qu-${quote.id}`}>Users covered</label>
          <QuoteField id={`qu-${quote.id}`} value={quote.users_covered ?? ''} placeholder="e.g. 1,000"
            onSave={(v) => patch({ users_covered: v === '' ? null : Math.max(0, Math.round(Number(v)) || 0) })} /></div>
        <button className="ghost sm" style={{ alignSelf: 'flex-end' }}
          onClick={() => save(() => api.delete(`${base}/${quote.id}`))}>Use the Settings price instead</button>
      </div>
      {derived && (derived.people > 0
        ? <div className="muted">{money(derived.annual_cost, 'yr')} spread over the {derived.people.toLocaleString()} people
            in the groups that get it: {usd(derived.per_user_monthly)} per user per month (derived).</div>
        : <div className="err">No group gets {service.name} yet, so this quote is in no number. Tick it for the
            groups above.</div>)}
    </div>
  )
}

function QuoteField({ id, value, onSave, placeholder }) {
  const [v, setV] = useState(value ?? '')
  useEffect(() => { setV(value ?? '') }, [value])
  return (
    <input id={id} type="number" min="0" value={v} placeholder={placeholder} style={{ width: 140 }}
      onChange={(e) => setV(e.target.value)}
      onBlur={() => { if (String(v) !== String(value ?? '')) onSave(v) }}
      onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }} />
  )
}

// "Replacing your MDR" (G14, G13a): the managed tools a managed-security service
// replaces, what of each the move retires, and what the service costs after. Read
// from views.mdr; shown only when a service replaces a managed tool's managed part.
export function MdrPanel({ engagement, moneyUnit = 'yr' }) {
  const { result } = useResult(engagement.id)
  const outcomes = useOutcomeNames(engagement.id)
  const v = result?.views?.mdr
  if (!v) return null
  return (
    <div className="mdr-panel">
      <h3>Replacing your MDR</h3>
      <table className="guide-table">
        <thead><tr><th>Today</th><th className="num">Costs today</th><th className="num">Retired by the move</th></tr></thead>
        <tbody>
          {v.tools.map((t) => (
            <tr key={t.tool_id}><td>{t.name}
              <div className="muted">Delivers {t.outcome_ids.map((id) => outcomes[id]).filter(Boolean).join(', ') || 'nothing ticked yet'}</div></td>
              <td className="num">{money(t.today_annual, moneyUnit)}</td>
              <td className="num">{money(t.retired_annual, moneyUnit)}
                <div className="muted">software {money(t.software_retired, moneyUnit)}{t.software_by_microsoft ? ' (Microsoft 365)' : ''} ·
                  managed {money(t.managed_retired, moneyUnit)}</div></td></tr>
          ))}
        </tbody>
        <thead><tr><th>After: Microsoft 365 plus</th><th className="num">Per user / mo</th><th className="num">Costs after</th></tr></thead>
        <tbody>
          {v.services.map((x) => (
            <tr key={x.service_id}><td>{x.name}
              <div className="muted">{x.quoted
                ? `This customer's quote${x.users_covered != null ? `, for ${x.users_covered.toLocaleString()} users` : ''}; ${x.people.toLocaleString()} people get it`
                : `Settings price; ${x.people.toLocaleString()} people get it`}</div></td>
              <td className="num">{usd(x.per_user_monthly)}</td>
              <td className="num">{money(x.annual, moneyUnit)}</td></tr>
          ))}
        </tbody>
        <tfoot><tr><td>Change (the Microsoft 365 plans are in their own section)</td><td />
          <td className={`num ${v.change_annual < 0 ? 'pos' : ''}`}>{money(v.change_annual, moneyUnit)}</td></tr></tfoot>
      </table>
    </div>
  )
}

function useOutcomeNames(eid) {
  const version = useDataVersion()
  const [names, setNames] = useState({})
  useEffect(() => {
    api.get(`/api/engagements/${eid}/outcomes`).then((rows) => setNames(Object.fromEntries(rows.map((o) => [o.id, o.name])))).catch(() => {})
  }, [eid, version])
  return names
}

// The result in sections (G15), read from views.sections.
export function Sections({ engagement, moneyUnit = 'yr', only }) {
  const { result } = useResult(engagement.id)
  const v = result?.views?.sections
  if (!v) return null
  const rows = v.sections.filter((s) => !only || s.key === only)
  const label = (k) => (k === 'Microsoft' ? 'Microsoft 365' : KIND_LABEL[k])
  return (
    <table className="guide-table">
      <thead><tr><th>Section</th><th className="num">Cost after</th><th className="num">Retires</th>
        <th className="num">Change</th></tr></thead>
      <tbody>
        {rows.map((s) => (
          <tr key={s.key}><td>{label(s.key)}{s.services.length > 0 && <div className="muted">{s.services.join(', ')}</div>}</td>
            <td className="num">{money(s.cost_after, moneyUnit)}</td>
            <td className="num">{money(s.retired_total, moneyUnit)}
              {s.managed_share_retired > 0 && <div className="muted" title="The managed part of a managed tool's price, which only a managed service replaces">
                incl. {money(s.managed_share_retired, moneyUnit)} managed share</div>}</td>
            <td className={`num ${s.change_annual < 0 ? 'pos' : ''}`}>{money(s.change_annual, moneyUnit)}</td></tr>
        ))}
      </tbody>
      {!only && rows.length > 1 && <tfoot><tr><td>Combined</td><td /><td />
        <td className={`num ${v.combined_change_annual < 0 ? 'pos' : ''}`}>{money(v.combined_change_annual, moneyUnit)}</td></tr></tfoot>}
    </table>
  )
}

// Settings → Practice services: the practice's own services, entered here.
export function PracticeServicesAdmin({ onMsg, onErr }) {
  const [rows, setRows] = useState([])
  const [outcomes, setOutcomes] = useState([])
  const [form, setForm] = useState({ name: '', kind: 'ManagedSecurity', price_monthly: '' })
  const load = () => api.get('/api/admin/practice-services?include_archived=true').then(setRows).catch((e) => onErr(e.message))
  useEffect(() => { load(); api.get('/api/admin/default-outcomes').then(setOutcomes).catch(() => {}) }, [])
  const patch = async (id, body) => {
    try { await api.patch(`/api/admin/practice-services/${id}`, body); load() } catch (e) { onErr(e.message) }
  }
  async function add() {
    if (!form.name.trim()) return
    try {
      await api.post('/api/admin/practice-services', { ...form, price_monthly: Number(form.price_monthly) || 0 })
      setForm({ name: '', kind: form.kind, price_monthly: '' }); onMsg('Service added.'); load()
    } catch (e) { onErr(e.message) }
  }
  return (
    <div className="card">
      <h2>Practice services</h2>
      <p className="hint">The practice's own services, offered beside Microsoft 365 in a group's future state (for example
        a managed detection and response service, or a Teams calling service). Their price is per user per month. Tick
        what each one delivers: a tool is replaced only when the plan and its services deliver everything the tool does.
        Archiving hides a service from new choices and keeps it where it's already used.</p>
      <table>
        <thead><tr><th>Service</th><th>Kind</th><th className="num">$ / user / mo</th><th>Delivers</th><th></th></tr></thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id} style={s.archived ? { opacity: .55 } : undefined}>
              <td><input defaultValue={s.name} onBlur={(e) => e.target.value.trim() && e.target.value !== s.name && patch(s.id, { name: e.target.value })} />
                <input defaultValue={s.description} placeholder="Description" style={{ marginTop: 4 }}
                  onBlur={(e) => e.target.value !== s.description && patch(s.id, { description: e.target.value })} /></td>
              <td><select value={s.kind} onChange={(e) => patch(s.id, { kind: e.target.value })}>
                {Object.entries(KIND_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
              <td className="num"><input type="number" defaultValue={Number(s.price_monthly)} style={{ width: 90 }}
                onBlur={(e) => Number(e.target.value) !== Number(s.price_monthly) && patch(s.id, { price_monthly: Number(e.target.value) || 0 })} /></td>
              <td><select multiple value={s.outcome_keys} style={{ minWidth: 240, height: 90 }}
                onChange={(e) => patch(s.id, { outcome_keys: [...e.target.selectedOptions].map((o) => o.value) })}>
                {outcomes.map((o) => <option key={o.key} value={o.key}>{o.name}</option>)}</select></td>
              <td><button className="ghost sm" onClick={() => patch(s.id, { archived: !s.archived })}>
                {s.archived ? 'Restore' : 'Archive'}</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="toolbar" style={{ marginTop: '.6rem' }}>
        <div><label>New service</label><input value={form.name} placeholder="Service name"
          onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
        <div><label>Kind</label><select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
          {Object.entries(KIND_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        <div><label>$ / user / mo</label><input type="number" value={form.price_monthly}
          onChange={(e) => setForm({ ...form, price_monthly: e.target.value })} /></div>
        <button style={{ alignSelf: 'flex-end' }} onClick={add}>Add service</button>
      </div>
    </div>
  )
}
