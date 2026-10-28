import React, { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { useAuth } from '../auth'

const NAV = [
  { to: '/', label: 'Overview', icon: '📊' },
  { to: '/alerts', label: 'Alerts', icon: '🚨' },
  { to: '/incidents', label: 'Incidents', icon: '🔥' },
  { to: '/anomalies', label: 'Anomalies', icon: '🧭' },
  { to: '/services', label: 'Services', icon: '🧩' },
  { to: '/assistant', label: 'AI Assistant', icon: '🤖' },
  { to: '/integrations', label: 'Integrations', icon: '🔌' },
]

export default function Layout({ children }: { children: React.ReactNode }) {
  const { meta, logout } = useAuth()
  const [open, setOpen] = useState(false)
  const loc = useLocation()
  useEffect(() => setOpen(false), [loc.pathname])

  return (
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <aside className={`fixed inset-y-0 left-0 z-40 w-60 bg-surface-raised border-r border-surface-border flex flex-col transition-transform lg:translate-x-0 lg:static ${open ? 'translate-x-0' : '-translate-x-full'}`}>
        <div className="px-5 py-5 flex items-center gap-2">
          <span className="text-2xl">🛡️</span>
          <div>
            <div className="font-bold text-lg leading-none">AlertIQ</div>
            <div className="text-xs text-slate-500">Incident Intelligence</div>
          </div>
        </div>
        <nav className="flex-1 px-3 space-y-1">
          {NAV.map(n => (
            <NavLink key={n.to} to={n.to}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-lg text-sm ${isActive ? 'bg-accent/15 text-accent-soft font-semibold' : 'text-slate-400 hover:bg-surface-border hover:text-slate-200'}`}>
              <span>{n.icon}</span>{n.label}
            </NavLink>
          ))}
        </nav>
        <div className="px-4 py-4 border-t border-surface-border text-xs text-slate-500 space-y-2">
          {meta && (
            <div className="space-y-1">
              <div>AI engine: <span className="text-slate-300">{meta.llm_engine === 'none' ? 'local rules' : meta.llm_engine}</span></div>
              <div>Web research: <span className={meta.web_research_available ? 'text-emerald-400' : 'text-slate-500'}>{meta.web_research_available ? 'enabled' : 'not configured'}</span></div>
              {meta.demo_mode && <div className="badge bg-purple-500/15 text-purple-300">DEMO MODE — sample data</div>}
            </div>
          )}
          <button onClick={logout} className="btn-ghost w-full text-left">Sign out</button>
        </div>
      </aside>
      {open && <div className="fixed inset-0 bg-black/50 z-30 lg:hidden" onClick={() => setOpen(false)} />}

      {/* Main */}
      <main className="flex-1 min-w-0">
        <header className="lg:hidden sticky top-0 z-20 bg-surface-raised border-b border-surface-border px-4 py-3 flex items-center justify-between">
          <button onClick={() => setOpen(!open)} className="btn-ghost">☰</button>
          <span className="font-bold">AlertIQ</span>
        </header>
        <div className="p-4 md:p-6 max-w-7xl mx-auto">{children}</div>
      </main>
    </div>
  )
}
