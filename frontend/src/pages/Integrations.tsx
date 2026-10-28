import React, { useCallback, useEffect, useState } from 'react'
import { api, Integration } from '../api'
import { Badge, EmptyState, ErrorState, Loading } from '../components/ui'

const KINDS: { kind: string; label: string; hint: string; credFields: string[]; configFields: string[] }[] = [
  { kind: 'prometheus', label: 'Prometheus', hint: 'Pulls firing alerts from /api/v1/alerts', credFields: ['username', 'password'], configFields: ['base_url'] },
  { kind: 'datadog', label: 'Datadog', hint: 'Pulls alerting monitors', credFields: ['api_key', 'app_key'], configFields: ['base_url'] },
  { kind: 'github', label: 'GitHub', hint: 'Maps failed CI runs to alerts', credFields: ['token'], configFields: ['repo', 'service_name'] },
  { kind: 'webhook', label: 'Generic webhook', hint: 'Push alerts to POST /api/webhooks/{token}', credFields: [], configFields: [] },
  { kind: 'csv', label: 'CSV upload', hint: 'Upload CSV files on the Alerts page', credFields: [], configFields: [] },
  { kind: 'grafana', label: 'Grafana', hint: 'Use Grafana webhook notifications → generic webhook', credFields: [], configFields: ['base_url'] },
  { kind: 'rest', label: 'Custom REST API', hint: 'Push normalized alerts via POST /api/alerts', credFields: [], configFields: ['base_url'] },
]

