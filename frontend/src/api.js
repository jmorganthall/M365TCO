// Thin REST client for the FastAPI backend.
const BASE = ''

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
    return res.json()
  },
}

export const usd = (n) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(n || 0))

export const pct = (n) => `${(Number(n || 0) * 100).toFixed(0)}%`
