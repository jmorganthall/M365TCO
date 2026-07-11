import React, { useEffect, useState } from 'react'
import { api } from './api'
import Sidebar from './components/Sidebar.jsx'
import PricingBanner from './components/PricingBanner.jsx'
import UpdateBanner from './components/UpdateBanner.jsx'
import NewEngagement from './components/NewEngagement.jsx'
import CustomerInfo from './components/CustomerInfo.jsx'
import Personas from './components/Personas.jsx'
import CurrentLicensing from './components/CurrentLicensing.jsx'
import ThirdParty from './components/ThirdParty.jsx'
import CoverageMap from './components/CoverageMap.jsx'
import Scenarios from './components/Scenarios.jsx'
import CoverageCheck from './components/CoverageCheck.jsx'
import Readout from './components/Readout.jsx'
import DataInspector from './components/DataInspector.jsx'
import AdminPanel from './components/AdminPanel.jsx'

const STEPS = [
  ['info', 'Customer Info'],
  ['personas', 'Personas'],
  ['licensing', 'Current Licensing'],
  ['thirdparty', 'Third-Party'],
  ['coverage', 'Coverage Map'],
  ['scenarios', 'Scenarios'],
  ['gaps', 'Coverage Check'],
  ['readout', 'Readout'],
  ['data', 'Data'],
]

export default function App() {
  const [engagements, setEngagements] = useState([])
  const [active, setActive] = useState(null)
  const [tab, setTab] = useState('personas')
  const [meta, setMeta] = useState(null)
  const [view, setView] = useState('app')  // 'app' | 'settings'
  const openSettings = () => setView('settings')
  const closeSettings = () => { setView('app'); api.get('/api/meta').then(setMeta).catch(() => {}) }

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
    <div className="app-root">
      <header className="topbar">
        <div className="topbar-brand">Microsoft 365 TCO</div>
        <button className={`gear ${view === 'settings' ? 'active' : ''}`} title="Settings"
          onClick={() => setView(view === 'settings' ? 'app' : 'settings')}>⚙</button>
      </header>

      {view === 'settings' ? (
        <main className="main"><AdminPanel onClose={closeSettings} /></main>
      ) : (
      <div className="app-shell">
      <Sidebar
        engagements={engagements}
        activeId={active?.id}
        onNew={() => setActive(null)}
        onSelect={open}
        onDuplicate={duplicate}
        onDelete={remove}
        onSettings={openSettings}
      />

      <main className="main">
        {!active && (
          <div className="container">
            <UpdateBanner />
            <PricingBanner onOpenSettings={openSettings} />
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
            <UpdateBanner />
            <PricingBanner onOpenSettings={openSettings} />
            <div className="work-header">
              <div>
                <h2 style={{ margin: 0 }}>{active.customer_name || 'Untitled engagement'}</h2>
                <span className="muted">
                  {active.market}/{active.currency} · annualized USD ·
                  tooling split {Math.round(active.global_tooling_pct * 100)}%
                </span>
              </div>
            </div>

            <div className="stepper">
              {STEPS.map(([k, label], i) => {
                const activeIdx = STEPS.findIndex(([sk]) => sk === tab)
                const state = i === activeIdx ? 'current' : i < activeIdx ? 'done' : 'upcoming'
                return (
                  <button key={k} className={`step ${state}`} onClick={() => setTab(k)}>
                    <span className="step-dot">{state === 'done' ? '✓' : ''}</span>{label}
                  </button>
                )
              })}
            </div>

            {tab === 'info' && <CustomerInfo engagement={active}
              onUpdate={(u) => { setActive(u); reload() }} />}
            {tab === 'personas' && <Personas engagement={active} meta={meta} />}
            {tab === 'licensing' && <CurrentLicensing engagement={active} meta={meta} />}
            {tab === 'thirdparty' && <ThirdParty engagement={active} meta={meta} />}
            {tab === 'coverage' && <CoverageMap engagement={active} meta={meta} />}
            {tab === 'scenarios' && <Scenarios engagement={active} meta={meta} />}
            {tab === 'gaps' && <CoverageCheck engagement={active} onNavigate={setTab} />}
            {tab === 'readout' && <Readout engagement={active} />}
            {tab === 'data' && <DataInspector engagement={active} meta={meta} />}
          </div>
        )}
      </main>
      </div>
      )}
    </div>
  )
}
