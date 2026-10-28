import React, { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, Legend } from 'recharts'
import { api, Alert, Incident, sevColor } from '../api'
import { Badge, EmptyState, ErrorState, Loading, StatCard } from '../components/ui'

interface Overview {
  total_alerts: number; active_incidents: number; critical_alerts: number
  resolved_incidents: number; resolved_alerts: number; system_health: number
  alert_timeseries: { date: string; total: number; critical: number; high: number }[]
  incident_trend: { date: string; created: number; resolved: number }[]
  severity_breakdown: { severity: string; count: number }[]
  service_health: { service: string; health: number; open_alerts: number; critical_alerts: number; active_incident: boolean; criticality: string }[]
  recent_alerts: Alert[]; recent_incidents: Incident[]
}

const SEV_COLORS: Record<string, string> = { critical: '#ef4444', high: '#f97316', medium: '#eab308', low: '#3b82f6', info: '#64748b', unknown: '#475569' }

export default function OverviewPage() {
  const [data, setData] = useState<Overview | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true); setError('')
    api<Overview>('/api/dashboard/overview').then(setData).catch(e => setError(e.message)).finally(() => setLoading(false))
  }, [])
  useEffect(load, [load])

  if (loading) return <Loading label="Loading dashboard…" />
  if (error) return <ErrorState message={error} onRetry={load} />
  if (!data) return null
  if (data.total_alerts === 0 && data.active_incidents === 0)
    return <EmptyState title="No monitoring data yet" hint="Ingest alerts via CSV upload, webhook, or an integration to activate the dashboard." />

  const healthTone = data.system_health >= 80 ? 'text-emerald-400' : data.system_health >= 50 ? 'text-yellow-400' : 'text-red-400'

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold">Overview</h1>
        <p className="text-sm text-slate-500">Live platform status — all figures come from your ingested data.</p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <StatCard label="Total alerts" value={data.total_alerts} />
        <StatCard label="Active incidents" value={data.active_incidents} tone={data.active_incidents ? 'text-orange-400' : 'text-emerald-400'} />
        <StatCard label="Critical alerts" value={data.critical_alerts} tone={data.critical_alerts ? 'text-red-400' : 'text-slate-100'} />
        <StatCard label="Resolved incidents" value={data.resolved_incidents} tone="text-emerald-400" />
        <StatCard label="Resolved alerts" value={data.resolved_alerts} tone="text-emerald-400" />
        <StatCard label="System health" value={`${data.system_health}%`} tone={healthTone} sub="derived from open criticals & incidents" />
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <div className="card">
          <h2 className="font-semibold mb-3 text-sm text-slate-400">Alert frequency — last 7 days</h2>
          {data.alert_timeseries.length === 0 ? <p className="text-sm text-slate-500">No alerts in the window.</p> : (
            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={data.alert_timeseries}>
                <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 11 }} />
                <YAxis tick={{ fill: '#64748b', fontSize: 11 }} allowDecimals={false} />
                <Tooltip contentStyle={{ background: '#161a23', border: '1px solid #242a38' }} />
                <Area dataKey="total" stroke="#6366f1" fill="#6366f1" fillOpacity={0.25} name="All alerts" />
                <Area dataKey="critical" stroke="#ef4444" fill="#ef4444" fillOpacity={0.3} name="Critical" />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
        <div className="card">
          <h2 className="font-semibold mb-3 text-sm text-slate-400">Incident trend — created vs resolved</h2>
          {data.incident_trend.length === 0 ? <p className="text-sm text-slate-500">No incidents recorded yet.</p> : (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={data.incident_trend}>
                <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 11 }} />
                <YAxis tick={{ fill: '#64748b', fontSize: 11 }} allowDecimals={false} />
                <Tooltip contentStyle={{ background: '#161a23', border: '1px solid #242a38' }} />
                <Legend />
                <Bar dataKey="created" fill="#f97316" name="Created" />
                <Bar dataKey="resolved" fill="#10b981" name="Resolved" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <div className="card">
          <h2 className="font-semibold mb-3 text-sm text-slate-400">Severity breakdown</h2>
          {data.severity_breakdown.length === 0 ? <p className="text-sm text-slate-500">No data.</p> : (
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie data={data.severity_breakdown} dataKey="count" nameKey="severity" innerRadius={55} outerRadius={85} paddingAngle={3}>
                  {data.severity_breakdown.map((s, i) => <Cell key={i} fill={SEV_COLORS[s.severity] || '#475569'} />)}
                </Pie>
                <Tooltip contentStyle={{ background: '#161a23', border: '1px solid #242a38' }} />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          )}
        </div>
        <div className="card">
          <h2 className="font-semibold mb-3 text-sm text-slate-400">Service health</h2>
          {data.service_health.length === 0 ? <p className="text-sm text-slate-500">No services recorded — add them on the Services page.</p> : (
            <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
              {data.service_health.map(s => (
                <div key={s.service} className="flex items-center gap-3 text-sm">
                  <span className="w-40 truncate" title={s.service}>{s.service}</span>
                  <div className="flex-1 h-2 bg-surface-border rounded-full overflow-hidden">
                    <div className={`h-full rounded-full ${s.health >= 80 ? 'bg-emerald-500' : s.health >= 50 ? 'bg-yellow-500' : 'bg-red-500'}`}
                      style={{ width: `${Math.max(3, s.health)}%` }} />
                  </div>
                  <span className="w-10 text-right text-slate-400">{s.health}%</span>
                  {s.active_incident && <Badge text="incident" color="bg-orange-500/15 text-orange-400" />}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <div className="card">
          <div className="flex justify-between items-center mb-3">
            <h2 className="font-semibold text-sm text-slate-400">Recent alerts</h2>
            <Link to="/alerts" className="text-xs text-accent-soft hover:underline">View all →</Link>
          </div>
          {data.recent_alerts.length === 0 ? <p className="text-sm text-slate-500">No alerts yet.</p> : (
            <div className="space-y-2">
              {data.recent_alerts.map(a => (
                <div key={a.id} className="flex items-center gap-2 text-sm">
                  <Badge text={a.severity} color={sevColor[a.severity]} />
                  <span className="flex-1 truncate" title={a.title}>{a.title}</span>
                  <span className="text-slate-500 text-xs shrink-0">{a.service_name}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="card">
          <div className="flex justify-between items-center mb-3">
            <h2 className="font-semibold text-sm text-slate-400">Recent incidents</h2>
            <Link to="/incidents" className="text-xs text-accent-soft hover:underline">View all →</Link>
          </div>
          {data.recent_incidents.length === 0 ? <p className="text-sm text-slate-500">No incidents yet.</p> : (
            <div className="space-y-2">
              {data.recent_incidents.map(i => (
                <div key={i.id} className="flex items-center gap-2 text-sm">
                  <Badge text={i.severity} color={sevColor[i.severity]} />
                  <span className="flex-1 truncate" title={i.title}>{i.title}</span>
                  <Badge text={i.status} color={i.status === 'resolved' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-orange-500/15 text-orange-400'} />
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
