import type { AppState, CoaResponse, CopilotReply, CopilotStatus, EngineEvent, Hazard, MetResponse, MetSource, Preset, Proposal, ReadinessResponse, RobustnessResponse, ScenarioKind, WhatIfResponse } from './types'

/** One engine session per browser tab, so visitors to a shared server do not see each other's changes. */
const SESSION = (() => {
  const make = () =>
    (globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`)
      .replace(/[^A-Za-z0-9-]/g, '')
  try {
    let id = sessionStorage.getItem('sarthi-session')
    if (!id) {
      id = make()
      sessionStorage.setItem('sarthi-session', id)
    }
    return id
  } catch {
    return make() // storage blocked (private mode, embedded frame): a session for this page load
  }
})()

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', 'x-sarthi-session': SESSION, ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* keep statusText */
    }
    throw new Error(`${res.status}: ${detail}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  state: () => call<AppState>('/state'),
  scenario: (seed: number, kind: ScenarioKind = 'conflict') =>
    call<AppState>('/scenario', { method: 'POST', body: JSON.stringify({ seed, kind }) }),
  plan: (timeLimit = 10) => call<AppState>(`/plan?time_limit=${timeLimit}`, { method: 'POST' }),
  hazard: (proposal: boolean) => call<Hazard>(`/hazard${proposal ? '?proposal=true' : ''}`),
  presets: (at: number) => call<Preset[]>(`/presets?at=${Math.round(at)}`),
  met: (source: MetSource, threshold: number, at: number) =>
    call<MetResponse>(`/met?source=${source}&threshold=${threshold}&at=${Math.round(at)}`),
  propose: (events: EngineEvent[], timeLimit = 10) =>
    call<Proposal>('/retask/propose', { method: 'POST', body: JSON.stringify({ events, time_limit: timeLimit }) }),
  coas: (timeLimit = 6) => call<CoaResponse>(`/coa?time_limit=${timeLimit}`, { method: 'POST' }),
  coaPropose: (id: string) => call<Proposal>(`/coa/${id}/propose`, { method: 'POST' }),
  whatIf: (mission: string, timeLimit = 3) =>
    call<WhatIfResponse>(`/whatif/${encodeURIComponent(mission)}?time_limit=${timeLimit}`, { method: 'POST' }),
  readiness: (at: number, proposal: boolean) => call<ReadinessResponse>(`/readiness?at=${Math.round(at)}${proposal ? '&proposal=true' : ''}`),
  copilot: (text: string) => call<CopilotReply>('/copilot', { method: 'POST', body: JSON.stringify({ text }) }),
  copilotStatus: () => call<CopilotStatus>('/copilot/status'),
  health: () => call<{ ok: boolean; cpus: number; time_scale: number }>('/health'),
  robustness: (runs = 2000) => call<RobustnessResponse>(`/robustness?runs=${runs}`),
  sparesPropose: () => call<Proposal>('/robustness/propose', { method: 'POST' }),
  approve: (id: string) => call<AppState>(`/retask/${id}/approve`, { method: 'POST' }),
  reject: (id: string) => call<AppState>(`/retask/${id}/reject`, { method: 'POST' }),
}
