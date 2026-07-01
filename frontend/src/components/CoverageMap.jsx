import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function CoverageMap({ engagement, meta }) {
  const eid = engagement.id
  const [outcomes, setOutcomes] = useState([])
  const [products, setProducts] = useState([])
  const [coverage, setCoverage] = useState([])
  const [aiEnabled, setAiEnabled] = useState(false)
  const [err, setErr] = useState('')
  const [newOutcome, setNewOutcome] = useState('')

  function load() {
    api.get(`/api/engagements/${eid}/outcomes`).then(setOutcomes)
    api.get(`/api/engagements/${eid}/third-party`).then(setProducts)
    api.get(`/api/engagements/${eid}/coverage`).then(setCoverage)
    api.get('/api/admin/ai/status').then((s) => setAiEnabled(s.enabled)).catch(() => {})
  }
  useEffect(() => { load() }, [eid])

  const outcomeName = (id) => outcomes.find((o) => o.id === id)?.name || id
  const tpEntries = (tpId) => coverage.filter((c) => c.product_kind === 'ThirdParty' && c.third_party_product_id === tpId)
  const skuRefs = [...new Set(coverage.filter((c) => c.product_kind === 'MicrosoftSku').map((c) => c.microsoft_sku_reference))]
  const skuEntries = (ref) => coverage.filter((c) => c.product_kind === 'MicrosoftSku' && c.microsoft_sku_reference === ref)

  async function addCustomOutcome() {
    if (!newOutcome.trim()) return
    try { await api.post(`/api/engagements/${eid}/outcomes`, { name: newOutcome, is_custom: true }); setNewOutcome(''); load() }
    catch (e) { setErr(e.message) }
  }
  async function addCoverage(tpId, outcomeId, cov) {
    try {
      await api.post(`/api/engagements/${eid}/coverage`, {
        outcome_id: outcomeId, product_kind: 'ThirdParty', third_party_product_id: tpId,
        coverage: cov, ai_suggested: false, ratified: true,
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
    setErr('')
    try {
      await api.post(`/api/admin/engagements/${eid}/ai/suggest-coverage`, { third_party_product_id: tpId })
      load()
    } catch (e) { setErr(e.message) }
  }

  return (
    <>
      <div className="card">
        <h2>Outcome coverage map</h2>
        <p className="hint">The engine's lookup for what a target SKU displaces. Microsoft
          SKU coverage is seeded and ratified. Capture third-party coverage per engagement.
          <b> Unratified AI suggestions never feed the math.</b></p>
        {err && <div className="err">{err}</div>}
        <div className="toolbar">
          <div style={{ flex: 2 }}>
            <label>Add custom outcome (mid-workshop)</label>
            <input value={newOutcome} placeholder="Unusual customer capability"
              onChange={(e) => setNewOutcome(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addCustomOutcome()} />
          </div>
          <button onClick={addCustomOutcome}>Add outcome</button>
        </div>
      </div>

      <div className="card">
        <h2>Third-party coverage</h2>
        <p className="hint">Map each product to the outcomes it delivers. A target SKU
          displaces a product only when it covers every outcome the product delivers.</p>
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
                  {outcomeName(c.outcome_id)} · {c.coverage}
                  {c.ai_suggested && !c.ratified && ' · AI'}
                  {!c.ratified && <button className="sm ghost" style={{ marginLeft: 6 }} onClick={() => ratify(c.id)}>ratify</button>}
                  <button className="sm danger" style={{ marginLeft: 4 }} onClick={() => removeCoverage(c.id)}>×</button>
                </span>
              ))}
              {tpEntries(tp.id).length === 0 && <span className="muted">No coverage captured.</span>}
            </div>
            <AddCoverageRow outcomes={outcomes} meta={meta}
              existing={tpEntries(tp.id).map((c) => c.outcome_id)}
              onAdd={(oid, cov) => addCoverage(tp.id, oid, cov)} />
          </div>
        ))}
      </div>

      <div className="card">
        <h2>Microsoft SKU coverage (seeded library)</h2>
        <p className="hint">Reference map used for the displacement test. Editable via the
          coverage API if the default library does not cover a SKU.</p>
        {skuRefs.map((ref) => (
          <div key={ref} style={{ marginBottom: '.5rem' }}>
            <b>{ref}</b>{' '}
            <span className="pill-list" style={{ display: 'inline-flex' }}>
              {skuEntries(ref).map((c) => (
                <span key={c.id} className="badge muted">{outcomeName(c.outcome_id)} · {c.coverage}</span>
              ))}
            </span>
          </div>
        ))}
      </div>
    </>
  )
}

function AddCoverageRow({ outcomes, existing, onAdd, meta }) {
  const available = outcomes.filter((o) => !existing.includes(o.id))
  const [oid, setOid] = useState('')
  const [cov, setCov] = useState('Full')
  return (
    <div className="toolbar">
      <div style={{ flex: 2 }}>
        <select value={oid} onChange={(e) => setOid(e.target.value)}>
          <option value="">+ add outcome…</option>
          {available.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
        </select>
      </div>
      <div>
        <select value={cov} onChange={(e) => setCov(e.target.value)}>
          {(meta?.coverage || ['Full', 'Partial']).map((c) => <option key={c}>{c}</option>)}
        </select>
      </div>
      <button className="sm" disabled={!oid} onClick={() => { onAdd(oid, cov); setOid('') }}>Add</button>
    </div>
  )
}
