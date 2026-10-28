import React from 'react'

export function StatCard({ label, value, sub, tone = 'text-slate-100' }: {
  label: string; value: React.ReactNode; sub?: string; tone?: string
}) {
  return (
    <div className="card">
      <div className="text-xs text-slate-500 uppercase tracking-wide">{label}</div>
      <div className={`text-2xl font-bold mt-1 ${tone}`}>{value}</div>
      {sub && <div className="text-xs text-slate-500 mt-1">{sub}</div>}
    </div>
  )
}

export function Badge({ text, color }: { text: string; color?: string }) {
  return <span className={`badge ${color || 'bg-slate-500/15 text-slate-300'}`}>{text}</span>
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex items-center justify-center py-16 text-slate-500 gap-3">
      <span className="animate-spin inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full" />
      {label}
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="card text-center py-10">
      <div className="text-3xl mb-2">⚠️</div>
      <div className="text-red-400 mb-1">Something went wrong</div>
      <div className="text-sm text-slate-500 mb-4">{message}</div>
      {onRetry && <button className="btn-primary" onClick={onRetry}>Retry</button>}
    </div>
  )
}

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="card text-center py-12">
      <div className="text-3xl mb-2">📭</div>
      <div className="font-semibold">{title}</div>
      {hint && <div className="text-sm text-slate-500 mt-1 max-w-md mx-auto">{hint}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
