import React, { useEffect, useState } from 'react'
import { api } from './api'
import Sidebar from './components/Sidebar.jsx'
import PricingBanner from './components/PricingBanner.jsx'
import NewEngagement from './components/NewEngagement.jsx'
import Personas from './components/Personas.jsx'
import CurrentLicensing from './components/CurrentLicensing.jsx'
import ThirdParty from './components/ThirdParty.jsx'
import CoverageMap from './components/CoverageMap.jsx'
import Scenarios from './components/Scenarios.jsx'
import Readout from './components/Readout.jsx'
import AdminPanel from './components/AdminPanel.jsx'

const STEPS = [
  ['personas', '1 · Personas'],
  ['licensing', '2 · Current Licensing'],
  ['thirdparty', '3 · Third-Party'],
  ['coverage', '4 · Coverage Map'],
  ['scenarios', '5 · Scenarios'],
  ['readout', '6 · Readout'],
]

export default function App() {
  const [engagements, setEngagements] = useState([])
  const [active, setActive] = useState(null)
  const [tab, setTab] = useState('personas')
  const [meta, setMeta] = useState(null)
  const [showAdmin, setShowAdmin] = useState(false)

  useEffect(() => { api.get('/api/meta').then(setMeta).catch(() => {}) }, [])

  function reload() {
    return api.get('/api/engagements').then(setEngagements).catch(() => {})
  }
  useEffect(() => { reload() }, [])

  function open(e) { setActive(e); setTab('personas') }

  async function duplicate(id) {
    const copy = await api.post(`/api/engagements/${id}/duplicate`)
    await reload()
    open(copy)
  }
  async function remove(id) {
    if (!confirm('Delete this engagement and all its data?')) return
    await api.del(`/api/engagements/${id}`)
    if (active?.id === id) setActive(null)
    reload()
  }
  async function created(e) {
    await reload()
    open(e)
  }

  return (
    <div className="app-shell">
      <Sidebar
        engagements={engagements}
        activeId={active?.id}
        onNew={() => setActive(null)}
        onSelect={open}
        onDuplicate={duplicate}
        onDelete={remove}
        onSettings={() => setShowAdmin(true)}
      />

      <main className="main">
        {!active && (
          <div className="container">
            <PricingBanner onOpenSettings={() => setShowAdmin(true)} />
            <div className="welcome">
              <h1>Model a Microsoft 365 total cost of ownership.</h1>
              <p className="muted">Create an engagement, then work through personas,
                current licensing, third-party spend, the coverage map, scenarios, and the
                readout. Pick an engagement from the left or start a new one.</p>
            </div>
            <NewEngagement onCreated={created} />
          </div>
        )}

        {active && (
          <div className="container">
            <PricingBanner onOpenSettings={() => setShowAdmin(true)} />
            <div className="work-header">
              <div>
                <h2 style={{ margin: 0 }}>{active.customer_name || 'Untitled engagement'}</h2>
                <span className="muted">
                  {active.market}/{active.currency} · annualized USD ·
                  tooling split {Math.round(active.global_tooling_pct * 100)}%
                </span>
              </div>
            </div>

            <div className="tabs">
              {STEPS.map(([k, label]) => (
                <button key={k} className={tab === k ? 'active' : ''} onClick={() => setTab(k)}>
                  {label}
                </button>
              ))}
            </div>

            {tab === 'personas' && <Personas engagement={active} meta={meta} />}
            {tab === 'licensing' && <CurrentLicensing engagement={active} meta={meta} />}
            {tab === 'thirdparty' && <ThirdParty engagement={active} meta={meta} />}
            {tab === 'coverage' && <CoverageMap engagement={active} meta={meta} />}
            {tab === 'scenarios' && <Scenarios engagement={active} meta={meta} />}
            {tab === 'readout' && <Readout engagement={active} />}
          </div>
        )}
      </main>

      {showAdmin && <AdminPanel onClose={() => { setShowAdmin(false); api.get('/api/meta').then(setMeta).catch(() => {}) }} />}
    </div>
  )
}
