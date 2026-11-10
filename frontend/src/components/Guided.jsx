import React, { useEffect, useMemo, useState } from 'react'
import { api, money, useDataVersion, useInterpreted, useResult, usd } from '../api'
import { RecommendPlans } from './FutureState.jsx'
import ExtraSeats from './ExtraSeats.jsx'
import { Sections, ServicePicker } from './PracticeServices.jsx'
import { EngagementBasisEditor, effectiveBasis } from './basis.jsx'
import SkuCombobox from './SkuCombobox.jsx'

// The persona-guided walkthrough (docs/GUIDED_PLAN.md §4): one question per screen,
// over the same data as the detail steps. Every number shown comes from the
// backend (useInterpreted / useResult); this file formats, never calculates.
export const GUIDE_STEPS = [
  ['customer', 'Customer'],
  ['groups', 'Groups'],
  ['microsoft', 'Microsoft today'],
  ['outcomes', 'How you work today'],
  ['tools', 'Price your tools'],
  ['today', 'Today'],
  ['plan', 'Recommendation'],
  ['gaps', "What's new"],
  ['seats', 'Seats'],
  ['security', 'Managed security'],
  ['calling', 'Calling'],
  ['summary', 'Summary'],
]

// A list endpoint, refetched after every write (like useResult).
function useList(path) {
  const version = useDataVersion()
  const [rows, setRows] = useState(null)
  useEffect(() => {
    let live = true
    api.get(path).then((r) => { if (live) setRows(r) }).catch(() => {})
    return () => { live = false }
  }, [path, version])
  return rows
}

export default function Guided({ engagement, meta, step, sub, onGo, onUpdate, onDetail }) {
  const eid = engagement.id
  const base = `/api/engagements/${eid}`
  const interp = useInterpreted(eid)
  const { result } = useResult(eid)
  const personas = useList(`${base}/personas`) || []
  const outcomes = useList(`${base}/outcomes`) || []
  const tools = useList(`${base}/third-party`) || []
  const contracts = useList(`${base}/contracts`) || []
  // What the pricing step walks: each standalone tool, then each contract (its
  // lines are priced on the contract's screen).
  const priceables = [
    ...tools.filter((t) => !t.contract_id).map((t) => ({ kind: 'tool', tool: t })),
    ...contracts.map((c) => ({ kind: 'contract', contract: c })),
  ]
  const [err, setErr] = useState('')
  const areas = useMemo(() => areaList(outcomes), [outcomes])

  const i = Math.max(0, GUIDE_STEPS.findIndex(([k]) => k === step))
  const subCount = step === 'outcomes' ? areas.length : step === 'tools' ? priceables.length : 0
  const subIdx = Math.min(Number(sub) || 0, Math.max(subCount - 1, 0))
  function next() {
    setErr('')
    if (subCount && subIdx < subCount - 1) return onGo(step, subIdx + 1)
    const n = GUIDE_STEPS[i + 1]
    if (n) onGo(n[0], 0)
  }
  function back() {
    setErr('')
    if (subCount && subIdx > 0) return onGo(step, subIdx - 1)
    const p = GUIDE_STEPS[i - 1]
    if (!p) return
    const pc = p[0] === 'outcomes' ? areas.length : p[0] === 'tools' ? priceables.length : 0
    onGo(p[0], Math.max(pc - 1, 0))
  }
  async function call(fn) {
    setErr('')
    try { return await fn() } catch (e) { setErr(e.message) }
  }

  const ctx = { eid, base, engagement, meta, interp, result, personas, outcomes, tools, contracts, areas, call, onUpdate, onGo, onDetail }
  return (
    <div className="guide">
      <div className="guide-main">
        <div className="guide-progress">
          {GUIDE_STEPS.map(([k, label], n) => (
            <button key={k} className={`guide-dot ${n === i ? 'on' : n < i ? 'done' : ''}`} title={label}
              onClick={() => onGo(k, 0)}><span>{label}</span></button>
          ))}
        </div>
        <div className="guide-card">
          {step === 'customer' && <CustomerQ {...ctx} />}
          {step === 'groups' && <GroupsQ {...ctx} />}
          {step === 'microsoft' && <MicrosoftQ {...ctx} />}
          {step === 'outcomes' && <OutcomeWalk {...ctx} area={areas[subIdx]} n={subIdx} of={areas.length} />}
          {step === 'tools' && priceables[subIdx]?.kind === 'contract'
            ? <PriceContract {...ctx} contract={priceables[subIdx].contract} n={subIdx} of={priceables.length} />
            : step === 'tools' && <PriceTool {...ctx} tool={priceables[subIdx]?.tool} n={subIdx} of={priceables.length} />}
          {step === 'gaps' && <GapSweep {...ctx} />}
          {step === 'today' && <TodayQ {...ctx} />}
          {step === 'plan' && <PlanQ {...ctx} />}
          {step === 'seats' && <SeatsQ {...ctx} />}
          {step === 'security' && (
            <Ask title="Who should we watch over?"
              why="Our managed security service watches alerts around the clock. Add it to a group and it replaces the managed part of what you pay a provider today; Microsoft's security replaces the tooling.">
              <ServicePicker engagement={engagement} kind="ManagedSecurity" />
              <Sections engagement={engagement} only="ManagedSecurity" />
            </Ask>
          )}
          {step === 'calling' && (
            <Ask title="Who needs a phone number?"
              why="Teams Phone is the phone system; our calling service is the phone line. A phone system you pay for today goes away only when a group has both.">
              <ServicePicker engagement={engagement} kind="Calling" />
              <Sections engagement={engagement} only="Calling" />
            </Ask>
          )}
          {step === 'summary' && <SummaryQ {...ctx} />}
          {err && <div className="err" style={{ marginTop: '1rem' }}>{err}</div>}
          <div className="guide-nav">
            {i > 0 || subIdx > 0 ? <button className="ghost" onClick={back}>‹ Back</button> : <span />}
            {i < GUIDE_STEPS.length - 1 && <button onClick={next}>Continue ›</button>}
          </div>
        </div>
      </div>
      <Rail interp={interp} personas={personas} />
    </div>
  )
}

