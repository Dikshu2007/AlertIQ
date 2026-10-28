import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'
import { Badge, Loading } from '../components/ui'

interface Msg { role: 'user' | 'assistant'; content: string; engine?: string; sources?: any[]; caveats?: string[] }

const EXAMPLES = [
  'Why is my payment service generating so many alerts?',
  'What caused this incident?',
  'Find unusual patterns in the last 24 hours.',
  'Which alerts are related?',
  'What is the most likely root cause?',
  'How can I resolve this issue?',
  'What should I investigate first?',
  'Are there similar incidents in the past?',
  'Which service is most likely to fail next?',
]

export default function AssistantPage() {
  const { meta } = useAuth()
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [context, setContext] = useState<any>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api<any[]>('/api/ai/chat/history').then(h =>
      setMessages(h.map((m: any) => ({ role: m.role, content: m.content })))).catch(() => {})
  }, [])
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, busy])

  const ask = async (question: string) => {
    if (!question.trim() || busy) return
    setMessages(m => [...m, { role: 'user', content: question }])
    setInput(''); setBusy(true)
    try {
      const r = await api<any>('/api/ai/ask', { method: 'POST', body: JSON.stringify({ question }) })
      setMessages(m => [...m, { role: 'assistant', content: r.answer, engine: r.engine, caveats: r.caveats }])
      setContext(r.context_used)
    } catch (e: any) {
      setMessages(m => [...m, { role: 'assistant', content: `Error: ${e.message}` }])
    } finally { setBusy(false) }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)]">
      <div className="mb-3">
        <h1 className="text-xl font-bold">AI Assistant</h1>
        <p className="text-sm text-slate-500">
          Answers are computed from your actual platform data.
          {meta && (meta.llm_engine === 'none'
            ? ' LLM provider not configured — using the local data-summary engine.'
            : ` Engine: ${meta.llm_engine}.`)}
        </p>
      </div>

      <div className="flex-1 overflow-y-auto space-y-3 pr-1">
        {messages.length === 0 && (
          <div className="card">
            <p className="text-sm text-slate-400 mb-3">Try one of these questions:</p>
            <div className="flex flex-wrap gap-2">
              {EXAMPLES.map(q => (
                <button key={q} className="btn-ghost text-xs" onClick={() => ask(q)}>{q}</button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-2xl rounded-xl px-4 py-3 text-sm whitespace-pre-wrap ${m.role === 'user' ? 'bg-accent text-white' : 'bg-surface-raised border border-surface-border'}`}>
              {m.content}
              {m.role === 'assistant' && m.engine && (
                <div className="mt-2 flex flex-wrap gap-2">
                  <Badge text={`engine: ${m.engine}`} />
                  {(m.caveats || []).map((c, j) => <Badge key={j} text={c} color="bg-yellow-500/10 text-yellow-400" />)}
                </div>
              )}
            </div>
          </div>
        ))}
        {busy && <Loading label="Analyzing your data…" />}
        <div ref={bottomRef} />
      </div>

      {context && (
        <details className="card text-xs text-slate-500 mt-2">
          <summary className="cursor-pointer">Data used for the last answer (grounding)</summary>
          <pre className="mt-2 overflow-x-auto max-h-40">{JSON.stringify(context, null, 2).slice(0, 3000)}</pre>
        </details>
      )}

      <form className="flex gap-2 mt-3" onSubmit={e => { e.preventDefault(); ask(input) }}>
        <input className="input" placeholder="Ask about your alerts, incidents, anomalies…" value={input}
          onChange={e => setInput(e.target.value)} disabled={busy} />
        <button className="btn-primary" disabled={busy || !input.trim()}>Send</button>
      </form>
    </div>
  )
}
