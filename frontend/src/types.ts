// Mirrors engine/sarthi/models.py and the /api responses. Times are minutes from 00:00 D-day.
// Note: the engine serialises route points as [lat, lon]; deck.gl wants [lon, lat].

export type Role = 'DCA' | 'STRIKE' | 'SEAD' | 'CAS' | 'ISR' | 'AEW' | 'AAR' | 'AIRLIFT'

export interface TimeWindow { start: number; end: number; reason: string }

export interface Base {
  id: string
  name: string
  lat: number
  lon: number
  closures: TimeWindow[]
  stocks: Record<string, number>
  fighter_reserve: number
}

export interface AircraftType {
  name: string
  roles: Role[]
  speed_kmh: number
  combat_radius_km: number
  prep_min: number
  turnaround_min: number
  weapons: Record<string, number>
  payload_t: number
  aar_receivers: number
  fighter: boolean
}

export interface Aircraft {
  tail: string
  type: string
  base: string
  serviceable: boolean
  p_serviceable: number
  available_from: number
}

export interface Crew {
  id: string
  base: string
  qualified: string
  night_qualified: boolean
  wake_time: number
  sleep_hours: number
  max_sorties: number
  max_flight_min: number
  available: boolean
}

export interface Threat {
  id: string
  kind: string
  lat: number
  lon: number
  radius_km: number
  pk: number
  observed_at: number
  mobile_kmh: number
}

export interface RestrictedZone { id: string; lat: number; lon: number; radius_km: number; reason: string }

export interface Mission {
  id: string
  role: Role
  priority: number
  lat: number
  lon: number
  tot_earliest: number
  tot_latest: number
  on_station_min: number
  package: number
  weapon: string | null
  weapons_per_aircraft: number
  cargo_t: number
  max_risk: number
  depends_on: string | null
  dep_lag_min: number
  dep_lag_max: number
  suppresses: string[]
  label: string
}

export interface World {
  now: number
  horizon: number
  area: [number, number, number, number] // lat0, lat1, lon0, lon1
  bases: Record<string, Base>
  types: Record<string, AircraftType>
  aircraft: Record<string, Aircraft>
  crews: Record<string, Crew>
  threats: Record<string, Threat>
  zones: Record<string, RestrictedZone>
  missions: Record<string, Mission>
  aar_extension: number
  fatigue_threshold: number
}

export interface Sortie {
  tail: string
  base: string
  crew: string | null
  launch: number
  recover: number
  route_km: number
  risk: number
  needs_aar: boolean
  route: [number, number][]
}

export interface Assignment {
  mission: string
  tot: number
  sorties: Sortie[]
  tankers: string[]
  tanker_sorties: Sortie[]
}

export interface Plan {
  assignments: Record<string, Assignment>
  unassigned: Record<string, string[]>
  solver: string
  status: string
  solve_seconds: number
  objective: number
}

export interface Kpis {
  missions_planned: number
  missions_total: number
  priority_weighted_fulfilment: number
  expected_value: number
  sorties: number
  tanker_sorties: number
  mean_sortie_risk: number
  max_sortie_risk: number
  solve_seconds: number
}

export interface Envelope { r_eff_km: number; age_min: number }

export interface HistoryItem {
  id: string
  notes: string[]
  now: number
  decision: 'approved' | 'rejected'
  aircraft_changes?: number
  naive_aircraft_changes?: number | null
}

export interface AppState {
  world: World
  plan: Plan | null
  version: number
  kpis: Kpis | null
  baseline_kpis: Kpis | null
  envelopes: Record<string, Envelope>
  history: HistoryItem[]
}

export type ChangeKind = 'ADDED' | 'DROPPED' | 'MODIFIED'

export interface MissionChange { mission: string; priority: number; change: ChangeKind; details: string[] }

export interface PlanDiff {
  changes: MissionChange[]
  aircraft_changes: number
  crew_changes: number
  tot_shifts: number
  untouched_missions: number
}

export interface Proposal {
  id: string
  notes: string[]
  diff: PlanDiff
  naive_diff: PlanDiff | null
  world: World
  plan: Plan
  kpis: Kpis
  envelopes: Record<string, Envelope>
}

// Events accepted by /api/retask/propose (discriminated by `kind`).
export type EngineEvent =
  | { kind: 'base_closure'; at: number; base: string; start: number; end: number; reason: string }
  | { kind: 'aircraft_down'; at: number; tails: string[]; reason: string }
  | { kind: 'new_threat'; at: number; threat: Threat }
  | { kind: 'new_mission'; at: number; mission: Mission }
  | { kind: 'cancel_mission'; at: number; mission: string }
  | { kind: 'priority_change'; at: number; mission: string; priority: number }
  | { kind: 'stock_loss'; at: number; base: string; weapon: string; qty: number }

export interface Preset { id: string; label: string; detail: string; events: EngineEvent[] }

export interface Hazard {
  lat0: number
  lon0: number
  res: number
  nlat: number
  nlon: number
  max: number
  scale: string
  data: string // base64 uint8, row-major, rows south -> north
}

export type Selection =
  | { kind: 'mission'; id: string }
  | { kind: 'base'; id: string }
  | { kind: 'threat'; id: string }
  | { kind: 'aircraft'; id: string }