// Areas in library order; outcomes with no area last, as "Other".
function areaList(outcomes) {
  const seen = new Map()
  for (const o of outcomes) {
    const a = o.area || 'Other'
    if (!seen.has(a)) seen.set(a, [])
    seen.get(a).push(o)
  }
  const list = [...seen.entries()].map(([name, items]) => ({ name, items }))
  return [...list.filter((a) => a.name !== 'Other'), ...list.filter((a) => a.name === 'Other')]
}

// The side rail: each group, its people and its cost per user per month today.
function Rail({ interp, personas }) {
  const s = interp.data?.summary
  return (
    <aside className="guide-rail">
      <div className="guide-rail-title">Your groups</div>
      {personas.length === 0 && <div className="muted">None yet.</div>}
      {personas.map((p) => (
        <div key={p.id} className="guide-rail-row">
          <div><b>{p.name}</b><div className="muted">{p.headcount.toLocaleString()} people</div></div>
          <div className="num">{interp.baseline[p.id] ? usd(interp.baseline[p.id].per_user_monthly) : '…'}
            <div className="muted">per user / mo</div></div>
        </div>
      ))}
      {s && personas.length > 0 && (
        <div className="guide-rail-row total">
          <div><b>Everyone</b><div className="muted">{s.groups_headcount.toLocaleString()} people</div></div>
          <div className="num">{usd(s.baseline_per_user_monthly)}<div className="muted">{money(s.baseline_total_annual, 'yr')}</div></div>
        </div>
      )}
    </aside>
  )
}

function Ask({ title, why, children }) {
  return (
    <>
      <h1 className="guide-q">{title}</h1>
      {why && <p className="guide-why">{why}</p>}
      {children}
    </>
  )
}

// A field that saves on Enter or blur, never per keystroke.
function Field({ value, onSave, type = 'text', placeholder, big, style }) {
  const [v, setV] = useState(value ?? '')
  useEffect(() => { setV(value ?? '') }, [value])
  const save = () => { if (String(v) !== String(value ?? '')) onSave(v) }
  return (
    <input className={big ? 'guide-big' : ''} type={type} value={v} placeholder={placeholder} style={style}
      onChange={(e) => setV(e.target.value)} onBlur={save}
      onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }} />
  )
}

const num = (v) => (v === '' || v == null ? null : Math.max(0, Math.round(Number(v)) || 0))

function CustomerQ({ engagement, meta, base, call, onUpdate }) {
  const patch = (body) => call(async () => onUpdate(await api.patch(base, body)))
  const [err, setErr] = useState('')
  return (
    <Ask title="Who is the customer?">
      <Field big value={engagement.customer_name} placeholder="Customer name"
        onSave={(v) => patch({ customer_name: v })} />
      <label className="guide-label">Workshop date</label>
      <Field type="date" value={engagement.workshop_date || ''} onSave={(v) => patch({ workshop_date: v || null })} />
      {/* How they buy: the segment (commercial, nonprofit, …) and the default term and
          payment every plan is priced at. Options come from the loaded price list;
          "Default (…)" keeps the install setting from Settings (stored as not set). */}
      <label className="guide-label">How do they buy Microsoft 365?</label>
      <EngagementBasisEditor engagement={engagement} meta={meta} onUpdate={onUpdate} onError={setErr} />
      {err && <div className="err">{err}</div>}
    </Ask>
  )
}

// What a group needs whatever plan it's on (the persona's required outcomes): a full
// mailbox, full cloud storage, desktop Office. The recommender keeps them, so a
// group that needs desktop apps is never moved to a plan without them. Taken from
// the engagement's outcome library by key; a new group starts with all ticked.
const NEED_KEYS = ['mailbox-full', 'cloud-storage-full', 'desktop-software']

function Needs({ needs, selected, onToggle }) {
  if (!needs.length) return null
  return (
    <div className="needs" role="group" aria-label="Needs">
      {needs.map((o) => (
        <label key={o.id} className="check needs-item">
          <input type="checkbox" checked={selected.includes(o.id)} onChange={() => onToggle(o.id)} /> {o.name}
        </label>
      ))}
    </div>
  )
}

