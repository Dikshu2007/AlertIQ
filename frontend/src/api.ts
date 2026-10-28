const TOKEN_KEY = 'alertiq_token'
const API_BASE_URL_KEY = 'alertiq_api_base_url'

export function getApiBaseUrl(): string {
  const savedUrl = localStorage.getItem(API_BASE_URL_KEY)
  const defaultUrl = import.meta.env.VITE_API_BASE_URL || ''
  return (savedUrl ?? defaultUrl).trim().replace(/\/+$/, '')
}

export function saveApiBaseUrl(url: string) {
  const normalized = url.trim().replace(/\/+$/, '')
  if (normalized) localStorage.setItem(API_BASE_URL_KEY, normalized)
  else localStorage.removeItem(API_BASE_URL_KEY)
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}
export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token)
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(options.headers as any) }
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json'
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  const resp = await fetch(`${getApiBaseUrl()}${path}`, { ...options, headers })
  if (resp.status === 401 && !path.startsWith('/api/auth/')) {
    clearToken()
    throw new ApiError(401, 'Session expired — please sign in again')
  }
  if (!resp.ok) {
    let detail = `Request failed (${resp.status})`
    try {
      const body = await resp.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
    } catch { /* keep default */ }
    throw new ApiError(resp.status, detail)
  }
  const ct = resp.headers.get('content-type') || ''
  return ct.includes('application/json') ? resp.json() : (resp.text() as any)
}

// ---------------------------------------------------------------- types
export interface Alert {
  id: number; source: string; service_name: string; title: string; description: string
  severity: string; status: string; labels: Record<string, any>; metric_values: Record<string, number>
  timestamp: string
}
export interface Incident {
  id: number; title: string; description: string; severity: string; status: string
  affected_services: string[]; root_cause: string | null; root_cause_confidence: number | null
  recommended_resolution: string | null; correlation_evidence: Record<string, any>
  created_at: string; resolved_at: string | null
}
export interface Anomaly {
  id: number; alert_id: number | null; service_name: string; kind: string; score: number
  confidence: number; method: string; explanation: string; evidence: Record<string, any>
  status: string; created_at: string
}
export interface Integration {
  id: number; name: string; kind: string; config: Record<string, any>; enabled: boolean
  last_status: string | null; last_error: string | null; last_sync_at: string | null
  webhook_token: string | null; has_credentials: boolean; created_at: string
}
export interface Recommendation {
  rank: number; action: string; rationale: string; evidence: string[]
  expected_benefit: string; risk: string; difficulty: string; priority: string
  requires_human_approval: boolean; score: number; source: string
}
export interface Meta { app: string; version: string; demo_mode: boolean; llm_engine: string; web_research_available: boolean }

export const sevColor: Record<string, string> = {
  critical: 'bg-red-500/15 text-red-400',
  high: 'bg-orange-500/15 text-orange-400',
  medium: 'bg-yellow-500/15 text-yellow-400',
  low: 'bg-blue-500/15 text-blue-400',
  info: 'bg-slate-500/15 text-slate-400',
}
export const statusColor: Record<string, string> = {
  open: 'bg-red-500/15 text-red-400', acknowledged: 'bg-yellow-500/15 text-yellow-400',
  resolved: 'bg-emerald-500/15 text-emerald-400', investigating: 'bg-yellow-500/15 text-yellow-400',
  mitigated: 'bg-blue-500/15 text-blue-400',
}
