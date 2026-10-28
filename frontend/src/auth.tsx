import React, { createContext, useContext, useEffect, useState } from 'react'
import { api, clearToken, getToken, setToken, Meta } from './api'

interface AuthCtx {
  token: string | null
  meta: Meta | null
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string) => Promise<void>
  logout: () => void
}
const Ctx = createContext<AuthCtx>(null as any)
export const useAuth = () => useContext(Ctx)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setTok] = useState<string | null>(getToken())
  const [meta, setMeta] = useState<Meta | null>(null)

  useEffect(() => {
    if (!token) return
    api<Meta>('/api/meta').then(setMeta).catch(() => clearToken())
  }, [token])

  const doAuth = async (path: string, email: string, password: string) => {
    const data = await api<{ access_token: string }>(path, {
      method: 'POST', body: JSON.stringify({ email, password }),
    })
    setToken(data.access_token)
    setTok(data.access_token)
  }
  return (
    <Ctx.Provider value={{
      token, meta,
      login: (e, p) => doAuth('/api/auth/login', e, p),
      register: (e, p) => doAuth('/api/auth/register', e, p),
      logout: () => { clearToken(); setTok(null); setMeta(null) },
    }}>
      {children}
    </Ctx.Provider>
  )
}
