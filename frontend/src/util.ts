import type { Assignment, Base, Mission, Sortie, World } from './types'
import type { BaseStatus } from './theme'

// ---------- time ----------

export function fmtTime(t: number): string {
  const m = Math.round(t)
  const day = Math.floor(m / 1440)
  const r = ((m % 1440) + 1440) % 1440
  const s = `${String(Math.floor(r / 60)).padStart(2, '0')}:${String(r % 60).padStart(2, '0')}`
  return day > 0 ? `${s}+${day}` : day < 0 ? `${s}-${-day}` : s
}

export const fmtPct = (x: number, digits = 0) => `${(x * 100).toFixed(digits)}%`

export function fmtDur(min: number): string {
  const h = Math.floor(min / 60)
  const m = Math.round(min % 60)
  return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m`
}

// ---------- geometry ----------

export type LonLat = [number, number]

const KM_PER_DEG = 111.32

export function circle(lat: number, lon: number, km: number, n = 72): LonLat[] {
  const pts: LonLat[] = []
  const kx = KM_PER_DEG * Math.cos((lat * Math.PI) / 180)
  for (let i = 0; i <= n; i++) {
    const a = (i / n) * 2 * Math.PI
    pts.push([lon + (km / kx) * Math.sin(a), lat + (km / KM_PER_DEG) * Math.cos(a)])
  }
  return pts
}

export function haversineKm(a: LonLat, b: LonLat): number {
  const R = 6371.0088
  const [lon1, lat1, lon2, lat2] = [a[0], a[1], b[0], b[1]].map((d) => (d * Math.PI) / 180)
  const h = Math.sin((lat2 - lat1) / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin((lon2 - lon1) / 2) ** 2
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)))
}

/** Bearing in degrees clockwise from north. */
export function bearing(a: LonLat, b: LonLat): number {
  const dx = (b[0] - a[0]) * Math.cos((((a[1] + b[1]) / 2) * Math.PI) / 180)
  const dy = b[1] - a[1]
  return ((Math.atan2(dx, dy) * 180) / Math.PI + 360) % 360
}

/** Engine route ([lat, lon] pairs, base -> target) to deck.gl path ending exactly on the objective. */
export function routePath(s: Sortie, m: Mission | undefined, base: Base | undefined): LonLat[] {
  const pts: LonLat[] = s.route.map(([lat, lon]) => [lon, lat])
  if (base) pts[0] = [base.lon, base.lat]
  if (m && pts.length) pts[pts.length - 1] = [m.lon, m.lat]
  return pts
}

/** Point at fraction f (0..1) along a polyline, with heading. */
export function along(path: LonLat[], f: number): { pos: LonLat; heading: number } {
  if (path.length < 2) return { pos: path[0] ?? [0, 0], heading: 0 }
  const seg: number[] = []
  let total = 0
  for (let i = 1; i < path.length; i++) {
    const d = haversineKm(path[i - 1], path[i])
    seg.push(d)
    total += d
  }
  let target = Math.min(Math.max(f, 0), 1) * total
  for (let i = 0; i < seg.length; i++) {
    if (target <= seg[i] || i === seg.length - 1) {
      const t = seg[i] ? target / seg[i] : 0
      const a = path[i]
      const b = path[i + 1]
      return { pos: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], heading: bearing(a, b) }
    }
    target -= seg[i]
  }
  return { pos: path[path.length - 1], heading: 0 }
}

export type Phase = 'start-up' | 'ingress' | 'on station' | 'egress'

export interface Flight {
  key: string
  tail: string
  mission: string
  pos: LonLat
  heading: number
  phase: Phase
  tanker: boolean
}

const ORBIT_KM = 18

/** Where an aircraft is at time t (null if on the ground or not yet launched). */
export function flightAt(
  world: World,
  a: Assignment,
  s: Sortie,
  t: number,
  tanker = false,
): Flight | null {
  if (t < s.launch || t > s.recover) return null
  const m = world.missions[a.mission]
  const base = world.bases[s.base]
  const type = world.types[world.aircraft[s.tail]?.type]
  if (!m || !base || !type) return null
  const prep = type.prep_min
  const path: LonLat[] = tanker
    ? s.route.map(([lat, lon]) => [lon, lat] as LonLat)
    : routePath(s, m, base)
  const transit = tanker ? (s.route_km / type.speed_kmh) * 60 : a.tot - s.launch - prep
  const arrive = s.launch + prep + transit
  const depart = tanker ? s.recover - transit : a.tot + m.on_station_min
  const key = `${a.mission}|${s.tail}${tanker ? '|aar' : ''}`
  const base_ = { key, tail: s.tail, mission: a.mission, tanker }
  if (t < s.launch + prep) return { ...base_, pos: path[0], heading: 0, phase: 'start-up' }
  if (t < arrive) return { ...base_, ...along(path, (t - s.launch - prep) / Math.max(transit, 1)), phase: 'ingress' }
  if (t < depart) {
    const end = path[path.length - 1]
    const loiter = tanker || ['DCA', 'AEW', 'ISR', 'CAS'].includes(m.role)
    if (!loiter) return { ...base_, pos: end, heading: bearing(path[path.length - 2] ?? end, end), phase: 'on station' }
    const ang = ((t - arrive) / 12) * 2 * Math.PI // one orbit every 12 minutes
    const kx = KM_PER_DEG * Math.cos((end[1] * Math.PI) / 180)
    const pos: LonLat = [end[0] + (ORBIT_KM / kx) * Math.sin(ang), end[1] + (ORBIT_KM / KM_PER_DEG) * Math.cos(ang)]
    return { ...base_, pos, heading: ((ang * 180) / Math.PI + 90) % 360, phase: 'on station' }
  }
  const back = [...path].reverse()
  return { ...base_, ...along(back, (t - depart) / Math.max(s.recover - depart, 1)), phase: 'egress' }
}

// ---------- status ----------

export function baseStatus(b: Base, t: number): { status: BaseStatus; text: string } {
  const now = b.closures.find((c) => c.start <= t && t <= c.end)
  if (now) return { status: 'closed', text: `Closed until ${fmtTime(now.end)} · ${now.reason}` }
  const next = b.closures.filter((c) => c.start > t).sort((x, y) => x.start - y.start)[0]
  if (next) return { status: 'closing', text: `Closes ${fmtTime(next.start)}-${fmtTime(next.end)} · ${next.reason}` }
  return { status: 'open', text: 'Open' }
}

export function serviceableAt(world: World, baseId: string) {
  const all = Object.values(world.aircraft).filter((a) => a.base === baseId)
  return { total: all.length, serviceable: all.filter((a) => a.serviceable).length }
}

/** Missions sorted for display: planned by TOT, then unplanned by window. */
export function missionOrder(world: World, assignments: Record<string, Assignment>): string[] {
  return Object.values(world.missions)
    .sort((a, b) => {
      const ta = assignments[a.id]?.tot ?? a.tot_earliest
      const tb = assignments[b.id]?.tot ?? b.tot_earliest
      return ta - tb || b.priority - a.priority
    })
    .map((m) => m.id)
}

export function maxRisk(a: Assignment | undefined): number {
  return a ? Math.max(0, ...a.sorties.map((s) => s.risk)) : 0
}

export const sortieKey = (mission: string, tail: string) => `${mission}|${tail}`

/** Fighters that must stay on the ground at a base (mirrors World.reserve in the engine). */
export function reserveAt(world: World, baseId: string): number {
  const b = world.bases[baseId]
  const it = world.intent
  const fixed = b.fighter_reserve + (b.fighter_reserve > 0 ? it.reserve_extra : 0)
  if (!it.reserve_fraction) return fixed
  const n = Object.values(world.aircraft).filter((a) => a.base === baseId && a.serviceable && world.types[a.type].fighter).length
  return Math.max(fixed, Math.ceil(it.reserve_fraction * n))
}