function GroupsQ({ engagement, base, personas, outcomes, interp, call }) {
  const [split, setSplit] = useState(personas.length > 1)
  const [name, setName] = useState('')
  const [size, setSize] = useState('')
  const needs = NEED_KEYS.map((k) => outcomes.find((o) => o.seed_key === k)).filter(Boolean)
  const [newNeeds, setNewNeeds] = useState(null)
  const fresh = newNeeds ?? needs.map((o) => o.id)
  const toggleNew = (id) => setNewNeeds(fresh.includes(id) ? fresh.filter((x) => x !== id) : [...fresh, id])
  const toggleFor = (p, id) => {
    const cur = p.required_outcome_ids || []
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]
    call(() => api.patch(`${base}/personas/${p.id}`, { required_outcome_ids: next }))
  }
  const s = interp.data?.summary
  const supported = engagement.supported_headcount
  const [oneGroup, setOneGroup] = useState(false)
  const [everyoneSize, setEveryoneSize] = useState('')
  async function everyone(size) {
    if (!size) return
    await call(() => api.post(`${base}/personas`, { name: 'Everyone', headcount: size, required_outcome_ids: fresh }))
  }
  async function add() {
    if (!name.trim()) return
    await call(() => api.post(`${base}/personas`, { name: name.trim(), headcount: num(size) || 0, required_outcome_ids: fresh }))
    setName(''); setSize(''); setNewNeeds(null)
  }
  if (personas.length === 0 && !split) {
    return (
      <Ask title="Does everyone get the same Microsoft licensing?"
        why="A group is a set of people who get the same Microsoft licences. Everything after this is answered group by group.">
        <div className="guide-choices">
          <button className="guide-choice" onClick={() => { setOneGroup(true); if (supported && !everyoneSize) setEveryoneSize(String(supported)) }}>Yes — one group
            <small>Everyone gets the same licences</small></button>
          <button className="guide-choice" onClick={() => setSplit(true)}>No — some get different licences
            <small>Name each group and its size</small></button>
        </div>
        {oneGroup && (
          <div className="guide-row" style={{ marginTop: '1rem' }}>
            <span>How many people are in it?</span>
            <input type="number" min="1" value={everyoneSize} style={{ width: 140 }} autoFocus
              onChange={(e) => setEveryoneSize(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && everyone(num(everyoneSize))} />
            <button disabled={!num(everyoneSize)} onClick={() => everyone(num(everyoneSize))}>Create the group</button>
          </div>
        )}
        {oneGroup && needs.length > 0 && (
          <div style={{ marginTop: 8 }}><span className="muted">They need:</span>
            <Needs needs={needs} selected={fresh} onToggle={toggleNew} /></div>
        )}
      </Ask>
    )
  }
  return (
    <Ask title="Who gets different licensing?"
      why="Name each group, how many people are in it, and what they need whatever plan they're on. Untick one only if that group really doesn't need it.">
      {personas.map((p) => (
        <div key={p.id} className="group-item">
          <div className="guide-row" style={{ margin: 0 }}>
            <Field value={p.name} onSave={(v) => v.trim() && call(() => api.patch(`${base}/personas/${p.id}`, { name: v.trim() }))} />
            <Field type="number" value={p.headcount} style={{ width: 120 }}
              onSave={(v) => call(() => api.patch(`${base}/personas/${p.id}`, { headcount: num(v) || 0 }))} />
            <span className="muted">people</span>
            <button className="ghost sm" aria-label={`Remove ${p.name}`} title="Remove this group"
              onClick={() => confirm(`Remove ${p.name}?`) && call(() => api.del(`${base}/personas/${p.id}`))}>✕</button>
          </div>
          <Needs needs={needs} selected={p.required_outcome_ids || []} onToggle={(id) => toggleFor(p, id)} />
        </div>
      ))}
      {/* UI3: visible labels; the placeholders are examples. MO4: a number pad for size. */}
      <div className="guide-row" style={{ alignItems: 'flex-end' }}>
        <div className="guide-field"><label htmlFor="new-group-name">New group</label>
          <input id="new-group-name" value={name} placeholder="e.g. Frontline" onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && add()} /></div>
        <div className="guide-field"><label htmlFor="new-group-size">People</label>
          <input id="new-group-size" type="number" inputMode="numeric" value={size} placeholder="e.g. 300" style={{ width: 120 }}
            onChange={(e) => setSize(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && add()} /></div>
        <button className="ghost" onClick={add}>Add group</button>
      </div>
      <Needs needs={needs} selected={fresh} onToggle={toggleNew} />
      {s && personas.length > 0 && (
        <p className="guide-count">These groups add up to <b>{s.groups_headcount.toLocaleString()}</b> people.</p>
      )}
    </Ask>
  )
}