export default function IntegrationsPage() {
  const [rows, setRows] = useState<Integration[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [kind, setKind] = useState('prometheus')
  const [name, setName] = useState('')
  const [config, setConfig] = useState<Record<string, string>>({})
  const [creds, setCreds] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<number | 'new' | ''>('')
  const [flash, setFlash] = useState('')
  const [events, setEvents] = useState<any[]>([])

  const spec = KINDS.find(k => k.kind === kind)!

  const load = useCallback(() => {
    setLoading(true); setError('')
    api<Integration[]>('/api/integrations').then(setRows).catch(e => setError(e.message)).finally(() => setLoading(false))
  }, [])
  useEffect(load, [load])

  const create = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy('new')
    try {
      await api('/api/integrations', { method: 'POST', body: JSON.stringify({ name, kind, config, credentials: creds }) })
      setName(''); setConfig({}); setCreds({}); setFlash('Integration added — credentials are encrypted at rest.')
      load()
    } catch (err: any) { setError(err.message) } finally { setBusy('') }
  }

  const act = async (i: Integration, action: 'test' | 'sync' | 'toggle' | 'delete') => {
    setBusy(i.id)
    try {
      if (action === 'delete') { await api(`/api/integrations/${i.id}`, { method: 'DELETE' }) }
      else if (action === 'toggle') { await api(`/api/integrations/${i.id}`, { method: 'PATCH', body: JSON.stringify({ enabled: !i.enabled }) }) }
      else {
        const r = await api<any>(`/api/integrations/${i.id}/${action}`, { method: 'POST' })
        setFlash(`${action}: ${r.ok ? 'OK' : 'FAILED'} — ${r.message || JSON.stringify(r).slice(0, 140)}`)
      }
      load()
    } catch (err: any) { setFlash(`${action} failed: ${err.message}`) } finally { setBusy('') }
  }

  const openEvents = async (i: Integration) => {
    setBusy(i.id)
    try { setEvents(await api<any[]>(`/api/integrations/${i.id}/events`)) } catch { setEvents([]) } finally { setBusy('') }
  }

  if (loading) return <Loading label="Loading integrations…" />

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">Integrations</h1>
        <p className="text-sm text-slate-500">Credentials are Fernet-encrypted on the backend and never returned to the frontend.</p>
      </div>
      {flash && <div className="card text-sm">{flash}</div>}
      {error && <ErrorState message={error} onRetry={load} />}

      <form onSubmit={create} className="card space-y-3">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <div>
            <label className="text-xs text-slate-500 block mb-1">Type</label>
            <select className="input" value={kind} onChange={e => { setKind(e.target.value); setConfig({}); setCreds({}) }}>
              {KINDS.map(k => <option key={k.kind} value={k.kind}>{k.label}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-500 block mb-1">Name</label>
            <input className="input" required value={name} onChange={e => setName(e.target.value)} placeholder="prod-cluster" />
          </div>
          {spec.configFields.map(f => (
            <div key={f}>
              <label className="text-xs text-slate-500 block mb-1">{f}</label>
              <input className="input" value={config[f] || ''} onChange={e => setConfig({ ...config, [f]: e.target.value })} />
            </div>
          ))}
          {spec.credFields.map(f => (
            <div key={f}>
              <label className="text-xs text-slate-500 block mb-1">{f} (secret)</label>
              <input className="input" type="password" value={creds[f] || ''} onChange={e => setCreds({ ...creds, [f]: e.target.value })} />
            </div>
          ))}
        </div>
        <div className="flex items-center gap-3">
          <button className="btn-primary" disabled={busy === 'new'}>{busy === 'new' ? 'Adding…' : '+ Add integration'}</button>
          <span className="text-xs text-slate-500">{spec.hint}</span>
        </div>
      </form>

      {rows.length === 0 ? (
        <EmptyState title="No integrations configured" hint="Add Prometheus, Datadog, GitHub or a generic webhook to start ingesting real alerts." />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {rows.map(i => (
            <div key={i.id} className="card space-y-2">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge text={i.kind} color="bg-accent/15 text-accent-soft" />
                <span className="font-semibold text-sm">{i.name}</span>
                <Badge text={i.enabled ? 'enabled' : 'disabled'}
                  color={i.enabled ? 'bg-emerald-500/15 text-emerald-400' : 'bg-slate-500/15 text-slate-400'} />
                {i.last_status && <Badge text={`last: ${i.last_status}`} color={i.last_status === 'ok' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'} />}
                <span className="ml-auto text-xs text-slate-500">{i.has_credentials ? '🔒 credentials stored' : 'no credentials'}</span>
              </div>
              {i.last_error && <p className="text-xs text-red-400">Last error: {i.last_error}</p>}
              {i.webhook_token && (
                <p className="text-xs text-slate-500 break-all">
                  Webhook URL: <code className="text-slate-300">POST /api/webhooks/{i.webhook_token}</code>
                </p>
              )}
              <div className="flex flex-wrap gap-2 pt-1">
                {['prometheus', 'datadog', 'github'].includes(i.kind) && (
                  <>
                    <button className="btn-ghost text-xs" disabled={busy === i.id} onClick={() => act(i, 'test')}>🔌 Test</button>
                    <button className="btn-ghost text-xs" disabled={busy === i.id} onClick={() => act(i, 'sync')}>⬇ Sync now</button>
                  </>
                )}
                <button className="btn-ghost text-xs" onClick={() => act(i, 'toggle')}>{i.enabled ? 'Disable' : 'Enable'}</button>
                <button className="btn-ghost text-xs" onClick={() => openEvents(i)}>📜 Log</button>
                <button className="btn-ghost text-xs text-red-400" onClick={() => act(i, 'delete')}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {events.length > 0 && (
        <div className="card">
          <div className="flex justify-between items-center mb-2">
            <h3 className="font-semibold text-sm">Recent integration events</h3>
            <button className="btn-ghost text-xs" onClick={() => setEvents([])}>Close</button>
          </div>
          <div className="space-y-1 text-xs max-h-60 overflow-y-auto">
            {events.map(ev => (
              <div key={ev.id} className="flex gap-2">
                <Badge text={ev.level} color={ev.level === 'error' ? 'bg-red-500/15 text-red-400' : 'bg-emerald-500/15 text-emerald-400'} />
                <span className="text-slate-500 shrink-0">{new Date(ev.created_at).toLocaleString()}</span>
                <span className="text-slate-300">{ev.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
