import { useEffect, useRef, useState, type ReactNode } from 'react'

import { useStore } from '../store'
import { C } from '../theme'
import type { BaseMet, MetResponse, MetSource, MetWindow } from '../types'
import { fmtPct, fmtTime } from '../util'

const DAY = 30 * 60 // forecast spans D-day 00:00 to D+1 06:00

function topRounded(x: number, y: number, w: number, h: number, r: number) {
  const rr = Math.min(r, w / 2, h)
  return `M${x},${y + h} L${x},${y + rr} Q${x},${y} ${x + rr},${y} L${x + w - rr},${y} Q${x + w},${y} ${x + w},${y + rr} L${x + w},${y + h} Z`
}

const visText = (m: number | null | undefined) =>
  m === null || m === undefined ? 'n/a' : m >= 10000 ? `${(m / 1000).toFixed(0)} km` : m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`

/** Hourly P(fog) bars with threshold line and observed-fog ticks (single series: no legend box). */
export function FogChart({ met, threshold, width, height, axis = false, now }: {
  met: BaseMet
  threshold: number
  width: number
  height: number
  axis?: boolean
  now?: number
}) {
  const [tip, setTip] = useState<{ x: number; y: number; i: number } | null>(null)
  const pad = { l: 0, r: axis ? 26 : 0, t: 2, b: (met.observed_vis_m ? 6 : 1) + (axis ? 14 : 0) }
  const pw = width - pad.l - pad.r
  const ph = height - pad.t - pad.b
  const x = (t: number) => pad.l + (t / DAY) * pw
  const y = (p: number) => pad.t + (1 - p) * ph
  const bw = Math.max(1, x(60) - x(0) - 1.5)
  const base = pad.t + ph
  return (
    <div style={{ position: 'relative', width, height }} onPointerLeave={() => setTip(null)}>
      <svg width={width} height={height} role="img" aria-label="Hourly fog probability">
        <line x1={pad.l} x2={pad.l + pw} y1={base} y2={base} stroke={C.axis} />
        {met.times.map((t, i) => {
          const p = met.p_fog[i]
          const h = p * ph
          return h > 0.5 ? (
            <path key={t} d={topRounded(x(t) + 0.75, y(p), bw, h, 1.5)} fill={C.accent} fillOpacity={p >= threshold ? 1 : 0.4} />
          ) : null
        })}
        {met.observed_vis_m?.map((v, i) =>
          v === null ? null : (
            <rect key={`o${i}`} x={x(met.times[i]) + 0.75} y={base + 2} width={bw} height={3} rx={1}
              fill={v < 1000 ? C.ink : C.axis} />
          ),
        )}
        <line x1={pad.l} x2={pad.l + pw} y1={y(threshold)} y2={y(threshold)} stroke={C.ink2} strokeWidth={1} />
        {axis && (
          <>
            <text x={pad.l + pw + 3} y={y(threshold) + 4} fontSize={10} fill={C.ink2}>{fmtPct(threshold)}</text>
            {[0, 360, 720, 1080, 1440].map((t) => (
              <text key={t} x={x(t)} y={height - 2} fontSize={10} fill={C.muted} className="num">{fmtTime(t).slice(0, 2)}h</text>
            ))}
          </>
        )}
        {now !== undefined && now > 0 && now < DAY && (
          <line x1={x(now)} x2={x(now)} y1={pad.t} y2={base} stroke={C.ink} strokeOpacity={0.5} />
        )}
        {met.times.map((t, i) => (
          <rect key={`h${t}`} x={x(t)} y={0} width={x(60) - x(0)} height={height} fill="transparent"
            onPointerMove={(e) => setTip({ x: e.clientX, y: e.clientY, i })} />
        ))}
      </svg>
      {tip && (
        <div className="tooltip" style={{ left: tip.x + 12, top: tip.y + 12 }}>
          <div className="big">P(fog) {fmtPct(met.p_fog[tip.i])}</div>
          <div className="line">{fmtTime(met.times[tip.i])}</div>
          <div className="sub">
            Model visibility {visText(met.nwp_vis_m[tip.i])} · RH {met.rh[tip.i] ?? '-'}% · wind {met.wind_kmh[tip.i] ?? '-'} km/h
          </div>
          {met.observed_vis_m && (
            <div className="sub">Observed ({met.observed_station}): {visText(met.observed_vis_m[tip.i])}</div>
          )}
        </div>
      )}
    </div>
  )
}

/** "2025-11-15..2026-02-15" -> "winter 2025–26" */
const winter = (range: string) => `winter ${range.slice(0, 4)}–${range.slice(14, 16)}`

function windowsFor(met: MetResponse, base: string): MetWindow[] {
  return met.windows.filter((w) => w.base === base)
}

export function useMetForBase(baseId: string): { met: BaseMet | null; windows: MetWindow[]; threshold: number } {
  const resp = useStore((s) => s.met)
  const threshold = useStore((s) => s.metThreshold)
  if (!resp) return { met: null, windows: [], threshold }
  return { met: resp.forecast.bases[baseId] ?? null, windows: windowsFor(resp, baseId), threshold }
}

function Seg<T extends string | number>({ value, options, onChange, label }: {
  value: T
  options: [T, string][]
  onChange: (v: T) => void
  label: string
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map(([v, text]) => (
        <button key={String(v)} aria-pressed={v === value} onClick={() => onChange(v)}>{text}</button>
      ))}
    </div>
  )
}

export function WeatherMenu() {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const met = useStore((s) => s.met)
  const source = useStore((s) => s.metSource)
  const threshold = useStore((s) => s.metThreshold)
  const loading = useStore((s) => s.metLoading)
  const metError = useStore((s) => s.metError)
  const loadMet = useStore((s) => s.loadMet)
  const propose = useStore((s) => s.propose)
  const pending = useStore((s) => !!s.proposal)
  const busy = useStore((s) => s.busy)
  const world = useStore((s) => s.app?.world)

  // Load once when the world arrives; after an error, wait for the user to retry (no request loop).
  useEffect(() => {
    if (world && !met && !loading && !metError) void loadMet()
  }, [world, met, loading, metError, loadMet])

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [open])

  const fogBases = met ? new Set(met.windows.map((w) => w.base)).size : 0
  const rows = met && world
    ? Object.entries(met.forecast.bases)
        .filter(([id]) => world.bases[id])
        .sort((a, b) => Math.max(...b[1].p_fog) - Math.max(...a[1].p_fog))
    : []
  const meta = met?.forecast.model
  let body: ReactNode = <div className="menu-row muted">Loading forecast...</div>
  if (met && world) {
    body = (
      <>
        <div className="menu-row muted" style={{ flexWrap: 'wrap' }}>
          {met.forecast.label} · D-day = {met.forecast.date}
        </div>
        <div className="wx-rows">
          {rows.map(([id, b]) => {
            const ws = windowsFor(met, id)
            const peak = Math.max(...b.p_fog)
            return (
              <div key={id} className="wx-row">
                <div className="wx-name">
                  <b>{world.bases[id].name}</b>
                  {b.observed_station && <span className="muted">obs: {b.observed_station.split(' ')[0]}</span>}
                </div>
                <FogChart met={b} threshold={threshold} width={210} height={b.observed_vis_m ? 30 : 24} now={world.now} />
                <div className="wx-peak num">{fmtPct(peak)}</div>
                <div className="wx-win num">
                  {ws.length ? ws.map((w) => `${fmtTime(w.start)}-${fmtTime(w.end + 1)}`).join(', ') : <span className="muted">open</span>}
                </div>
              </div>
            )
          })}
        </div>
        <div className="menu-row muted" style={{ fontSize: 11, gap: 12, flexWrap: 'wrap' }}>
          <span><span className="swatch" style={{ background: C.accent }} /> P(fog) per hour (faded below threshold)</span>
          <span><span className="swatch" style={{ background: C.ink, height: 3 }} /> observed fog (&lt; 1 km)</span>
          <span><span className="swatch" style={{ background: C.axis, height: 3 }} /> observed clear · no tick: no report</span>
          <span>── threshold</span>
        </div>
        {meta && (
          <div className="menu-row muted" style={{ fontSize: 11, display: 'block' }}>
            MOS model on Open-Meteo NWP: fitted on {meta.n_fit.toLocaleString()} METAR-hours ({meta.train_winters.map(winter).join(', ')}),
            calibrated on {meta.n_calibration.toLocaleString()} ({winter(meta.calibration_winter)}), tested on{' '}
            {meta.n_test.toLocaleString()} unseen hours of {winter(meta.test_winter)}: Brier skill{' '}
            <b className="ink2">{meta.brier_skill >= 0 ? '+' : ''}{(meta.brier_skill * 100).toFixed(0)}%</b> vs climatology, AUC{' '}
            <b className="ink2">{meta.auc.toFixed(2)}</b>. Fog hours detected at P≥50%:{' '}
            <b className="ink2">{fmtPct(meta['mos_at_0.5'].pod)}</b> (raw model visibility: {fmtPct(meta.raw_nwp_vis_below_1km.pod)}).
          </div>
        )}
      </>
    )
  }

  return (
    <div className="menu-wrap" ref={ref}>
      <button className="btn" onClick={() => setOpen(!open)} title="Fog forecast for every base">
        Weather{fogBases ? <span className="wide-only"> · fog at {fogBases}</span> : null} ▾
      </button>
      {open && (
        <div className="menu wx-menu" role="dialog" aria-label="Fog forecast">
          <h4>Fog forecast · P(visibility below 1 km)</h4>
          <div className="menu-row" style={{ justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
            <Seg<MetSource> label="Forecast source" value={source} onChange={(v) => void loadMet(v)}
              options={[['snapshot', 'Cached fog night'], ['live', 'Live (Open-Meteo)']]} />
            <span className="muted" style={{ fontSize: 12 }}>Close base at</span>
            <Seg<number> label="Risk threshold" value={threshold} onChange={(v) => void loadMet(undefined, v)}
              options={[[0.3, '≥30%'], [0.5, '≥50%'], [0.7, '≥70%']]} />
          </div>
          {metError && <div className="menu-row" style={{ color: C.serious, fontSize: 12 }}>⚠ {metError}</div>}
          {body}
          <div className="menu-row" style={{ justifyContent: 'flex-end' }}>
            <button className="btn primary" disabled={!met?.events.length || pending || !!busy || loading}
              onClick={() => {
                if (!met) return
                setOpen(false)
                void propose(met.events, `Met forecast: fog closures at ${fogBases} base${fogBases === 1 ? '' : 's'} (P ≥ ${fmtPct(threshold)})`)
              }}>
              {met?.events.length ? `Propose ${met.events.length} closure${met.events.length === 1 ? '' : 's'}` : 'No new closures at this threshold'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