function MicrosoftQ({ engagement, base, personas, interp, call }) {
  const lines = useList(`${base}/current-licenses`) || []
  // The same searchable picker as the detail view: a catalog pick sends the SKU and
  // the server sets the name and its list price; a typed name is saved as typed and
  // priced from the catalog when it can be.
  async function pick(p, line, sku, title) {
    const name = (title || '').trim()
    if (!sku && !name) return
    await call(async () => {
      const body = sku ? { catalog_sku_id: sku.id } : { sku_reference: name }
      const row = line
        ? await api.patch(`${base}/current-licenses/${line.id}`, body)
        : await api.post(`${base}/current-licenses`, {
          ...body, quantity_assigned: p.headcount, quantity_purchased: p.headcount, persona_ids: [p.id] })
      if (!sku) await api.post(`${base}/current-licenses/${row.id}/list-price`)
    })
  }
  const remove = (line) => call(() => api.del(`${base}/current-licenses/${line.id}`))
  // "Set seats to N": the group's size changed after the seats were entered. The
  // number comes from the finding; nothing changes without the click.
  const seatsFix = (line) => interp.findings.find((f) => f.code === 'seats_vs_headcount' && f.licence_id === line.id)
  return (
    <Ask title="What Microsoft licensing does each group have today?"
      why="Each group's main plan, plus any add-ons they all have. Prices are list; negotiated prices go in the detail view.">
      {personas.map((p) => {
        // A line applies to this group when it's tagged to it, or untagged (Org-wide:
        // every group). One shared with other groups is shown, never rewritten from
        // this row: changing it here would change every group on it.
        const applies = (l) => (l.persona_ids || []).length === 0 || l.persona_ids.includes(p.id)
        const mine = lines.filter(applies)
        const shared = mine.filter((l) => interp.licence[l.id]?.applies_to?.length > 1)
        const own = mine.filter((l) => !shared.includes(l))
        const others = (l) => personas.filter((x) => x.id !== p.id && interp.licence[l.id]?.applies_to?.includes(x.id)).map((x) => x.name)
        return (
          <div key={p.id} className="lic-group">
            {/* DB1: the group's Microsoft cost per user is the number that matters. */}
            <div className="lic-head">
              <div><div className="lic-name">{p.name}</div>
                <div className="muted">{p.headcount.toLocaleString()} people · every licence covers all of them</div></div>
              {interp.baseline[p.id] && (own.length > 0 || shared.length > 0) && (
                <div className="lic-total"><div className="lic-total-n">{usd(interp.baseline[p.id].microsoft_per_user_monthly)}</div>
                  <div className="muted">per user / mo</div></div>
              )}
            </div>
            {shared.map((l) => (
              <div key={l.id} className="lic-row">
                <div><span className="lic-tag">Shared</span> {l.sku_reference || 'A licence'}
                  <div className="muted lic-note">Also on {others(l).join(', ') || 'every group'}; change it in the detail view so they change together.</div></div>
                <div className="num muted">{interp.licence[l.id] ? money(interp.licence[l.id].effective_price_annual, 'mo') : ''}</div>
                <div />
              </div>
            ))}
            {own.map((l, n) => (
              <LicenceLine key={l.id} l={l} il={interp.licence[l.id]} first={n === 0 && shared.length === 0}
                base={base} call={call} remove={remove} seatsFix={seatsFix(l)} />
            ))}
            <AddLicence p={p} first={own.length === 0 && shared.length === 0} engagement={engagement} pick={pick} />
          </div>
        )
      })}
    </Ask>
  )
}

// One Microsoft licence on the guided step: what it is, what it costs per person,
// and a way to remove it. The plan is shown as text, not an open box; to change it,
// remove it and add the right one.
function LicenceLine({ l, il, first, base, call, remove, seatsFix }) {
  return (
    <>
      <div className="lic-row">
        <div><span className="lic-tag">{first ? 'Main plan' : 'Add-on'}</span> {l.sku_reference}</div>
        <div className="num">{il ? <>{money(il.effective_price_annual, 'mo')}<span className="muted"> per person</span></> : '…'}</div>
        <button className="ghost sm lic-x" aria-label={`Remove ${l.sku_reference}`} title="Remove" onClick={() => remove(l)}>✕</button>
      </div>
      {seatsFix && (
        <div className="err lic-note">{l.sku_reference} is entered for {seatsFix.facts.assigned.toLocaleString()} people, not the
          whole group ({seatsFix.facts.headcount.toLocaleString()}).{' '}
          <button className="ghost sm" onClick={() => call(() => api.patch(`${base}/current-licenses/${l.id}`, {
            quantity_assigned: seatsFix.facts.headcount, quantity_purchased: seatsFix.facts.headcount }))}>
            Make it the whole group</button> If only some of them have it, they are a group of their own.</div>
      )}
      {il && Number(il.effective_price_annual) === 0 && (
        <div className="err lic-note">No price found for {l.sku_reference}. Load the price list in Settings, or enter the price in the detail view.</div>
      )}
    </>
  )
}

// "+ Add a licence": a button until it's wanted, then the searchable plan picker.
// A group with nothing yet gets the picker straight away, for its main plan.
function AddLicence({ p, first, engagement, pick }) {
  const [open, setOpen] = useState(false)
  const b = effectiveBasis(null, engagement)
  if (!first && !open) {
    return <button className="ghost sm lic-add" onClick={() => setOpen(true)}>+ Add a licence</button>
  }
  return (
    <div className="lic-adding">
      <label>{first ? 'Main plan' : 'Add-on or product'}</label>
      <div className="guide-row" style={{ margin: 0 }}>
        <SkuCombobox key={`add-${p.id}`} value="" style={{ minWidth: 320 }}
          placeholder={first ? 'e.g. Office 365 E3 or Business Premium' : 'e.g. E5 Security, Teams Phone, Visio'}
          segment={b.segment} term={b.term} billing={b.billing}
          onChange={(v) => { pick(p, null, null, v); setOpen(false) }}
          onSelectSku={(sku, title) => { pick(p, null, sku, title); setOpen(false) }} />
        {!first && <button className="ghost sm" onClick={() => setOpen(false)}>Cancel</button>}
      </div>
    </div>
  )
}

