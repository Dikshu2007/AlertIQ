import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api, Alert, sevColor } from '../api'
import { Badge, EmptyState, ErrorState, Loading } from '../components/ui'

interface Analysis {
  summary: string; severity_assessment: string; possible_root_cause: string
  evidence: string[]; observed_patterns: string[]; possible_causes: string[]
  related_alerts: number[]; affected_services: string[]; investigation_steps: string[]
  recommended_resolution: string; risk_level: string; confidence: number
  consequences: string; references: { title: string; url: string; why: string }[]
  information_gaps: string[]; engine: string
}

const SECTION_LABEL: Record<string, string> = {
  evidence: '✅ Confirmed facts (from your data)',
  observed_patterns: '📈 Observed patterns',
  possible_causes: '❓ Possible causes (hypotheses)',
  investigation_steps: '🔍 Recommended investigation steps',
  information_gaps: '⚠️ Missing information',
}

export default function AlertsPage() {
  const [rows, setRows] = useState<Alert[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ search: '', severity: '', status: '', hours: '' })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<Alert | null>(null)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [history, setHistory] = useState<any>(null)
  const [analyzing, setAnalyzing] = useState(false)
  const [importing, setImporting] = useState(false)
  const [importMsg, setImportMsg] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState('')
  const [createMsg, setCreateMsg] = useState('')
  const [createForm, setCreateForm] = useState({ service_name: '', title: '', description: '', severity: 'medium' })
  const [showDemo, setShowDemo] = useState(false)
  const [demoCompany, setDemoCompany] = useState('')
  const [generatingDemo, setGeneratingDemo] = useState(false)
  const [demoMsg, setDemoMsg] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const load = useCallback((p = page, f = filters) => {
    setLoading(true); setError('')
    const qs = new URLSearchParams({ page: String(p), page_size: '25' })
    if (f.search) qs.set('search', f.search)
    if (f.severity) qs.set('severity', f.severity)
    if (f.status) qs.set('status', f.status)
    if (f.hours) qs.set('hours', f.hours)
    api<{ items: Alert[]; total: number }>(`/api/alerts?${qs}`)
      .then(d => { setRows(d.items); setTotal(d.total) })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [page, filters])

  useEffect(() => { load(page, filters) }, [page])

  const openDetail = async (a: Alert) => {
    setSelected(a); setAnalysis(null); setHistory(null)
    api<any>(`/api/alerts/${a.id}/history`).then(setHistory).catch(() => {})
    const prev = await api<Analysis | null>(`/api/alerts/${a.id}/analysis`).catch(() => null)
    if (prev) setAnalysis(prev)
  }

  const runAnalysis = async () => {
    if (!selected) return
    setAnalyzing(true)
    try {
      setAnalysis(await api<Analysis>(`/api/alerts/${selected.id}/analyze`, { method: 'POST' }))
    } catch (e: any) { setError(e.message) } finally { setAnalyzing(false) }
  }

  const uploadCsv = async (file: File) => {
    setImporting(true); setImportMsg('')
    const fd = new FormData(); fd.append('file', file)
    try {
      const r = await api<{ imported: number; skipped: number; errors: string[] }>('/api/alerts/import-csv', { method: 'POST', body: fd })
      setImportMsg(`Imported ${r.imported} alerts (${r.skipped} skipped).${r.errors.length ? ' Errors: ' + r.errors.slice(0, 3).join('; ') : ''}`)
      load(1, filters)
    } catch (e: any) { setImportMsg(`Import failed: ${e.message}`) } finally { setImporting(false) }
  }

  const createAlert = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    setCreating(true); setCreateError(''); setCreateMsg('')
    try {
      await api<Alert>('/api/alerts', {
        method: 'POST',
        body: JSON.stringify({ ...createForm, source: 'manual' }),
      })
      setCreateForm({ service_name: '', title: '', description: '', severity: 'medium' })
      setShowCreate(false)
      setCreateMsg('Alert added successfully.')
      setPage(1)
      load(1, filters)
    } catch (e: any) {
      setCreateError(e.message || 'Could not add alert')
    } finally {
      setCreating(false)
    }
  }

  const generateDemo = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const company = demoCompany.trim()
    if (!company) return
    const slug = company.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 55) || 'sample-saas'
    const scenarios = [
      { service: 'api', title: 'Routine API latency warning', severity: 'info', hoursAgo: 120, status: 'resolved', description: `${company} API had a brief latency increase during a routine traffic peak.`, metrics: { latency_p95_ms: 420 } },
      { service: 'database', title: 'Database replication lag observed', severity: 'low', hoursAgo: 96, status: 'resolved', description: `Replication lag returned to normal for ${company}.`, metrics: { replication_lag_seconds: 3 } },
      { service: 'auth', title: 'Token refresh latency elevated', severity: 'info', hoursAgo: 72, status: 'resolved', description: `${company} authentication recovered without customer impact.`, metrics: { latency_p95_ms: 310 } },
      { service: 'api', title: 'API 5xx error rate rising', severity: 'critical', hoursAgo: 0.2, status: 'open', description: `Simulated spike in server errors across ${company} API requests.`, metrics: { error_rate_pct: 8.6, requests_per_minute: 12400 } },
      { service: 'database', title: 'Database connection pool near capacity', severity: 'critical', hoursAgo: 0.25, status: 'open', description: `Simulated connection pressure affecting ${company} application services.`, metrics: { connections_used: 94, connections_max: 100 } },
      { service: 'billing', title: 'Billing webhook retry backlog growing', severity: 'high', hoursAgo: 0.35, status: 'open', description: `Payment provider callbacks are retrying and accumulating for ${company}.`, metrics: { retry_queue_depth: 860 } },
      { service: 'auth', title: 'Login requests slower than baseline', severity: 'high', hoursAgo: 0.45, status: 'open', description: `Customers may see slower sign-in while ${company} authentication is under load.`, metrics: { latency_p95_ms: 1850 } },
      { service: 'web', title: 'Customer dashboard page load degraded', severity: 'medium', hoursAgo: 0.55, status: 'open', description: `The ${company} web app is responding slowly, possibly related to API latency.`, metrics: { page_load_p95_ms: 3200 } },
      { service: 'queue', title: 'Background job queue depth increasing', severity: 'high', hoursAgo: 0.65, status: 'open', description: `Background work is falling behind for ${company}; inspect workers and downstream dependencies.`, metrics: { queue_depth: 2400 } },
    ]
    setGeneratingDemo(true); setDemoMsg('')
    try {
      const result = await api<{ imported: number; duplicates: number; errors: string[] }>('/api/alerts/batch', {
        method: 'POST',
        body: JSON.stringify({ alerts: scenarios.map(row => ({
          service_name: `${slug}-${row.service}`,
          title: row.title,
          description: row.description,
          severity: row.severity,
          status: row.status,
          source: 'demo',
          timestamp: new Date(Date.now() - row.hoursAgo * 60 * 60 * 1000).toISOString(),
          labels: { demo: true, demo_company: company, scenario: 'saas-sample' },
          metric_values: row.metrics,
        })) }),
      })
      const msg = `Added ${result.imported} demo alerts for ${company}${result.duplicates ? ` (${result.duplicates} duplicates skipped)` : ''}.`
      setDemoMsg(msg)
      setDemoCompany('')
      setShowDemo(false)
      const allFilters = { search: '', severity: '', status: '', hours: '' }
      setFilters(allFilters); setPage(1); load(1, allFilters)
    } catch (e: any) {
      setDemoMsg(`Could not generate demo alerts: ${e.message}`)
    } finally {
      setGeneratingDemo(false)
    }
  }

  const setStatus = async (a: Alert, status: string) => {
    await api(`/api/alerts/${a.id}/status`, { method: 'PATCH', body: JSON.stringify({ status }) })
    load(page, filters)
    if (selected?.id === a.id) setSelected({ ...a, status })
  }

  const pages = Math.max(1, Math.ceil(total / 25))
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">Alerts Explorer</h1>
          <p className="text-sm text-slate-500">{total} alerts in the platform</p>
        </div>
        <div className="flex gap-2">
          <button className="btn-ghost" onClick={() => { setShowDemo(!showDemo); setDemoMsg('') }}>
            {showDemo ? 'Cancel demo' : 'Generate SaaS demo'}
          </button>
          <button className="btn-primary" onClick={() => { setShowCreate(!showCreate); setCreateError('') }}>
            {showCreate ? 'Cancel' : '+ Add alert'}
          </button>
          <input ref={fileRef} type="file" accept=".csv,.txt" className="hidden"
            onChange={e => e.target.files?.[0] && uploadCsv(e.target.files[0])} />
          <button className="btn-ghost" onClick={() => fileRef.current?.click()} disabled={importing}>
            {importing ? 'Importing…' : '⬆ Upload CSV'}
          </button>
          <a className="btn-ghost" href="/api/alerts/export.csv" target="_blank" rel="noreferrer">⬇ Export</a>
        </div>
      </div>
      {importMsg && <div className="card text-sm text-slate-300">{importMsg}</div>}
      {createMsg && <div className="card text-sm text-emerald-400">{createMsg}</div>}
      {demoMsg && <div className="card text-sm text-slate-300">{demoMsg}</div>}

      {showDemo && (
        <form onSubmit={generateDemo} className="card space-y-3">
          <div>
            <h2 className="font-semibold">Generate sample SaaS alerts</h2>
            <p className="text-xs text-slate-500 mt-1">Creates clearly labeled fictional alerts for API, database, billing, auth, web, and queue services.</p>
          </div>
          <div className="flex flex-col sm:flex-row gap-2">
            <input className="input" required maxLength={80} value={demoCompany}
              onChange={e => setDemoCompany(e.target.value)} placeholder="Company name, e.g. Acme Cloud" />
            <button className="btn-primary shrink-0" disabled={generatingDemo}>
              {generatingDemo ? 'Generating…' : 'Generate demo alerts'}
            </button>
          </div>
        </form>
      )}

      {showCreate && (
        <form onSubmit={createAlert} className="card space-y-3">
          <h2 className="font-semibold">Add an alert manually</h2>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="text-xs text-slate-500 space-y-1">
              <span>Service name</span>
              <input className="input" required maxLength={120} value={createForm.service_name}
                onChange={e => setCreateForm({ ...createForm, service_name: e.target.value })}
                placeholder="e.g. checkout-api" />
            </label>
            <label className="text-xs text-slate-500 space-y-1">
              <span>Severity</span>
              <select className="input" value={createForm.severity}
                onChange={e => setCreateForm({ ...createForm, severity: e.target.value })}>
                {['critical', 'high', 'medium', 'low', 'info'].map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
          </div>
          <label className="block text-xs text-slate-500 space-y-1">
            <span>Title</span>
            <input className="input" required maxLength={500} value={createForm.title}
              onChange={e => setCreateForm({ ...createForm, title: e.target.value })}
              placeholder="Short summary of the issue" />
          </label>
          <label className="block text-xs text-slate-500 space-y-1">
            <span>Description (optional)</span>
            <textarea className="input min-h-24" value={createForm.description}
              onChange={e => setCreateForm({ ...createForm, description: e.target.value })}
              placeholder="Add context, symptoms, or notes" />
          </label>
          {createError && <p className="text-sm text-red-400">{createError}</p>}
          <button className="btn-primary" disabled={creating}>
            {creating ? 'Adding…' : 'Add alert'}
          </button>
        </form>
      )}

      <div className="card grid grid-cols-2 md:grid-cols-5 gap-2">
        <input className="input col-span-2" placeholder="Search title/description…" value={filters.search}
          onChange={e => setFilters({ ...filters, search: e.target.value })}
          onKeyDown={e => e.key === 'Enter' && load(1, filters)} />
        <select className="input" value={filters.severity} onChange={e => { setFilters({ ...filters, severity: e.target.value }); setPage(1) }}>
          <option value="">All severities</option>
          {['critical', 'high', 'medium', 'low', 'info'].map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select className="input" value={filters.status} onChange={e => { setFilters({ ...filters, status: e.target.value }); setPage(1) }}>
          <option value="">All statuses</option>
          {['open', 'acknowledged', 'resolved'].map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select className="input" value={filters.hours} onChange={e => { setFilters({ ...filters, hours: e.target.value }); setPage(1) }}>
          <option value="">All time</option>
          <option value="1">Last hour</option>
          <option value="24">Last 24h</option>
          <option value="168">Last 7 days</option>
          <option value="720">Last 30 days</option>
        </select>
      </div>

      {error && <ErrorState message={error} onRetry={() => load()} />}
      {loading ? <Loading /> : rows.length === 0 ? (
        <EmptyState title="No alerts match" hint="Adjust filters, or ingest alerts via CSV / webhook / integrations." />
      ) : (
        <div className="card overflow-x-auto p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-500 border-b border-surface-border">
                <th className="px-4 py-3">Severity</th><th className="px-4 py-3">Title</th>
                <th className="px-4 py-3">Service</th><th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Time</th><th className="px-4 py-3">Source</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(a => (
                <tr key={a.id} className="border-b border-surface-border/50 hover:bg-surface-border/30 cursor-pointer"
                  onClick={() => openDetail(a)}>
                  <td className="px-4 py-2.5"><Badge text={a.severity} color={sevColor[a.severity]} /></td>
                  <td className="px-4 py-2.5 max-w-[320px] truncate" title={a.title}>{a.title}</td>
                  <td className="px-4 py-2.5 text-slate-400">{a.service_name}</td>
                  <td className="px-4 py-2.5">{a.status}</td>
                  <td className="px-4 py-2.5 text-slate-500 text-xs">{new Date(a.timestamp).toLocaleString()}</td>
                  <td className="px-4 py-2.5 text-slate-500 text-xs">{a.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {pages > 1 && (
        <div className="flex justify-center gap-2 items-center text-sm">
          <button className="btn-ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>← Prev</button>
          <span className="text-slate-500">Page {page} / {pages}</span>
          <button className="btn-ghost" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next →</button>
        </div>
      )}

      {/* Detail drawer */}
      {selected && (
        <div className="fixed inset-0 z-50 flex justify-end" onClick={() => setSelected(null)}>
          <div className="absolute inset-0 bg-black/60" />
          <div className="relative bg-surface-raised border-l border-surface-border w-full max-w-2xl h-full overflow-y-auto p-6 space-y-4"
            onClick={e => e.stopPropagation()}>
            <div className="flex items-start justify-between gap-3">
              <h2 className="font-bold text-lg">{selected.title}</h2>
              <button className="btn-ghost" onClick={() => setSelected(null)}>✕</button>
            </div>
            <div className="flex flex-wrap gap-2 text-sm">
              <Badge text={selected.severity} color={sevColor[selected.severity]} />
              <Badge text={selected.status} />
              <Badge text={`service: ${selected.service_name}`} />
              <Badge text={`source: ${selected.source}`} />
            </div>
            {selected.description && <p className="text-sm text-slate-300 whitespace-pre-wrap">{selected.description}</p>}
            {Object.keys(selected.metric_values).length > 0 && (
              <div>
                <h3 className="text-xs text-slate-500 uppercase mb-1">Metric values</h3>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(selected.metric_values).map(([k, v]) => <Badge key={k} text={`${k}: ${v}`} />)}
                </div>
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              {['open', 'acknowledged', 'resolved'].map(s => (
                <button key={s} disabled={selected.status === s}
                  className={selected.status === s ? 'btn-primary' : 'btn-ghost'}
                  onClick={() => setStatus(selected, s)}>Mark {s}</button>
              ))}
            </div>

            {history && (
              <div className="card">
                <h3 className="font-semibold text-sm mb-2">Alert history</h3>
                <p className="text-xs text-slate-500 mb-2">
                  {history.total_occurrences} occurrence(s) · first seen {history.first_seen ? new Date(history.first_seen).toLocaleString() : '—'}
                  {history.incident_ids?.length > 0 && ` · part of incident(s) #${history.incident_ids.join(', #')}`}
                </p>
              </div>
            )}

            <div>
              <button className="btn-primary" onClick={runAnalysis} disabled={analyzing}>
                {analyzing ? 'Analyzing…' : analysis ? '🔄 Re-run AI analysis' : '🤖 Run AI analysis'}
              </button>
            </div>

            {analysis && (
              <div className="space-y-3">
                <div className="card">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs text-slate-500">Engine: {analysis.engine}</span>
                    <span className="text-xs text-slate-500">confidence: {(analysis.confidence * 100).toFixed(0)}%</span>
                  </div>
                  <p className="text-sm">{analysis.summary}</p>
                </div>
                {Object.entries(SECTION_LABEL).map(([key, label]) => {
                  const items = (analysis as any)[key]
                  if (!items?.length) return null
                  return (
                    <div key={key} className="card">
                      <h3 className="font-semibold text-sm mb-2">{label}</h3>
                      <ul className="list-disc pl-5 space-y-1 text-sm text-slate-300">
                        {items.map((t: string, i: number) => <li key={i}>{t}</li>)}
                      </ul>
                    </div>
                  )
                })}
                {analysis.possible_root_cause && (
                  <div className="card"><h3 className="font-semibold text-sm mb-1">Possible root cause</h3>
                    <p className="text-sm text-slate-300">{analysis.possible_root_cause}</p></div>
                )}
                {analysis.recommended_resolution && (
                  <div className="card"><h3 className="font-semibold text-sm mb-1">Recommended resolution</h3>
                    <p className="text-sm text-slate-300">{analysis.recommended_resolution}</p></div>
                )}
                {analysis.consequences && (
                  <div className="card"><h3 className="font-semibold text-sm mb-1">If unresolved</h3>
                    <p className="text-sm text-slate-300">{analysis.consequences}</p></div>
                )}
                {analysis.references?.length > 0 && (
                  <div className="card"><h3 className="font-semibold text-sm mb-2">References</h3>
                    <ul className="space-y-1 text-sm">
                      {analysis.references.map((r, i) => (
                        <li key={i}><a href={r.url} target="_blank" rel="noreferrer" className="text-accent-soft hover:underline">{r.title}</a>
                          {r.why && <span className="text-slate-500"> — {r.why}</span>}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
