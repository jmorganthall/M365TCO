import React, { useEffect, useState } from 'react'
import { api, loadMoneyUnit, saveMoneyUnit } from './api'
import PricingBanner from './components/PricingBanner.jsx'
import UpdateBanner from './components/UpdateBanner.jsx'
import NewEngagement from './components/NewEngagement.jsx'
import OpenEngagement from './components/OpenEngagement.jsx'
import { AgreementCard, CustomerCard, SummaryCard } from './components/EngagementFields.jsx'
import Personas from './components/Personas.jsx'
import CurrentLicensing from './components/CurrentLicensing.jsx'
import ThirdParty from './components/ThirdParty.jsx'
import CoverageMap from './components/CoverageMap.jsx'
import FutureState from './components/FutureState.jsx'
import CoverageCheck from './components/CoverageCheck.jsx'
import Review from './components/Review.jsx'
import Readout from './components/Readout.jsx'
import DataInspector from './components/DataInspector.jsx'
import AdminPanel from './components/AdminPanel.jsx'
import { StepIntro } from './components/Help.jsx'

// The guided walkthrough (docs/WALKTHROUGH.md §3): seven steps an account
// executive runs with the customer, in order. "Data" is NOT a step; it's an
// engagement tool reached from the header Tools menu.
const STEPS = [
  ['customer', 'Customer'],
  ['groups', 'Groups & licences'],
  ['tools', 'Other tools'],
  ['future', 'Future state'],
  ['gaps', 'Coverage check'],
  ['review', 'Review'],
  ['summary', 'Summary & PDF'],
]
const TABS = new Set([...STEPS.map(([k]) => k), 'data'])
// Addresses saved before the walkthrough's steps existed still land somewhere sensible.
const OLD_TABS = { baseline: 'customer', thirdparty: 'tools', coverage: 'tools', scenarios: 'future', readout: 'summary' }