function OutcomeWalk({ base, area, n, of, tools, interp, personas, call }) {
  const coverage = useList(`${base}/coverage`) || []
  if (!area) return <Ask title="No capabilities in this engagement's library yet." />
  const toolRows = coverage.filter((c) => c.product_kind === 'ThirdParty' && c.ratified)
  const names = Object.fromEntries(personas.map((p) => [p.id, p.name]))
  async function addTool(o, name) {
    if (!name.trim()) return
    await call(async () => {
      const t = await api.post(`${base}/third-party`, { name: name.trim(), raw_cost: 0 })
      await api.post(`${base}/coverage`, { outcome_id: o.id, product_kind: 'ThirdParty', third_party_product_id: t.id, ratified: true })
    })
  }
  return (
    <Ask title={`Let's talk about ${area.name.toLowerCase()}.`}
      why={`How do you deliver each of these today? Name the tool you pay for, or leave it if Microsoft covers it or you don't do it. (${n + 1} of ${of})`}>
      {area.items.map((o) => {
        const mine = toolRows.filter((c) => c.outcome_id === o.id)
        const ms = (interp.data?.today || []).filter((t) => t.outcome_id === o.id && t.by_microsoft).map((t) => names[t.persona_id]).filter(Boolean)
        const others = tools.filter((t) => !mine.some((c) => c.third_party_product_id === t.id))
        return <OutcomeRow key={o.id} o={o} mine={mine} ms={ms} others={others} tools={tools}
          onAdd={(tid) => call(() => api.post(`${base}/coverage`, { outcome_id: o.id, product_kind: 'ThirdParty', third_party_product_id: tid, ratified: true }))}
          onRemove={(cid) => call(() => api.del(`${base}/coverage/${cid}`))}
          onNew={(name) => addTool(o, name)} />
      })}
    </Ask>
  )
}

function OutcomeRow({ o, mine, ms, others, tools, onAdd, onRemove, onNew }) {
  const toolName = (id) => tools.find((t) => t.id === id)?.name || 'A tool'
  return (
    <div className="guide-block">
      <div className="guide-block-title">{o.name}</div>
      {o.description && <div className="muted guide-desc">{o.description}</div>}
      <div className="guide-chips">
        {ms.length > 0 && <span className="badge pos" title="From the Microsoft licensing you entered">Microsoft · {ms.join(', ')}</span>}
        {mine.map((c) => (
          <span key={c.id} className="badge">{toolName(c.third_party_product_id)}
            <button className="chip-x" title="Not used for this" onClick={() => onRemove(c.id)}>✕</button></span>
        ))}
        <ToolPicker others={others} onAdd={onAdd} onNew={onNew} />
      </div>
    </div>
  )
}

