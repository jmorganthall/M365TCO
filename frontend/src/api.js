// Thin REST client for the FastAPI backend.
import { useEffect, useState } from 'react'

const BASE = ''

// ---- One source of calculated numbers, always current ----------------------
// Every successful write through this client bumps the data version. Anything
// that shows calculated numbers reads them through `useResult` (or refetches on
// `useDataVersion`), so an edit on any screen shows on every screen with no
// "Recompute" step to forget. The result is calculated on read by the backend
// (GET …/result) and writing nothing, so refetching is always safe.
let dataVersion = 0
const listeners = new Set()
export function notifyDataChanged() {
  dataVersion += 1
  listeners.forEach((fn) => fn(dataVersion))
}
export const currentDataVersion = () => dataVersion
export function useDataVersion() {
  const [v, setV] = useState(dataVersion)
  useEffect(() => {
    listeners.add(setV)
    return () => { listeners.delete(setV) }
  }, [])
  return v
}
// POSTs that only read (they take a body, but change nothing).
const READ_ONLY_POSTS = [/\/bundle-analysis$/, /\/compute$/, /\/sanity-check$/]

// The engagement's calculated result, refetched after every write.
export function useResult(eid) {
  const version = useDataVersion()
  const [state, setState] = useState({ eid: null, result: null, err: '' })
  useEffect(() => {
    if (!eid) return undefined
    let live = true
    api.get(`/api/engagements/${eid}/result`)
      .then((result) => { if (live) setState({ eid, result, err: '' }) })
      .catch((e) => { if (live) setState((s) => ({ ...s, eid, err: e.message })) })
    return () => { live = false }
  }, [eid, version])
  // Never show another engagement's numbers while this one loads.
  return state.eid === eid ? state : { result: null, err: '' }
}

async function req(method, path, body) {
  const opts = { method, headers: {} }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(BASE + path, opts)
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail || detail } catch (e) {}
    throw new Error(detail)
  }
  if (method !== 'GET' && !READ_ONLY_POSTS.some((re) => re.test(path.split('?')[0]))) {
    notifyDataChanged()
  }
  if (res.status === 204) return null
  const ct = res.headers.get('content-type') || ''
  return ct.includes('application/json') ? res.json() : res.text()
}

export const api = {
  get: (p) => req('GET', p),
  post: (p, b) => req('POST', p, b),
  patch: (p, b) => req('PATCH', p, b),
  put: (p, b) => req('PUT', p, b),
  del: (p) => req('DELETE', p),

  async uploadCsv(path, file, fields = {}) {
    const fd = new FormData()
    fd.append('file', file)
    for (const [k, v] of Object.entries(fields)) fd.append(k, v)
    const res = await fetch(BASE + path, { method: 'POST', body: fd })
    if (!res.ok) {
      let detail = res.statusText
      try { detail = (await res.json()).detail || detail } catch (e) {}
      throw new Error(detail)
    }
    notifyDataChanged()
    return res.json()
  },
}

export const usd = (n) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(n || 0))

// Money display unit: values are stored/computed ANNUALIZED (the engine's
// canonical unit); humans gut-check monthly, so views default to $/mo and a
// header toggle switches to $/yr. A per-browser view preference — not domain
// data — so it lives in localStorage.
export const money = (annual, unit) =>
  unit === 'yr' ? `${usd(annual)}/yr` : `${usd(Number(annual || 0) / 12)}/mo`

export const loadMoneyUnit = () => localStorage.getItem('tco-money-unit') || 'mo'
export const saveMoneyUnit = (u) => localStorage.setItem('tco-money-unit', u)

export const pct = (n) => `${(Number(n || 0) * 100).toFixed(0)}%`