// Navigation lives in the URL hash, so a reload returns to the same engagement
// and step instead of a page that lists every customer:
//   #/                   Open engagement (search box + new engagement)
//   #/e/<id>/<step>      an engagement at a step
//   #/settings           Settings
function parseHash() {
  const parts = (window.location.hash || '').replace(/^#\/?/, '').split('/').filter(Boolean)
  if (parts[0] === 'settings') return { view: 'settings' }
  if (parts[0] === 'e' && parts[1]) {
    const tab = TABS.has(parts[2]) ? parts[2] : (OLD_TABS[parts[2]] || 'customer')
    return { view: 'engagement', id: decodeURIComponent(parts[1]), tab }
  }
  return { view: 'home' }
}
const go = (path) => { window.location.hash = path }
const engagementPath = (id, tab = 'customer') => `/e/${encodeURIComponent(id)}/${tab}`

export default function App() {
  const [route, setRoute] = useState(parseHash)
  const [active, setActive] = useState(null)
  const [loadErr, setLoadErr] = useState('')
  const [meta, setMeta] = useState(null)
  const [returnTo, setReturnTo] = useState('/')
  // Money display unit ($/mo default — humans gut-check monthly; data stays annualized).
  const [moneyUnit, setMoneyUnit] = useState(loadMoneyUnit())
  const switchMoneyUnit = (u) => { setMoneyUnit(u); saveMoneyUnit(u) }

  useEffect(() => {
    const onHash = () => setRoute(parseHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => { api.get('/api/meta').then(setMeta).catch(() => {}) }, [])

  // Load the routed engagement (only that one — never the list).
  const activeId = route.view === 'engagement' ? route.id : null
  useEffect(() => {
    setLoadErr('')
    if (!activeId) { setActive(null); return }
    if (active?.id === activeId) return
    setActive(null)
    api.get(`/api/engagements/${activeId}`).then(setActive)
      .catch(() => setLoadErr('That engagement no longer exists, or the link is wrong.'))
  }, [activeId])

  const tab = route.view === 'engagement' ? route.tab : 'customer'
  const setTab = (k) => go(engagementPath(activeId, k))

  function openSettings() {
    setReturnTo((window.location.hash || '#/').replace(/^#/, '') || '/')
    go('/settings')
  }
  function closeSettings() {
    go(returnTo)
    api.get('/api/meta').then(setMeta).catch(() => {})
  }

  function open(e) { setActive(null); go(engagementPath(e.id)) }
  async function duplicate(id) {
    const copy = await api.post(`/api/engagements/${id}/duplicate`)
    open(copy)
  }
  async function remove(e) {
    if (!confirm(`Delete “${e.customer_name || 'Untitled'}” and all its data?`)) return false
    await api.del(`/api/engagements/${e.id}`)
    if (activeId === e.id) go('/')
    return true
  }

  return (
    <div className="app-root">
      <header className="topbar">
        <div className="topbar-left">
          {route.view !== 'home' && (
            <button className="ghost sm" title="Open another engagement"
              onClick={() => go('/')}>‹ Engagements</button>
          )}
          <div className="topbar-brand">Microsoft 365 TCO</div>
        </div>
        <button className={`gear ${route.view === 'settings' ? 'active' : ''}`} title="Settings"
          onClick={() => (route.view === 'settings' ? closeSettings() : openSettings())}>⚙</button>
      </header>

      <div className="app-shell">
        <main className="main">
          {route.view === 'settings' && <AdminPanel onClose={closeSettings} />}

          {route.view === 'home' && (
            <div className="container">
              <UpdateBanner />
              <PricingBanner onOpenSettings={openSettings} />
              <div className="welcome">
                <h1>Model a Microsoft 365 total cost of ownership.</h1>
                <p className="muted">Open an engagement by typing the customer's name, or start
                  a new one. Inside an engagement, nothing about other customers is shown.</p>
              </div>
              <OpenEngagement onOpen={open} onDuplicate={duplicate} onDelete={remove} />
              <NewEngagement onCreated={open} />
            </div>
          )}

          {route.view === 'engagement' && !active && (
            <div className="container">
              {loadErr
                ? <div className="card"><div className="err">{loadErr}</div>
                    <button className="ghost" onClick={() => go('/')}>Back to engagements</button></div>
                : <div className="card"><p className="muted">Loading…</p></div>}
            </div>
          )}

          {route.view === 'engagement' && active && (
            <div className="container">
              <div className="work-header">
                <div>
                  <h2 style={{ margin: 0 }}>{active.customer_name || 'Untitled engagement'}</h2>
                  <span className="muted">
                    TCO workshop{active.workshop_date ? ` · ${new Date(active.workshop_date + 'T00:00').toLocaleDateString()}` : ''}
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
                  <div className="unit-toggle" title="Money display unit — data stays annualized underneath">
                    <button className={moneyUnit === 'mo' ? 'on' : ''} onClick={() => switchMoneyUnit('mo')}>$/mo</button>
                    <button className={moneyUnit === 'yr' ? 'on' : ''} onClick={() => switchMoneyUnit('yr')}>$/yr</button>
                  </div>
                  <EngagementTools active={tab === 'data'} onData={() => setTab('data')}
                    onDuplicate={() => duplicate(active.id)} onDelete={() => remove(active)} />
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

              {tab !== 'data' && <StepIntro step={tab} />}
              {tab === 'customer' && <CustomerCard engagement={active} meta={meta} onUpdate={setActive} />}
              {tab === 'groups' && (
                <>
                  <Personas engagement={active} meta={meta} />
                  <AgreementCard engagement={active} onUpdate={setActive} />
                  <CurrentLicensing engagement={active} meta={meta} onUpdate={setActive} />
                </>
              )}
              {tab === 'tools' && (
                <>
                  <ThirdParty engagement={active} meta={meta} moneyUnit={moneyUnit} />
                  <CoverageMap engagement={active} meta={meta} section="tools" />
                </>
              )}
              {tab === 'future' && <FutureState engagement={active} meta={meta} moneyUnit={moneyUnit} />}
              {tab === 'gaps' && (
                <>
                  <CoverageCheck engagement={active} onNavigate={setTab} />
                  <details className="card more">
                    <summary>Capability library for this engagement (advanced)</summary>
                    <p className="hint">The capability list and which capabilities each Microsoft plan delivers,
                      as this engagement sees them. Changes here affect this engagement only.</p>
                    <CoverageMap engagement={active} meta={meta} section="library" />
                  </details>
                </>
              )}
              {tab === 'review' && <Review engagement={active} onNavigate={setTab} />}
              {tab === 'summary' && (
                <>
                  <SummaryCard engagement={active} onUpdate={setActive} />
                  <Readout engagement={active} />
                </>
              )}
              {tab === 'data' && <DataInspector engagement={active} meta={meta} />}
              <StepNav tab={tab} onGo={setTab} />
            </div>
          )}
        </main>
      </div>
    </div>
  )
}

// Engagement-specific tools — reached from the header, not the progress stepper.
function EngagementTools({ active, onData, onDuplicate, onDelete }) {
  const [open, setOpen] = useState(false)
  const item = (label, fn, isActive = false) => (
    <button className={`ghost sm ${isActive ? 'active' : ''}`}
      style={{ display: 'block', width: '100%', textAlign: 'left' }}
      onClick={() => { setOpen(false); fn() }}>{label}</button>
  )
  return (
    <div style={{ position: 'relative' }}>
      <button className={`ghost sm ${active ? 'active' : ''}`} onClick={() => setOpen((o) => !o)}>
        🔧 Tools ▾
      </button>
      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 10 }} />
          <div style={{
            position: 'absolute', right: 0, top: '100%', marginTop: 4, zIndex: 20, minWidth: 180,
            background: 'var(--panel2)', border: '1px solid rgba(255,255,255,.12)',
            borderRadius: 8, padding: 4, boxShadow: '0 6px 18px rgba(0,0,0,.4)',
          }}>
            {item('📊 Data inspector', onData, active)}
            {item('⧉ Duplicate engagement', onDuplicate)}
            {item('× Delete engagement', onDelete)}
          </div>
        </>
      )}
    </div>
  )
}

// The page scrolls inside <main>, not the window.
const toTop = () => document.querySelector('.main')?.scrollTo(0, 0)

// Back / Next at the foot of every step, so the walkthrough reads in order.
function StepNav({ tab, onGo }) {
  const i = STEPS.findIndex(([k]) => k === tab)
  if (i < 0) return null
  const prev = STEPS[i - 1]
  const next = STEPS[i + 1]
  return (
    <div className="flex-between" style={{ margin: '.4rem 0 1.4rem' }}>
      {prev ? <button className="ghost" onClick={() => { onGo(prev[0]); toTop() }}>‹ {prev[1]}</button> : <span />}
      {next && <button onClick={() => { onGo(next[0]); toTop() }}>Next: {next[1]} ›</button>}
    </div>
  )
}
