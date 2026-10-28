import React, { useCallback, useEffect, useState } from 'react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { api, sevColor } from '../api'
import { Badge, EmptyState, ErrorState, Loading } from '../components/ui'

interface ServiceHealth {
  service: { id: number; name: string; description: string; dependencies: string[]; criticality: string; last_deploy_at: string | null }
  open_alerts: number; critical_alerts: number; last_alert_at: string | null; health: number
  metrics_summary: Record<string, { last: number; avg: number; min: number; max: number; n: number }>
  recent_alerts: any[]
}

export default function ServicesPage() {
  const [rows, setRows] = useState<ServiceHealth[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [series, setSeries] = useState<Record<string, { value: number; timestamp: string }[]>>({})
  const [form, setForm] = useState({ name: '', description: '', dependencies: '', criticality: 'medium' })
  const [creating, setCreating] = useState(false)

  const load = useCallback(() => {
    setLoading(true); setError('')
    api<ServiceHealth[]>('/api/services/health').then(setRows).catch(e => setError(e.message)).finally(() => setLoading(false))
  }, [])
  useEffect(load, [load])

  const openMetrics = async (name: string) => {
    setSelected(name)
    try { setSeries((await api<any>(`/api/services/${encodeURIComponent(name)}/metrics`)).series) }
    catch { setSeries({}) }
  }

  const createService = async (e: React.FormEvent) => {
    e.preventDefault()
    setCreating(true)
    try {
      await api('/api/services', {
        method: 'POST',
        body: JSON.stringify({
          name: form.name, description: form.description, criticality: form.criticality,
          dependencies: form.dependencies.split(',').map(s => s.trim()).filter(Boolean),
        }),
      })
      setForm({ name: '', description: '', dependencies: '', criticality: 'medium' })
      load()
    } catch (err: any) { setError(err.message) } finally { setCreating(false) }
  }

  const markDeploy = async (name: string) => {
    const svc = rows.find(r => r.service.name === name)
    if (!svc) return
    await api(`/api/services/${svc.service.id}/deploy`, { method: 'POST' })
    load()
  }

  if (loading) return <Loading label="Loading services…" />
  if (error && rows.length === 0) return <ErrorState message={error} onRetry={load} />

  const metricChart = Object.keys(series).length > 0 ? Object.entries(series).map(([name, points]) => ({ name, points })) : []

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">Service Monitoring</h1>
        <p className="text-sm text-slate-500">Health is derived from open alerts, critical alerts and active incidents.</p>
      </div>

      <form onSubmit={createService} className="card grid grid-cols-2 md:grid-cols-5 gap-2 items-end">
        <div><label className="text-xs text-slate-500 block mb-1">Service name</label>
          <input className="input" required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></div>
        <div><label className="text-xs text-slate-500 block mb-1">Description</label>
          <input className="input" value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} /></div>
        <div><label className="text-xs text-slate-500 block mb-1">Depends on (comma-sep)</label>
          <input className="input" value={form.dependencies} onChange={e => setForm({ ...form, dependencies: e.target.value })} placeholder="db-core, cache" /></div>
        <div><label className="text-xs text-slate-500 block mb-1">Criticality</label>
          <select className="input" value={form.criticality} onChange={e => setForm({ ...form, criticality: e.target.value })}>
            {['critical', 'high', 'medium', 'low'].map(c => <option key={c}>{c}</option>)}
          </select></div>
        <button className="btn-primary" disabled={creating}>{creating ? 'Adding…' : '+ Add service'}</button>
      </form>

      {rows.length === 0 ? (
        <EmptyState title="No services registered" hint="Register your services above — dependency links power incident correlation and root-cause hypotheses." />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {rows.map(r => (
            <div key={r.service.name} className="card space-y-3">
              <div className="flex items-center justify-between">
                <div className="font-semibold">{r.service.name}</div>
                <div className={`text-sm font-bold ${r.health >= 80 ? 'text-emerald-400' : r.health >= 50 ? 'text-yellow-400' : 'text-red-400'}`}>
                  {r.health}%
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5 text-xs">
                <Badge text={r.service.criticality} />
                {r.service.dependencies.map(d => <Badge key={d} text={`↳ ${d}`} />)}
                {r.service.last_deploy_at && <Badge text="recent deploy" color="bg-purple-500/15 text-purple-300" />}
              </div>
              <div className="text-xs text-slate-500">
                {r.open_alerts} open alerts · {r.critical_alerts} critical
              </div>
              {Object.keys(r.metrics_summary).length > 0 && (
                <div className="text-xs text-slate-400 space-y-0.5">
                  {Object.entries(r.metrics_summary).map(([k, v]) => (
                    <div key={k}>{k}: last {v.last} · avg {v.avg} (n={v.n})</div>
                  ))}
                </div>
              )}
              {r.recent_alerts.length > 0 && (
                <div className="space-y-1">
                  {r.recent_alerts.slice(0, 3).map(a => (
                    <div key={a.id} className="flex gap-2 items-center text-xs">
                      <Badge text={a.severity} color={sevColor[a.severity]} />
                      <span className="truncate">{a.title}</span>
                    </div>
                  ))}
                </div>
              )}
              <div className="flex gap-2">
                <button className="btn-ghost text-xs" onClick={() => openMetrics(r.service.name)}>📈 Metrics</button>
                <button className="btn-ghost text-xs" onClick={() => markDeploy(r.service.name)}>🚀 Record deploy</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {selected && (
        <div className="fixed inset-0 z-50 flex justify-end" onClick={() => setSelected(null)}>
          <div className="absolute inset-0 bg-black/60" />
          <div className="relative bg-surface-raised border-l border-surface-border w-full max-w-2xl h-full overflow-y-auto p-6"
            onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-4">
              <h2 className="font-bold">{selected} — metric history</h2>
              <button className="btn-ghost" onClick={() => setSelected(null)}>✕</button>
            </div>
            {metricChart.length === 0 ? (
              <EmptyState title="No metrics recorded" hint="POST /api/services/{name}/metrics?name=cpu_usage&value=12 to push metrics." />
            ) : metricChart.map(m => (
              <div key={m.name} className="card mb-4">
                <h3 className="text-sm font-semibold mb-2">{m.name}</h3>
                <ResponsiveContainer width="100%" height={200}>
                  <LineChart data={m.points.map(p => ({ ...p, ts: new Date(p.timestamp).toLocaleDateString() }))}>
                    <XAxis dataKey="ts" tick={{ fill: '#64748b', fontSize: 11 }} />
                    <YAxis tick={{ fill: '#64748b', fontSize: 11 }} />
                    <Tooltip contentStyle={{ background: '#161a23', border: '1px solid #242a38' }} />
                    <Line dataKey="value" stroke="#6366f1" dot={false} name={m.name} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
