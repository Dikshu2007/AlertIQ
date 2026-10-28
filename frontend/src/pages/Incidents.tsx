import React, { useCallback, useEffect, useState } from 'react'
import { api, Incident, sevColor, Recommendation } from '../api'
import { Badge, EmptyState, ErrorState, Loading } from '../components/ui'

interface Analysis {
  summary: string; severity_assessment: string; possible_root_cause: string
  evidence: string[]; observed_patterns: string[]; possible_causes: string[]
  investigation_steps: string[]; recommended_resolution: string; risk_level: string
  confidence: number; consequences: string; references: { title: string; url: string; why: string }[]
  information_gaps: string[]; engine: string
}

const SECTION_LABEL: Record<string, string> = {
  evidence: '✅ Confirmed facts (from your data)',
  observed_patterns: '📈 Observed patterns',
  possible_causes: '❓ Possible causes (hypotheses)',
  investigation_steps: '🔍 Recommended investigation steps',
  information_gaps: '⚠️ Missing information',
}

export default function IncidentsPage() {
  const [rows, setRows] = useState<Incident[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<Incident | null>(null)
  const [alerts, setAlerts] = useState<any[]>([])
  const [timeline, setTimeline] = useState<any[]>([])
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [recs, setRecs] = useState<Recommendation[]>([])
  const [busy, setBusy] = useState('')

  const load = useCallback(() => {
    setLoading(true); setError('')
    api<{ items: Incident[] }>('/api/incidents')
      .then(d => setRows(d.items)).catch(e => setError(e.message)).finally(() => setLoading(false))
  }, [])
  useEffect(load, [load])

  const openDetail = async (i: Incident) => {
    setSelected(i); setAlerts([]); setTimeline([]); setAnalysis(null); setRecs([])
    api<any[]>(`/api/incidents/${i.id}/alerts`).then(setAlerts).catch(() => {})
    api<any>(`/api/incidents/${i.id}/timeline`).then(d => setTimeline(d.events)).catch(() => {})
    api<Analysis>(`/api/incidents/${i.id}/analysis`).then(setAnalysis).catch(() => {})
    api<Recommendation[]>(`/api/incidents/${i.id}/recommendations`).then(setRecs).catch(() => {})
  }

  const setStatus = async (status: string) => {
    if (!selected) return
    setBusy(status)
    try {
      const updated = await api<Incident>(`/api/incidents/${selected.id}/status`, { method: 'PATCH', body: JSON.stringify({ status }) })
      setSelected(updated); load()
    } catch (e: any) { setError(e.message) } finally { setBusy('') }
  }

  const runAnalysis = async () => {
    if (!selected) return
    setBusy('analyze')
    try { setAnalysis(await api<Analysis>(`/api/incidents/${selected.id}/analyze`, { method: 'POST' })) }
    catch (e: any) { setError(e.message) } finally { setBusy('') }
  }

  const runRecs = async () => {
    if (!selected) return
    setBusy('recs')
    try {
      await api(`/api/incidents/${selected.id}/analyze`, { method: 'POST' }) // regenerates + persists
      setRecs(await api<Recommendation[]>(`/api/incidents/${selected.id}/recommendations`))
    } catch (e: any) { setError(e.message) } finally { setBusy('') }
  }

  if (loading) return <Loading label="Loading incidents…" />
  if (error && rows.length === 0) return <ErrorState message={error} onRetry={load} />

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">Incident Management</h1>
        <p className="text-sm text-slate-500">{rows.filter(r => r.status !== 'resolved').length} active · {rows.length} total</p>
      </div>

      {rows.length === 0 ? (
        <EmptyState title="No incidents yet" hint="Incidents are created automatically when alerts correlate, or manually via POST /api/incidents." />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {rows.map(i => (
            <button key={i.id} className="card text-left hover:border-accent transition-colors" onClick={() => openDetail(i)}>
              <div className="flex items-center gap-2 mb-1">
                <Badge text={i.severity} color={sevColor[i.severity]} />
                <Badge text={i.status} color={i.status === 'resolved' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-orange-500/15 text-orange-400'} />
                <span className="text-xs text-slate-500 ml-auto">#{i.id}</span>
              </div>
              <div className="font-semibold text-sm">{i.title}</div>
              <div className="text-xs text-slate-500 mt-1">
                {i.affected_services.join(', ') || 'no services recorded'} · {new Date(i.created_at).toLocaleString()}
              </div>
              {i.root_cause && <div className="text-xs text-slate-400 mt-2 line-clamp-2">🔎 {i.root_cause}</div>}
            </button>
          ))}
        </div>
      )}

      {selected && (
        <div className="fixed inset-0 z-50 flex justify-end" onClick={() => setSelected(null)}>
          <div className="absolute inset-0 bg-black/60" />
          <div className="relative bg-surface-raised border-l border-surface-border w-full max-w-3xl h-full overflow-y-auto p-6 space-y-4"
            onClick={e => e.stopPropagation()}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="font-bold text-lg">{selected.title}</h2>
                <div className="flex gap-2 mt-2">
                  <Badge text={selected.severity} color={sevColor[selected.severity]} />
                  <Badge text={selected.status} />
                </div>
              </div>
              <button className="btn-ghost" onClick={() => setSelected(null)}>✕</button>
            </div>

            <div>
              <h3 className="text-xs text-slate-500 uppercase mb-2">Status</h3>
              <div className="flex flex-wrap gap-2">
                {['open', 'investigating', 'mitigated', 'resolved'].map(s => (
                  <button key={s} disabled={selected.status === s || busy === s}
                    className={selected.status === s ? 'btn-primary' : 'btn-ghost'}
                    onClick={() => setStatus(s)}>Mark {s}</button>
                ))}
              </div>
            </div>

            <div>
              <h3 className="text-xs text-slate-500 uppercase mb-2">Affected services</h3>
              <div className="flex flex-wrap gap-2">{selected.affected_services.map(s => <Badge key={s} text={s} />)}</div>
            </div>

            <div>
              <h3 className="text-xs text-slate-500 uppercase mb-2">Incident timeline ({timeline.length})</h3>
              {timeline.length === 0 ? <p className="text-sm text-slate-500">Loading…</p> : (
                <ol className="relative border-l border-surface-border ml-2 space-y-3">
                  {timeline.map((ev, i) => (
                    <li key={i} className="ml-4">
                      <span className="absolute -left-1.5 w-3 h-3 rounded-full bg-accent mt-1" />
                      <div className="text-xs text-slate-500">{new Date(ev.timestamp).toLocaleString()}</div>
                      <div className="text-sm">
                        {ev.type === 'alert' ? (
                          <span><Badge text={ev.severity} color={sevColor[ev.severity]} /> {ev.title} <span className="text-slate-500">({ev.service}{ev.relation === 'primary' ? ', primary' : ''})</span></span>
                        ) : <span className="font-semibold">{ev.type.replace('_', ' ')}</span>}
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </div>

            <div>
              <h3 className="text-xs text-slate-500 uppercase mb-2">Related alerts ({alerts.length})</h3>
              <div className="space-y-1 text-sm max-h-44 overflow-y-auto">
                {alerts.map(a => (
                  <div key={a.id} className="flex gap-2 items-center">
                    <Badge text={a.severity} color={sevColor[a.severity]} />
                    <span className="truncate">{a.title}</span>
                    <span className="text-slate-500 text-xs ml-auto shrink-0">{a.service_name}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="flex gap-2">
              <button className="btn-primary" onClick={runAnalysis} disabled={!!busy}>
                {busy === 'analyze' ? 'Analyzing…' : '🤖 Run AI analysis'}
              </button>
              <button className="btn-ghost" onClick={runRecs} disabled={!!busy}>
                {busy === 'recs' ? 'Ranking…' : '🎯 Refresh recommendations'}
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

            {recs.length > 0 && (
              <div className="space-y-3">
                <h3 className="text-xs text-slate-500 uppercase">Ranked recommended actions</h3>
                {recs.map(r => (
                  <div key={r.rank} className="card">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="badge bg-accent/15 text-accent-soft">#{r.rank}</span>
                      <Badge text={r.priority} color={r.priority === 'high' ? 'bg-red-500/15 text-red-400' : 'bg-slate-500/15 text-slate-300'} />
                      <Badge text={`difficulty: ${r.difficulty}`} />
                      {r.requires_human_approval && <Badge text="⚠ needs human approval" color="bg-yellow-500/15 text-yellow-400" />}
                      <span className="text-xs text-slate-500 ml-auto">score {r.score} · {r.source}</span>
                    </div>
                    <div className="font-semibold text-sm">{r.action}</div>
                    <p className="text-xs text-slate-400 mt-1">Why: {r.rationale}</p>
                    <p className="text-xs text-slate-400">Benefit: {r.expected_benefit}</p>
                    <p className="text-xs text-slate-400">Risk: {r.risk}</p>
                    <p className="text-xs text-slate-500 mt-1">Evidence: {(r.evidence || []).join(' · ')}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
