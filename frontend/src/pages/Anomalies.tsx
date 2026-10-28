import React, { useCallback, useEffect, useState } from 'react'
import { api, Anomaly } from '../api'
import { Badge, EmptyState, ErrorState, Loading } from '../components/ui'

const KIND_LABEL: Record<string, string> = {
  frequency_spike: '⚡ Alert frequency spike',
  new_alert_type: '🆕 First-seen alert type',
  metric_zscore: '📉 Metric deviation',
  cascade_sequence: '🌊 Possible cascade',
  correlation_shift: '🔀 Correlation shift',
}

export default function AnomaliesPage() {
  const [rows, setRows] = useState<Anomaly[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [scanning, setScanning] = useState(false)
  const [selected, setSelected] = useState<Anomaly | null>(null)
  const [explain, setExplain] = useState<any>(null)
  const [explaining, setExplaining] = useState(false)

  const load = useCallback(() => {
    setLoading(true); setError('')
    api<{ items: Anomaly[] }>('/api/ai/anomalies')
      .then(d => setRows(d.items)).catch(e => setError(e.message)).finally(() => setLoading(false))
  }, [])
  useEffect(load, [load])

  const scan = async () => {
    setScanning(true)
    try { await api('/api/ai/anomalies/scan', { method: 'POST' }); load() }
    catch (e: any) { setError(e.message) } finally { setScanning(false) }
  }

  const openExplain = async (a: Anomaly) => {
    setSelected(a); setExplain(null)
    setExplaining(true)
    try { setExplain(await api<any>(`/api/ai/anomalies/${a.id}/explain`, { method: 'POST' })) }
    catch (e: any) { setExplain({ error: e.message }) } finally { setExplaining(false) }
  }

  const setStatus = async (a: Anomaly, status: string) => {
    await api(`/api/ai/anomalies/${a.id}/status`, { method: 'PATCH', body: JSON.stringify({ status }) })
    load()
    if (selected?.id === a.id) setSelected({ ...a, status })
  }

  if (loading) return <Loading label="Loading anomalies…" />
  if (error && rows.length === 0) return <ErrorState message={error} onRetry={load} />

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">Anomalies & Pattern Detection</h1>
          <p className="text-sm text-slate-500">
            Every result is method-labeled. No model training is claimed unless it actually ran.
          </p>
        </div>
        <button className="btn-primary" onClick={scan} disabled={scanning}>
          {scanning ? 'Scanning…' : '🔍 Run detection scan'}
        </button>
      </div>

      {rows.length === 0 ? (
        <EmptyState title="No anomalies on record"
          hint="Run a detection scan. With insufficient history, the system will clearly say so instead of inventing results." />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {rows.map(a => (
            <div key={a.id} className="card space-y-2">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge text={KIND_LABEL[a.kind] || a.kind} />
                <Badge text={`score ${a.score}`} />
                <Badge text={`confidence ${(a.confidence * 100).toFixed(0)}%`} />
                <Badge text={a.status} color={a.status === 'confirmed' ? 'bg-emerald-500/15 text-emerald-400'
                  : a.status === 'false_positive' ? 'bg-slate-500/15 text-slate-400' : 'bg-blue-500/15 text-blue-400'} />
              </div>
              <div className="font-semibold text-sm">{a.service_name}</div>
              <p className="text-sm text-slate-300">{a.explanation}</p>
              <p className="text-xs text-slate-500">method: {a.method}</p>
              <div className="flex flex-wrap gap-2 pt-1">
                <button className="btn-ghost text-xs" onClick={() => openExplain(a)}>🧭 Explain</button>
                {['confirmed', 'false_positive', 'reviewed'].map(s => (
                  <button key={s} disabled={a.status === s} className={a.status === s ? 'btn-primary text-xs' : 'btn-ghost text-xs'}
                    onClick={() => setStatus(a, s)}>
                    {s === 'false_positive' ? 'Mark false positive' : `Mark ${s}`}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {selected && (
        <div className="fixed inset-0 z-50 flex justify-end" onClick={() => setSelected(null)}>
          <div className="absolute inset-0 bg-black/60" />
          <div className="relative bg-surface-raised border-l border-surface-border w-full max-w-2xl h-full overflow-y-auto p-6 space-y-4"
            onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-start">
              <h2 className="font-bold">Why is this unusual?</h2>
              <button className="btn-ghost" onClick={() => setSelected(null)}>✕</button>
            </div>
            {explaining ? <Loading label="Comparing with history…" /> : explain?.error ?
              <ErrorState message={explain.error} /> : explain && (
                <div className="space-y-3">
                  <div className="card">
                    <div className="flex flex-wrap gap-2 mb-2">
                      <Badge text={`verdict: ${explain.triage.verdict}`} />
                      <Badge text={`anomaly score ${explain.triage.anomaly_score}`} />
                      <Badge text={`confidence ${explain.triage.confidence}`} />
                      {explain.triage.deployment_related && <Badge text="deployment-related" color="bg-purple-500/15 text-purple-300" />}
                    </div>
                    <p className="text-sm">{explain.triage.why_unusual}</p>
                    <p className="text-xs text-slate-500 mt-2">method: {explain.triage.method}</p>
                    {explain.triage.needs_more_data && (
                      <p className="text-xs text-yellow-400 mt-2">⚠ More historical data is required for higher confidence.</p>
                    )}
                    <p className="text-xs text-slate-500 mt-2">affected services: {explain.triage.affected_services.join(', ')}</p>
                  </div>
                  {explain.research && (
                    <div className="card">
                      <h3 className="font-semibold text-sm mb-2">Web research</h3>
                      <p className="text-sm text-slate-300 mb-2">{explain.research.answer}</p>
                      {(explain.research.caveats || []).map((c: string, i: number) =>
                        <p key={i} className="text-xs text-yellow-400">⚠ {c}</p>)}
                      <ul className="space-y-2 mt-2">
                        {explain.research.sources.map((s: any, i: number) => (
                          <li key={i} className="text-xs">
                            <a href={s.url} target="_blank" rel="noreferrer" className="text-accent-soft hover:underline">{s.title}</a>
                            <span className="text-slate-500"> [{s.source_type}, relevance: {s.relevance}]</span>
                          </li>
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