// One box to add a tool to a capability: the tools already named for other
// capabilities are in its dropdown (filtered as you type), and anything else typed
// is added as a new tool. Only the tools picked for this capability are shown above.
function ToolPicker({ others, onAdd, onNew }) {
  const [text, setText] = useState('')
  const [open, setOpen] = useState(false)
  const [sel, setSel] = useState(0)
  const q = text.trim().toLowerCase()
  const matches = others.filter((t) => !q || t.name.toLowerCase().includes(q))
  const exact = others.find((t) => t.name.trim().toLowerCase() === q)
  const options = [...matches.map((t) => ({ kind: 'tool', t })),
    ...(q && !exact ? [{ kind: 'new', name: text.trim() }] : [])]
  function choose(opt) {
    if (!opt) return
    if (opt.kind === 'tool') onAdd(opt.t.id)
    else onNew(opt.name)
    setText(''); setSel(0); setOpen(false)
  }
  return (
    <div className="combo" style={{ width: 260 }}>
      <input value={text} placeholder="+ Add a tool" style={{ width: '100%' }}
        onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
        onChange={(e) => { setText(e.target.value); setSel(0); setOpen(true) }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setSel((n) => Math.min(n + 1, options.length - 1)) }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setSel((n) => Math.max(n - 1, 0)) }
          else if (e.key === 'Enter') { e.preventDefault(); choose(exact ? { kind: 'tool', t: exact } : options[sel]) }
          else if (e.key === 'Escape') setOpen(false)
        }} />
      {open && options.length > 0 && (
        <div className="combo-list">
          {options.map((opt, n) => (
            <div key={opt.kind === 'tool' ? opt.t.id : 'new'} className={`combo-item ${n === sel ? 'sel' : ''}`}
              onMouseDown={(e) => { e.preventDefault(); choose(opt) }} onMouseEnter={() => setSel(n)}>
              {opt.kind === 'tool'
                ? <><div className="combo-id">{opt.t.name}</div><div className="combo-name">already named</div></>
                : <><div className="combo-id">Add “{opt.name}”</div><div className="combo-name">as a new tool</div></>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function PriceTool({ base, tool, n, of, interp, personas, outcomes, call, onGo }) {
  if (!tool) {
    return (
      <Ask title="No tools named yet."
        why="Tools are collected while walking through how you work today. Go back and name any tool you pay for, or continue.">
        <button className="ghost" onClick={() => onGo('outcomes', 0)}>‹ How you work today</button>
      </Ask>
    )
  }
  const it = interp.tool[tool.id]
  const patch = (body) => call(() => api.patch(`${base}/third-party/${tool.id}`, body))
  const all = (tool.persona_ids || []).length === 0
  const toggle = (pid) => {
    const ids = new Set(tool.persona_ids || [])
    ids.has(pid) ? ids.delete(pid) : ids.add(pid)
    patch({ persona_ids: [...ids] })
  }
  const uses = (it?.delivered_outcome_ids || []).map((id) => outcomes.find((o) => o.id === id)?.name).filter(Boolean)
  const missing = interp.findings.find((f) => f.code === 'tool_incomplete' && f.tool_id === tool.id)?.facts.missing
  return (
    <Ask title={`What do you pay for ${tool.name}?`} why={`Tool ${n + 1} of ${of}. Used for: ${uses.join(', ') || 'nothing yet'}.`}>
      <div className="guide-row">
        <Field big type="number" value={Number(tool.raw_cost) || ''} placeholder="Cost" style={{ width: 220 }}
          onSave={(v) => patch({ raw_cost: Number(v) || 0 })} />
        <select value={tool.cost_period} onChange={(e) => patch({ cost_period: e.target.value })}>
          <option value="Annual">per year</option>
          <option value="Monthly">per month</option>
        </select>
      </div>
      <label className="guide-label">Who has it?</label>
      <div className="guide-chips">
        <button className={all ? '' : 'ghost'} onClick={() => patch({ persona_ids: [] })}>All groups</button>
        {personas.map((p) => (
          <button key={p.id} className={!all && tool.persona_ids.includes(p.id) ? '' : 'ghost'}
            onClick={() => toggle(p.id)}>{p.name}</button>
        ))}
      </div>
      <label className="guide-label">How many seats does it cover?</label>
      <div className="guide-row">
        <Field type="number" value={tool.covered_count_override ?? ''} style={{ width: 160 }}
          placeholder={it ? String(it.people_held) : ''}
          onSave={(v) => patch({ covered_count_override: num(v) })} />
        <span className="muted">Blank = the people in those groups{it ? ` (${it.people_held.toLocaleString()})` : ''}.</span>
      </div>
      {it && it.extra_seats > 0 && <div className="src">That's {it.extra_seats.toLocaleString()} more seats than people in those groups. We'll ask about them if a move replaces {tool.name}.</div>}
      <div className="guide-row" style={{ marginTop: '1rem', gap: '1.5rem' }}>
        <div><label className="guide-label">When does it renew?</label>
          <Field type="date" value={tool.renewal_date || ''} onSave={(v) => patch({ renewal_date: v || null })} /></div>
        <label style={{ display: 'flex', alignItems: 'center', gap: '.4rem', marginTop: '1.4rem' }}>
          <input type="checkbox" style={{ width: 'auto' }} checked={tool.is_managed}
            onChange={(e) => patch({ is_managed: e.target.checked })} /> Bought as a managed service</label>
      </div>
      {it && <p className="muted">Counted: {money(it.effective_annual_cost, 'yr')} · {money(it.cost_per_person, 'mo')} per person</p>}
      {missing && <div className="src warn">Left out of the numbers until it has: {missing.join(', ')}.</div>}
      {uses.length > 1 && (
        <div className="guide-block" style={{ marginTop: '1rem' }}>
          <div className="guide-block-title">Can that be broken down?</div>
          <div className="muted guide-desc">{tool.name} does {uses.length} things. Microsoft may replace some of them and
            not others; a saving shows only for a part that has its own cost. Breaking it down keeps the total
            and lets you put a cost against each part.</div>
          <button className="ghost" onClick={() => call(() => api.post(`${base}/third-party/${tool.id}/break-down`))}>
            Break this down</button>
        </div>
      )}
    </Ask>
  )
}

function PriceContract({ base, contract, n, of, tools, interp, outcomes, call }) {
  const ic = (interp.data?.contracts || []).find((c) => c.id === contract.id)
  const lines = tools.filter((t) => t.contract_id === contract.id)
  const patch = (body) => call(() => api.patch(`${base}/contracts/${contract.id}`, body))
  const uses = (t) => (interp.tool[t.id]?.delivered_outcome_ids || []).map((id) => outcomes.find((o) => o.id === id)?.name).filter(Boolean).join(', ')
  return (
    <Ask title={`What do you pay for ${contract.name}, and for each part?`}
      why={`Contract ${n + 1} of ${of}. Put a cost against each part you can; whatever is left stays until everything it does is replaced.`}>
      <div className="guide-row">
        <Field big type="number" value={Number(contract.total_cost) || ''} placeholder="Total" style={{ width: 220 }}
          onSave={(v) => patch({ total_cost: Number(v) || 0 })} />
        <select value={contract.cost_period} onChange={(e) => patch({ cost_period: e.target.value })}>
          <option value="Annual">per year</option>
          <option value="Monthly">per month</option>
        </select>
        <span className="muted">in total</span>
      </div>
      <label className="guide-label">When does it renew?</label>
      <Field type="date" value={contract.renewal_date || ''} onSave={(v) => patch({ renewal_date: v || null })} />
      <table className="guide-table" style={{ marginTop: '1rem' }}>
        <thead><tr><th>Part</th><th>Does</th><th className="num">Cost ({contract.cost_period === 'Monthly' ? 'per month' : 'per year'})</th></tr></thead>
        <tbody>
          {lines.map((t) => (
            <tr key={t.id}>
              <td><Field value={t.name} onSave={(v) => v.trim() && call(() => api.patch(`${base}/third-party/${t.id}`, { name: v.trim() }))} /></td>
              <td className="muted">{uses(t)}</td>
              <td className="num"><Field type="number" value={Number(t.raw_cost) || ''} placeholder="0" style={{ width: 130 }}
                onSave={(v) => call(() => api.patch(`${base}/third-party/${t.id}`, { raw_cost: Number(v) || 0, cost_period: contract.cost_period }))} /></td>
            </tr>
          ))}
        </tbody>
        {ic && <tfoot><tr><td>Not broken down</td><td className="muted">everything above</td>
          <td className="num">{money(ic.remainder_annual, 'yr')}</td></tr></tfoot>}
      </table>
      {interp.findings.filter((f) => f.code === 'contract_lines_exceed' && f.facts.name === contract.name).map((f, k) => (
        <div key={k} className="src warn">The parts add up to more than the contract's total. Check the amounts.</div>
      ))}
    </Ask>
  )
}

// What each group's new plan adds that nothing delivers today (asked after the
// recommendation, GUIDED_PLAN G10): the only time the answer changes a number,
// because a capability counts as gained once it's confirmed missing today. One
// block per group; one click confirms them all; an exception is one link.
function GapSweep({ base, outcomes, personas, interp, result, call }) {
  const name = Object.fromEntries(outcomes.map((o) => [o.id, o.name]))
  const answers = Object.fromEntries((useList(`${base}/coverage-gap-answers`) || []).map((a) => [`${a.persona_id}|${a.outcome_id}`, a.answer]))
  const plan = Object.fromEntries((result?.views?.personas || []).map((v) => [v.persona_id, v.plan]))
  const groups = personas.filter((p) => p.headcount > 0).map((p) => ({
    p,
    items: (interp.data?.proposed || []).filter((r) => r.persona_id === p.id && r.in_scope && r.by_target
      && (!r.delivered_today || answers[`${p.id}|${r.outcome_id}`] === 'CoveredOutsideInventory'))
      .map((r) => ({ id: r.outcome_id, answer: answers[`${p.id}|${r.outcome_id}`] || null })),
  })).filter((g) => g.items.length > 0)
  const put = (p, ids, answer) => call(async () => {
    for (const id of ids) await api.put(`${base}/coverage-gap-answers`, { persona_id: p.id, outcome_id: id, answer })
  })
  return (
    <Ask title="Here's what's new for each group."
      why="What each group's new plan adds that nothing gives them today. Confirm it's new and it counts as gained; if they already get one some other way, say so.">
      {groups.length === 0 && <p className="pos">Nothing to confirm: no group's plan adds anything they don't have today.</p>}
      {groups.map(({ p, items }) => {
        const open = items.filter((i) => !i.answer).map((i) => i.id)
        return (
          <div key={p.id} className="guide-block">
            <div className="guide-block-title">{p.name}{plan[p.id] && <span className="muted"> · {plan[p.id]} adds</span>}</div>
            <div className="guide-chips">
              {items.map((i) => (
                <span key={i.id} className={`badge ${i.answer === 'NotDeliveredToday' ? 'pos' : ''}`}
                  style={i.answer === 'CoveredOutsideInventory' ? { textDecoration: 'line-through', opacity: .6 } : undefined}
                  title={i.answer === 'CoveredOutsideInventory' ? 'They already have this another way' : i.answer ? 'Confirmed new' : 'Not confirmed yet'}>
                  {name[i.id] || 'A capability'}
                  {i.answer === 'CoveredOutsideInventory'
                    ? <button className="chip-x" title="Actually new for them" onClick={() => put(p, [i.id], 'NotDeliveredToday')}>↺</button>
                    : <button className="chip-x" title="They already have this another way" onClick={() => put(p, [i.id], 'CoveredOutsideInventory')}>✕</button>}
                </span>
              ))}
            </div>
            <div className="guide-chips">
              {open.length > 0
                ? <button className="ghost sm" onClick={() => put(p, open, 'NotDeliveredToday')}>These are all new for {p.name}</button>
                : <span className="src pos">Confirmed.</span>}
              <span className="muted" style={{ fontSize: '.8rem' }}>✕ on one they already have another way.</span>
            </div>
          </div>
        )
      })}
    </Ask>
  )
}

function TodayQ({ personas, interp }) {
  const s = interp.data?.summary
  return (
    <Ask title="Here's what each person costs you today."
      why="Each group's share of its Microsoft licences and of every tool it has, per person per month.">
      <table className="guide-table">
        <thead><tr><th>Group</th><th className="num">People</th><th className="num">Microsoft</th>
          <th className="num">Other tools</th><th className="num">Per user / mo</th></tr></thead>
        <tbody>
          {personas.map((p) => {
            const b = interp.baseline[p.id]
            return (
              <tr key={p.id}><td>{p.name}</td><td className="num">{p.headcount.toLocaleString()}</td>
                <td className="num">{b ? usd(b.microsoft_per_user_monthly) : '…'}</td>
                <td className="num">{b ? usd(b.tools_per_user_monthly) : '…'}</td>
                <td className="num"><b>{b ? usd(b.per_user_monthly) : '…'}</b></td></tr>
            )
          })}
        </tbody>
        {s && <tfoot><tr><td>Everyone</td><td className="num">{s.groups_headcount.toLocaleString()}</td>
          <td className="num">{money(s.baseline_microsoft_annual, 'yr')}</td><td className="num">{money(s.baseline_tools_annual, 'yr')}</td>
          <td className="num"><b>{usd(s.baseline_per_user_monthly)}</b></td></tr></tfoot>}
      </table>
      {s && Number(s.standing_annual) > 0 && (
        <p className="muted">Plus {money(s.standing_annual, 'yr')} for tool seats held by people outside these groups.</p>
      )}
    </Ask>
  )
}

function PlanQ({ engagement, base, personas, result, onDetail }) {
  const view = Object.fromEntries((result?.views?.personas || []).map((v) => [v.persona_id, v]))
  const totals = result?.views?.persona_totals
  const years = totals?.horizon_years || engagement.modeling_horizon_years || 3
  // Review's checks for the future state (seat caps, plans that drop a capability),
  // worded by the backend; shown here so nothing waits until the end.
  const review = useList(`${base}/review`)
  const checks = (review?.checks || []).filter((c) => c.step === 'future' || c.code === 'current_over_cap')
  return (
    <Ask title="Here's the Microsoft plan we'd recommend for each group."
      why="The lowest-cost plan that keeps everything each group has today. A tool's share goes away only where the plan does everything that tool does.">
      <RecommendPlans engagement={engagement} />
      <table className="guide-table">
        <thead><tr><th>Group</th><th>Plan</th><th className="num">Today / user / mo</th>
          <th className="num">After / user / mo</th><th className="num">Change / user / mo</th>
          <th className="num">Per year</th><th className="num">Over {years} years</th></tr></thead>
        <tbody>
          {personas.map((p) => {
            const v = view[p.id]
            return (
              <tr key={p.id}><td>{p.name}</td><td>{v?.plan || <span className="muted">no plan yet</span>}</td>
                <td className="num">{v ? usd(v.today_per_user_monthly) : '…'}</td>
                <td className="num">{v ? usd(v.after_per_user_monthly) : '…'}</td>
                <td className={`num ${v && v.delta_per_user_monthly < 0 ? 'pos' : ''}`}>{v ? usd(v.delta_per_user_monthly) : '…'}</td>
                <td className={`num ${v && v.delta_annual < 0 ? 'pos' : ''}`}>{v ? usd(v.delta_annual) : '…'}</td>
                <td className={`num ${v && v.delta_horizon < 0 ? 'pos' : ''}`}>{v ? usd(v.delta_horizon) : '…'}</td></tr>
            )
          })}
        </tbody>
        {totals && <tfoot><tr><td>All groups</td><td /><td /><td /><td />
          <td className={`num ${totals.delta_annual < 0 ? 'pos' : ''}`}>{usd(totals.delta_annual)}</td>
          <td className={`num ${totals.delta_horizon < 0 ? 'pos' : ''}`}>{usd(totals.delta_horizon)}</td></tr></tfoot>}
      </table>
      <p className="muted" style={{ fontSize: '.8rem' }}>Negative is a saving. Over {years} years is at the full yearly rate; the
        summary's headline times each saving from the renewal that unlocks it.</p>
      {checks.map((c, n) => <div key={n} className={`src ${c.severity === 'info' ? 'muted' : 'warn'}`}>{c.message}</div>)}
      <button className="ghost" style={{ marginTop: '.8rem' }} onClick={() => onDetail('future')}>Change a plan in the detail view ›</button>
    </Ask>
  )
}

function SeatsQ({ engagement, interp }) {
  const asked = interp.findings.some((f) => f.code === 'extra_seats_unanswered')
    || (interp.data?.tools || []).some((t) => t.extra_seats > 0 && t.extra_seats_answer)
  return (
    <Ask title={asked ? "A few tools have more seats than people." : 'Every replaced tool lines up.'}
      why={asked ? "If the extra seats aren't needed, we'll show the whole cost going away. If they belong to people who stay on the tool, that part stays." : 'Nothing to ask here.'}>
      <ExtraSeats engagement={engagement} moneyUnit="yr" />
    </Ask>
  )
}

function SummaryQ({ engagement, interp, result, onDetail }) {
  const s = interp.data?.summary
  const r = result?.rollup
  const open = interp.findings.filter((f) => f.severity !== 'info').length
  return (
    <Ask title="The picture so far.">
      {s && r && (
        <div className="guide-stats">
          <div><div className="muted">Today, per year</div><div className="guide-stat">{usd(s.baseline_total_annual)}</div></div>
          <div><div className="muted">Change per year</div>
            <div className={`guide-stat ${r.net_tco_delta_annual < 0 ? 'pos' : ''}`}>{usd(r.net_tco_delta_annual)}</div></div>
        </div>
      )}
      <Sections engagement={engagement} />
      <p className="muted">{open ? `${open} item${open === 1 ? '' : 's'} still to check on Review.` : 'Nothing left to check.'}</p>
      <div className="guide-chips">
        <button className="ghost" onClick={() => onDetail('review')}>Review ›</button>
        <button onClick={() => onDetail('summary')}>Summary and PDF ›</button>
      </div>
    </Ask>
  )
}
