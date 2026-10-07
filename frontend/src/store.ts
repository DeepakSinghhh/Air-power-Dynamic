import { useMemo } from 'react'
import { create } from 'zustand'

import { api } from './api'
import type { AppState, EngineEvent, Hazard, MetResponse, MetSource, Preset, Proposal, Selection } from './types'

export interface Layers {
  hazard: boolean
  threats: boolean
  routes: boolean
  aircraft: boolean
  labels: boolean
  rivers: boolean
}

export type Tool = null | 'SAM-MR' | 'SAM-LR'

interface Store {
  app: AppState | null
  proposal: Proposal | null
  proposalLabel: string
  hazard: Hazard | null
  presets: Preset[] | null
  busy: { label: string; since: number } | null
  error: string | null
  selection: Selection | null
  hoverMission: string | null
  viewTime: number
  playing: boolean
  speed: number // simulated minutes per real second
  timelineMode: 'missions' | 'aircraft'
  hideIdle: boolean
  layers: Layers
  tool: Tool
  met: MetResponse | null
  metSource: MetSource
  metThreshold: number
  metLoading: boolean
  metError: string | null

  init: () => Promise<void>
  newScenario: (seed: number) => Promise<void>
  replan: () => Promise<void>
  loadPresets: () => Promise<void>
  propose: (events: EngineEvent[], label: string) => Promise<void>
  approve: () => Promise<void>
  reject: () => Promise<void>
  loadHazard: () => Promise<void>
  select: (s: Selection | null) => void
  setHoverMission: (id: string | null) => void
  setViewTime: (t: number) => void
  setPlaying: (p: boolean) => void
  setSpeed: (s: number) => void
  setTimelineMode: (m: 'missions' | 'aircraft') => void
  setHideIdle: (v: boolean) => void
  toggleLayer: (k: keyof Layers) => void
  setTool: (t: Tool) => void
  dismissError: () => void
  loadMet: (source?: MetSource, threshold?: number) => Promise<void>
}

const PLAN_SECONDS = 10

export const useStore = create<Store>((set, get) => {
  async function run<T>(label: string, fn: () => Promise<T>): Promise<T | undefined> {
    if (get().busy) return undefined
    set({ busy: { label, since: performance.now() }, error: null })
    try {
      return await fn()
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) })
      return undefined
    } finally {
      set({ busy: null })
    }
  }

  const commit = (app: AppState) => {
    set((s) => ({ app, proposal: null, presets: null, viewTime: Math.max(s.viewTime, app.world.now) }))
    void get().loadMet()
  }

  return {
    app: null,
    proposal: null,
    proposalLabel: '',
    hazard: null,
    presets: null,
    busy: null,
    error: null,
    selection: null,
    hoverMission: null,
    viewTime: 0,
    playing: false,
    speed: 30,
    timelineMode: 'missions',
    hideIdle: true,
    layers: { hazard: false, threats: true, routes: true, aircraft: true, labels: true, rivers: true },
    tool: null,
    met: null,
    metSource: 'snapshot',
    metThreshold: 0.5,
    metLoading: false,
    metError: null,

    init: async () => {
      await run('Loading operational picture', async () => {
        let app = await api.state()
        set({ app, viewTime: app.world.now })
        if (!app.plan) {
          set({ busy: { label: 'Optimising air tasking plan', since: performance.now() } })
          app = await api.plan(PLAN_SECONDS)
        }
        commit(app)
      })
    },

    newScenario: async (seed) => {
      await run(`Generating scenario ${seed}`, async () => {
        const fresh = await api.scenario(seed)
        set({ app: fresh, proposal: null, selection: null, viewTime: fresh.world.now, hazard: null })
        set({ busy: { label: 'Optimising air tasking plan', since: performance.now() } })
        commit(await api.plan(PLAN_SECONDS))
      })
    },

    replan: async () => {
      await run('Re-optimising full plan', async () => commit(await api.plan(PLAN_SECONDS)))
    },

    loadPresets: async () => {
      const app = get().app
      if (!app?.plan) return
      const at = Math.max(app.world.now, Math.round(get().viewTime / 5) * 5)
      try {
        set({ presets: await api.presets(at) })
      } catch (e) {
        set({ error: e instanceof Error ? e.message : String(e) })
      }
    },

    propose: async (events, label) => {
      set({ tool: null, playing: false })
      const r = await run('Re-optimising with minimal disruption', () => api.propose(events, PLAN_SECONDS))
      if (r) set({ proposal: r, proposalLabel: label, viewTime: Math.max(get().viewTime, r.world.now) })
    },

    approve: async () => {
      const p = get().proposal
      if (!p) return
      const r = await run('Committing retask', () => api.approve(p.id))
      if (r) commit(r)
    },

    reject: async () => {
      const p = get().proposal
      if (!p) return
      const r = await run('Discarding proposal', () => api.reject(p.id))
      if (r) commit(r)
    },

    loadHazard: async () => {
      try {
        set({ hazard: await api.hazard(!!get().proposal) })
      } catch (e) {
        set({ error: e instanceof Error ? e.message : String(e) })
      }
    },

    select: (selection) => set({ selection }),
    setHoverMission: (hoverMission) => set({ hoverMission }),
    setViewTime: (viewTime) => set({ viewTime: Math.max(0, Math.min(viewTime, 1800)) }),
    setPlaying: (playing) => set({ playing }),
    setSpeed: (speed) => set({ speed }),
    setTimelineMode: (timelineMode) => set({ timelineMode }),
    setHideIdle: (hideIdle) => set({ hideIdle }),
    toggleLayer: (k) => set((s) => ({ layers: { ...s.layers, [k]: !s.layers[k] } })),
    setTool: (tool) => set({ tool }),
    dismissError: () => set({ error: null }),

    loadMet: async (source, threshold) => {
      const s = get()
      const src = source ?? s.metSource
      const thr = threshold ?? s.metThreshold
      const at = Math.max(s.app?.world.now ?? 0, Math.round(s.viewTime / 5) * 5)
      set({ metSource: src, metThreshold: thr, metLoading: true, metError: null })
      try {
        set({ met: await api.met(src, thr, at) })
      } catch (e) {
        // Live data can be unreachable (offline venue): keep the last forecast and say why.
        set({ metError: e instanceof Error ? e.message : String(e) })
      } finally {
        set({ metLoading: false })
      }
    },
  }
})

/** What the screens render: the pending proposal if there is one, otherwise the committed plan. */
export function useView() {
  const app = useStore((s) => s.app)
  const proposal = useStore((s) => s.proposal)
  return useMemo(() => {
    if (!app) return null
    const world = proposal?.world ?? app.world
    const plan = proposal?.plan ?? app.plan
    return {
      world,
      plan,
      envelopes: proposal?.envelopes ?? app.envelopes,
      kpis: proposal?.kpis ?? app.kpis,
      // KPI deltas: a proposal is compared with the committed plan, a plan with the manual-style baseline.
      reference: proposal ? app.kpis : app.baseline_kpis,
      referenceLabel: proposal ? 'vs current plan' : app.reference_label,
      previous: proposal ? app.plan : null,
      proposal,
    }
  }, [app, proposal])
}

export type View = NonNullable<ReturnType<typeof useView>>

export function eventTime(): number {
  const { app, viewTime } = useStore.getState()
  return Math.max(app?.world.now ?? 0, Math.round(viewTime / 5) * 5)
}
