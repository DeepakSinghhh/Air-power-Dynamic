import type { Role } from './types'

// Dark operations-room theme. Chrome/ink values follow the validated reference palette;
// the three mission-family hues are categorical slots 1-3 (validated all-pairs on the dark
// surface, since any two map markers can sit side by side). Status hues are reserved.
export const C = {
  page: '#0d0d0d',
  surface: '#1a1a19',
  surface2: '#222220',
  ink: '#ffffff',
  ink2: '#c3c2b7',
  muted: '#898781',
  grid: '#2c2c2a',
  axis: '#383835',
  water: '#0f1011',
  land: '#151514',
  india: '#252523',
  border: '#4a4944',
  indiaBorder: '#a3a298',
  river: '#1f2a33',
  good: '#0ca30c',
  warning: '#fab219',
  serious: '#ec835a',
  critical: '#d03b3b',
  accent: '#3987e5',
  accentInk: '#86b6ef', // view-time cursor (light step of the blue ramp, readable on dark)
} as const

export type Family = 'counter' | 'offensive' | 'support'

export const FAMILY_COLOR: Record<Family, string> = {
  counter: '#3987e5',   // slot 1 blue
  offensive: '#d95926', // slot 2 orange
  support: '#199e70',   // slot 3 aqua
}

export const FAMILY_LABEL: Record<Family, string> = {
  counter: 'Counter-air & AEW',
  offensive: 'Strike, SEAD & CAS',
  support: 'ISR, tanker & airlift',
}

export const ROLE_FAMILY: Record<Role, Family> = {
  DCA: 'counter',
  AEW: 'counter',
  STRIKE: 'offensive',
  SEAD: 'offensive',
  CAS: 'offensive',
  ISR: 'support',
  AAR: 'support',
  AIRLIFT: 'support',
}

export const ROLE_NAME: Record<Role, string> = {
  DCA: 'Defensive counter-air',
  AEW: 'Airborne early warning',
  STRIKE: 'Strike',
  SEAD: 'Suppression of enemy air defences',
  CAS: 'Close air support',
  ISR: 'ISR',
  AAR: 'Air-to-air refuelling',
  AIRLIFT: 'Airlift',
}

export type RGBA = [number, number, number, number]

export function rgba(hex: string, alpha = 1): RGBA {
  const n = parseInt(hex.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255, Math.round(alpha * 255)]
}

export const roleColor = (role: Role) => FAMILY_COLOR[ROLE_FAMILY[role]]

export type BaseStatus = 'open' | 'closing' | 'closed'

export const STATUS_COLOR: Record<BaseStatus, string> = { open: C.good, closing: C.warning, closed: C.critical }
export const STATUS_LABEL: Record<BaseStatus, string> = { open: 'Open', closing: 'Closure ahead', closed: 'Closed' }
export const STATUS_ICON: Record<BaseStatus, string> = { open: '●', closing: '▲', closed: '✕' }
