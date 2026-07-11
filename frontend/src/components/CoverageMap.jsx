import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function CoverageMap({ engagement, meta }) {
  const eid = engagement.id
  const [outcomes, setOutcomes] = useState([])
  const [products, setProducts] = useState([])
  const [coverage, setCoverage] = useState([])
  const [bundles, setBundles] = useState([])
  const [aiEnabled, setAiEnabled] = useState(false)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [newOutcome, setNewOutcome] = useState('')

  function load() {
    api.get(`/api/engagements/${eid}/outcomes`).then(setOutcomes)
    api.get(`/api/engagements/${eid}/third-party`).then(setProducts)
    api.get(`/api/engagements/${eid}/coverage`).then(setCoverage)
    api.get('/api/catalog/bundles').then(setBundles).catch(() => {})
    api.get('/api/admin/ai/status').then((s) => setAiEnabled(s.enabled)).catch(() => {})
  }
  useEffect(() => { load() }, [eid])

  const outcomeName = (id) => outcomes.find((o) => o.id === id)?.name || id
  const tpEntries = (tpId) => coverage.filter((c) => c.product_kind === 'ThirdParty' && c.third_party_product_id === tpId)
  // Microsoft coverage keys onto a bundle: match by bundle_id when set, else by
  // the bundle name (the reference the seed writes). Entries that resolve to no
  // known bundle are surfaced separately so nothing is hidden.
  const bundleEntries = (b) => coverage.filter((c) => c.product_kind === 'MicrosoftSku'
    && (c.bundle_id ? c.bundle_id === b.id : c.microsoft_sku_reference === b.name))
  const bundleIds = new Set(bundles.map((b) => b.id))
  const bundleNames = new Set(bundles.map((b) => b.name))
  const customRefs = [...new Set(coverage
    .filter((c) => c.product_kind === 'MicrosoftSku'
      && !(c.bundle_id && bundleIds.has(c.bundle_id)) && !bundleNames.has(c.microsoft_sku_reference))
    .map((c) => c.microsoft_sku_reference))]
  const refEntries = (ref) => coverage.filter((c) => c.product_kind === 'MicrosoftSku' && c.microsoft_sku_reference === ref)

  async function addCustomOutcome() {
    if (!newOutcome.trim()) return
    try { await api.post(`/api/engagements/${eid}/outcomes`, { name: newOutcome, is_custom: true }); setNewOutcome(''); load() }
    catch (e) { setErr(e.message) }
  }
  async function addCoverage(tpId, outcomeId) {
    try {
      await api.post(`/api/engagements/${eid}/coverage`, {
        outcome_id: outcomeId, product_kind: 'ThirdParty', third_party_product_id: tpId,
        ai_suggested: false, ratified: true,  // coverage is binary (defaults to covered)
      })
      load()
    } catch (e) { setErr(e.message) }
  }
  async function addBundleCoverage(bundleName, outcomeId) {
    try {
      await api.post(`/api/engagements/${eid}/coverage`, {
        outcome_id: outcomeId, product_kind: 'MicrosoftSku',
        microsoft_sku_reference: bundleName, ai_suggested: false, ratified: true,
      })
      load()
    } catch (e) { setErr(e.message) }
  }
  async function ratify(id) {
    try { await api.post(`/api/engagements/${eid}/coverage/${id}/ratify`); load() } catch (e) { setErr(e.message) }
  }
  async function removeCoverage(id) {
    try { await api.del(`/api/engagements/${eid}/coverage/${id}`); load() } catch (e) { setErr(e.message) }
  }
  async function aiSuggest(tpId) {
    setErr(''); setMsg('')
    try {
      const res = await api.post(`/api/admin/engagements/${eid}/ai/suggest-coverage`, { third_party_product_id: tpId })
      load()
      if (!res.suggestions?.length) {
        const name = products.find((p) => p.id === tpId)?.name || 'Product'
        setMsg(`${name}: no correlation — the AI matched it to no outcomes.`)
      }
    } catch (e) { setErr(e.message) }
  }
  async function aiSuggestAll() {
    setErr(''); setMsg(''); setBulkBusy(true)
    try {
      const res = await api.post(`/api/admin/engagements/${eid}/ai/suggest-coverage-all`)
      load()
      const none = (res.results || []).filter((r) => r.created === 0).map((r) => r.name)
      const mapped = (res.results || []).filter((r) => r.created > 0).length
      let m = `Mapped ${mapped} product(s) · ${res.suggestions_created} suggestion(s).`
      if (none.length) m += ` No correlation for: ${none.join(', ')}.`
      if (res.skipped_mapped) m += ` ${res.skipped_mapped} already mapped, skipped.`
      setMsg(m)
      if (res.errors?.length) setErr(`Some products failed: ${res.errors.join('; ')}`)
    } catch (e) { setErr(e.message) } finally { setBulkBusy(false) }
  }

  return (
    <>
      {err && <div className="err">{err}</div>}

      <details className="card">
        <summary style={{ cursor: 'pointer' }}>
          <h2 style={{ display: 'inline', margin: 0 }}>Outcomes in this engagement ({outcomes.length})</h2>
          <span className="muted"> — the capability list coverage maps to (expand)</span>
        </summary>
        <p className="hint" style={{ marginTop: '.6rem' }}>Every capability this engagement can map coverage
          to. <b>Seeded</b> outcomes were copied from the global default library when the engagement was
          created (Settings → Default outcomes is the template for <b>new</b> engagements only); <b>custom</b>
          ones were added here. Editing an engagement's outcomes never changes any other engagement or the
          global library. Third-party and Microsoft coverage below can only reference outcomes on this list.</p>
        <div className="pill-list" style={{ margin: '.3rem 0' }}>
          {[...outcomes].sort((a, b) => a.name.localeCompare(b.name)).map((o) => (
            <span key={o.id} className={`badge ${o.is_custom ? 'warn' : 'muted'}`}
              title={o.description || ''}>
              {o.name}{o.is_custom ? ' · custom' : ''}
            </span>
          ))}
          {outcomes.length === 0 && <span className="muted">No outcomes yet.</span>}
        </div>
      </details>

      <div className="card">
        <div className="flex-between">
          <h2 style={{ margin: 0 }}>Third-party coverage</h2>
          {aiEnabled && products.length > 0 && (
            <button className="ghost sm" onClick={aiSuggestAll}
              disabled={bulkBusy || products.every((tp) => tpEntries(tp.id).length > 0)}>
              {bulkBusy
                ? 'Suggesting…'
                : `✨ AI suggest all (${products.filter((tp) => tpEntries(tp.id).length === 0).length} unmapped)`}
            </button>
          )}
        </div>
        <p className="hint">Map each product to the outcomes it delivers. A target SKU
          displaces a product only when it covers every outcome the product delivers.
          <b> "AI suggest all"</b> runs only on products with no coverage yet.
          <b> Unratified AI suggestions never feed the math.</b></p>
        {msg && <div className="popcheck" style={{ margin: '.4rem 0' }}>{msg}</div>}
        {products.length === 0 && <p className="muted">Add third-party products first.</p>}
        {products.map((tp) => (
          <div key={tp.id} className="card" style={{ background: 'var(--panel2)' }}>
            <div className="flex-between">
              <b>{tp.name}</b>
              {aiEnabled && <button className="ghost sm" onClick={() => aiSuggest(tp.id)}>✨ AI suggest coverage</button>}
            </div>
            <div className="pill-list" style={{ margin: '.5rem 0' }}>
              {tpEntries(tp.id).map((c) => (
                <span key={c.id} className={`badge ${c.ratified ? 'pos' : 'warn'}`}>
                  {outcomeName(c.outcome_id)}
                  {c.ai_suggested && !c.ratified && ' · AI'}
                  {!c.ratified && <button className="sm ghost" style={{ marginLeft: 6 }} onClick={() => ratify(c.id)}>ratify</button>}
                  <button className="sm danger" style={{ marginLeft: 4 }} onClick={() => removeCoverage(c.id)}>×</button>
                </span>
              ))}
              {tpEntries(tp.id).length === 0 && <span className="muted">No coverage captured.</span>}
            </div>
            <AddCoverageRow outcomes={outcomes}
              existing={tpEntries(tp.id).map((c) => c.outcome_id)}
              onAdd={(oid) => addCoverage(tp.id, oid)} />
          </div>
        ))}
      </div>

      <details className="card">
        <summary style={{ cursor: 'pointer' }}>
          <h2 style={{ display: 'inline', margin: 0 }}>Microsoft bundle coverage</h2>
          <span className="muted"> — the reference map (collapsed; expand to tune)</span>
        </summary>
        <p className="hint" style={{ marginTop: '.6rem' }}>What each staple bundle delivers — the
          reference map the displacement test reads (via the SKU → Bundle → Outcomes spine). Seeded
          per engagement and fully editable: add or remove an outcome to tune coverage for this
          customer.</p>
        {bundles.length === 0 && <p className="muted">Bundle library not loaded.</p>}
        {bundles.map((b) => (
          <div key={b.id} className="card" style={{ background: 'var(--panel2)' }}>
            <div className="flex-between">
              <b>{b.name}{b.kind === 'addon' && b.base_name ? <span className="muted"> · add-on to {b.base_name}</span> : ''}</b>
            </div>
            <div className="pill-list" style={{ margin: '.5rem 0' }}>
              {bundleEntries(b).map((c) => (
                <span key={c.id} className={`badge ${c.ratified ? 'pos' : 'warn'}`}>
                  {outcomeName(c.outcome_id)}
                  {c.ai_suggested && !c.ratified && ' · AI'}
                  {!c.ratified && <button className="sm ghost" style={{ marginLeft: 6 }} onClick={() => ratify(c.id)}>ratify</button>}
                  <button className="sm danger" style={{ marginLeft: 4 }} onClick={() => removeCoverage(c.id)}>×</button>
                </span>
              ))}
              {bundleEntries(b).length === 0 && <span className="muted">No coverage — this bundle displaces nothing.</span>}
            </div>
            <AddCoverageRow outcomes={outcomes}
              existing={bundleEntries(b).map((c) => c.outcome_id)}
              onAdd={(oid) => addBundleCoverage(b.name, oid)} />
          </div>
        ))}

        {customRefs.length > 0 && (
          <>
            <h3 style={{ fontSize: '.85rem', marginTop: '.8rem' }}>Unmapped Microsoft references</h3>
            <p className="hint">Coverage that resolves to no staple bundle. Map the SKU to a bundle in
              Settings → Staple bundles, or remove these entries.</p>
            {customRefs.map((ref) => (
              <div key={ref} style={{ marginBottom: '.4rem' }}>
                <b>{ref}</b>{' '}
                <span className="pill-list" style={{ display: 'inline-flex' }}>
                  {refEntries(ref).map((c) => (
                    <span key={c.id} className="badge muted">{outcomeName(c.outcome_id)}
                      <button className="sm danger" style={{ marginLeft: 4 }} onClick={() => removeCoverage(c.id)}>×</button>
                    </span>
                  ))}
                </span>
              </div>
            ))}
          </>
        )}
      </details>

      <div className="card">
        <h2>Add a custom outcome</h2>
        <p className="hint">An outcome is a capability that coverage maps to. This engagement
          already has a seeded set (see <b>Outcomes in this engagement</b> at the top) — you rarely
          need to add one. Use this only when a customer has a capability that isn't already on the
          list (for example, something that surfaces mid-workshop). A new outcome becomes immediately
          selectable in the third-party and Microsoft coverage sections above. It affects this
          engagement only — never other engagements or the global default library.</p>
        <div className="toolbar">
          <div style={{ flex: 2 }}>
            <label>New outcome name</label>
            <input value={newOutcome} placeholder="e.g. Privileged access management"
              onChange={(e) => setNewOutcome(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addCustomOutcome()} />
          </div>
          <button onClick={addCustomOutcome}>Add outcome</button>
        </div>
      </div>
    </>
  )
}

function AddCoverageRow({ outcomes, existing, onAdd }) {
  const available = outcomes.filter((o) => !existing.includes(o.id))
  const [oid, setOid] = useState('')
  return (
    <div className="toolbar">
      <div style={{ flex: 2 }}>
        <select value={oid} onChange={(e) => setOid(e.target.value)}>
          <option value="">+ add outcome…</option>
          {available.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
        </select>
      </div>
      <button className="sm" disabled={!oid} onClick={() => { onAdd(oid); setOid('') }}>Add</button>
    </div>
  )
}
