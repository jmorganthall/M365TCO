import React, { useEffect, useState } from 'react'
import { api } from './api'
import EngagementList from './components/EngagementList.jsx'
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
  const [engagement, setEngagement] = useState(null)
  const [tab, setTab] = useState('personas')
  const [meta, setMeta] = useState(null)
  const [showAdmin, setShowAdmin] = useState(false)

  useEffect(() => { api.get('/api/meta').then(setMeta).catch(() => {}) }, [])

  function openEngagement(e) {
    setEngagement(e)
    setTab('personas')
  }

  return (
    <>
      <header className="app">
        <div className="brand">
          <h1>M365 TCO Tool <small>Microsoft Practice</small></h1>
        </div>
        <div className="row" style={{ gap: '.5rem' }}>
          {engagement && (
            <button className="ghost sm" onClick={() => setEngagement(null)}>
              ← Engagements
            </button>
          )}
          <button className="ghost sm" onClick={() => setShowAdmin(true)}>Settings</button>
        </div>
      </header>

      <div className="container">
        {!engagement && <EngagementList onOpen={openEngagement} />}

        {engagement && (
          <>
            <div className="flex-between">
              <div>
                <h2 style={{ margin: 0 }}>{engagement.customer_name || 'Untitled engagement'}</h2>
                <span className="muted">
                  {engagement.market}/{engagement.currency} · annualized USD ·
                  tooling split default {Math.round(engagement.global_tooling_pct * 100)}%
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

            {tab === 'personas' && <Personas engagement={engagement} meta={meta} />}
            {tab === 'licensing' && <CurrentLicensing engagement={engagement} meta={meta} />}
            {tab === 'thirdparty' && <ThirdParty engagement={engagement} meta={meta} />}
            {tab === 'coverage' && <CoverageMap engagement={engagement} meta={meta} />}
            {tab === 'scenarios' && <Scenarios engagement={engagement} meta={meta} />}
            {tab === 'readout' && <Readout engagement={engagement} />}
          </>
        )}
      </div>

      {showAdmin && <AdminPanel engagement={engagement} onClose={() => setShowAdmin(false)} />}
    </>
  )
}
