import React, { useState } from 'react'
import { Capacitor } from '@capacitor/core'
import { useAuth } from '../auth'
import { getApiBaseUrl, saveApiBaseUrl } from '../api'

export default function LoginPage() {
  const { login, register } = useAuth()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [apiBaseUrl, setApiBaseUrl] = useState(getApiBaseUrl())
  const [serverMessage, setServerMessage] = useState('')

  const saveServer = () => {
    const value = apiBaseUrl.trim()
    if (value) {
      try {
        const parsed = new URL(value)
        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') throw new Error()
      } catch {
        setServerMessage('Enter a valid server URL, such as https://api.example.com')
        return
      }
    }
    saveApiBaseUrl(value)
    setApiBaseUrl(value)
    setServerMessage(value ? 'Server address saved.' : 'Using the default server address.')
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    if (Capacitor.isNativePlatform() && !getApiBaseUrl()) {
      setError('Save the backend server URL above before signing in. For local Android testing, use http://<your-PC-Wi-Fi-IP>:8000.')
      return
    }
    setBusy(true)
    try {
      await (mode === 'login' ? login : register)(email, password)
    } catch (err: any) {
      setError(err.message || 'Authentication failed')
    } finally { setBusy(false) }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="text-5xl mb-3">🛡️</div>
          <h1 className="text-2xl font-bold">AlertIQ</h1>
          <p className="text-sm text-slate-500 mt-1">Intelligent incident monitoring & decision support</p>
        </div>
        <form onSubmit={submit} className="card space-y-4">
          <div className="space-y-1">
            <label className="text-xs text-slate-500 block">Backend server URL</label>
            <div className="flex gap-2">
              <input className="input min-w-0" type="url" value={apiBaseUrl}
                onChange={e => { setApiBaseUrl(e.target.value); setServerMessage('') }}
                placeholder="https://api.example.com" autoCapitalize="none" />
              <button type="button" className="btn-ghost shrink-0" onClick={saveServer}>Save</button>
            </div>
            <p className="text-xs text-slate-500">Set this to a server your phone can reach. Use HTTPS for a deployed app.</p>
            {serverMessage && <p className="text-xs text-slate-400">{serverMessage}</p>}
          </div>
          <div>
            <label className="text-xs text-slate-500 block mb-1">Email</label>
            <input className="input" type="email" required value={email} onChange={e => setEmail(e.target.value)} placeholder="you@company.com" />
          </div>
          <div>
            <label className="text-xs text-slate-500 block mb-1">Password</label>
            <input className="input" type="password" required minLength={8} value={password} onChange={e => setPassword(e.target.value)} placeholder="min. 8 characters" />
          </div>
          {error && <div className="text-sm text-red-400">{error}</div>}
          <button className="btn-primary w-full" disabled={busy}>
            {busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}
          </button>
          <button type="button" className="text-xs text-slate-500 hover:text-slate-300 w-full"
            onClick={() => setMode(mode === 'login' ? 'register' : 'login')}>
            {mode === 'login' ? 'No account yet? Register' : 'Have an account? Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
