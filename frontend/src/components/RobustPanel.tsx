import { useEffect, useState, type ReactNode } from 'react'

import { useStore } from '../store'
import { C } from '../theme'
import type { FailCause, StressResult } from '../types'
import { fmtPct, fmtTime } from '../util'

const CAUSE_LABEL: Record<FailCause, string> = {
  serviceability: 'U/S at start-up',
  tanker: 'tanker no-show',
  dependency: 'its SEAD failed',
  attrition: 'lost before target',
}

/** Outcome distribution: share of simulated days by mission success, current plan vs with spares. */
function Distribution({ cur, hard }: { cur: StressResult; hard: StressResult | null }) {
  const [tip, setTip] = useState<{ x: number; y: number; i: number } | null>(null)
  const W = 560, H = 220, pad = { l: 40, r: 12, t: 26, b: 34 }
  const series = [cur, ...(hard ? [hard] : [])]
  const nz = series.flatMap((s) => s.hist.map((n, i) => (n > 0 ? i : -1)).filter((i) => i >= 0))
  const i0 = Math.max(0, Math.min(...nz) - 1)
  const i1 = Math.min(cur.hist.length, Math.max(...nz) + 2)
  const bw = cur.bin_width
  const share = (s: StressResult, i: number) => s.hist[i] / s.runs
  const maxY = Math.max(...series.flatMap((s) => s.hist.slice(i0, i1).map((n) => n / s.runs))) * 1.15
  const x = (v: number) => pad.l + ((v - i0 * bw) / ((i1 - i0) * bw)) * (W - pad.l - pad.r)
  const y = (v: number) => pad.t + (1 - v / maxY) * (H - pad.t - pad.b)
  const xt: number[] = []
  for (let v = Math.ceil((i0 * bw) / 0.1) * 0.1; v <= i1 * bw + 1e-9; v += 0.1) xt.push(Math.round(v * 10) / 10)
  const yStep = maxY > 0.2 ? 0.1 : 0.05
  const yt: number[] = []
  for (let v = 0; v <= maxY; v += yStep) yt.push(Math.round(v * 100) / 100)
  const step = (s: StressResult) => {
    let d = `M${x(i0 * bw)},${y(0)}`
    for (let i = i0; i < i1; i++) d += ` L${x(i * bw)},${y(share(s, i))} L${x((i + 1) * bw)},${y(share(s, i))}`
    return `${d} L${x(i1 * bw)},${y(0)}`
  }
  const marker = (v: number, label: string, color: string, row: number) => (
    <g key={label}>
      <line x1={x(v)} x2={x(v)} y1={pad.t - 2} y2={H - pad.b} stroke={color} strokeDasharray="3 3" strokeWidth={1.5} />
      <text x={x(v) + 4} y={pad.t - 14 + row * 11} fontSize={10} fill={C.ink2}>{label}</text>
    </g>
  )
  return (
    <div style={{ position: 'relative' }} onPointerLeave={() => setTip(null)}>
      <div className="legend-inline">
        <span><i className="sw-bar" /> Current plan</span>
        {hard && <span><i className="sw-line" style={{ background: C.accent }} /> With ground spares</span>}
        <span className="muted">dashed: worst 5% of days (p05)</span>
      </div>
      <svg width={W} height={H} role="img" aria-label="Distribution of mission success over simulated days">
        {yt.map((v) => (
          <g key={`y${v}`}>
            <line x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} stroke={C.grid} />
            <text x={pad.l - 6} y={y(v) + 4} fontSize={10} fill={C.muted} textAnchor="end" className="num">{fmtPct(v)}</text>
          </g>
        ))}
        {cur.hist.slice(i0, i1).map((n, k) => {
          const i = i0 + k
          return n > 0 ? (
            <rect key={i} x={x(i * bw) + 1} y={y(n / cur.runs)} width={Math.max(1, x((i + 1) * bw) - x(i * bw) - 2)}
              height={y(0) - y(n / cur.runs)} fill={C.ink2} fillOpacity={0.35} />
          ) : null
        })}
        {hard && <path d={step(hard)} fill="none" stroke={C.accent} strokeWidth={2} />}
        <line x1={pad.l} x2={W - pad.r} y1={y(0)} y2={y(0)} stroke={C.axis} />
        {xt.map((v) => (
          <text key={`x${v}`} x={x(v)} y={H - pad.b + 14} fontSize={10} fill={C.muted} textAnchor="middle" className="num">{fmtPct(v)}</text>
        ))}
        <text x={(pad.l + W - pad.r) / 2} y={H - 4} fontSize={11} fill={C.ink2} textAnchor="middle">
          Mission success on the day (priority-weighted) →
        </text>
        {marker(cur.p05, `p05 ${fmtPct(cur.p05)} now`, C.ink2, 0)}
        {hard && marker(hard.p05, `p05 ${fmtPct(hard.p05)} with spares`, C.accent, 1)}
        {Array.from({ length: i1 - i0 }, (_, k) => i0 + k).map((i) => (
          <rect key={`h${i}`} x={x(i * bw)} y={pad.t} width={x((i + 1) * bw) - x(i * bw)} height={H - pad.t - pad.b}
            fill="transparent" onPointerMove={(e) => setTip({ x: e.clientX, y: e.clientY, i })} />
        ))}
      </svg>
      {tip && (
        <div className="tooltip" style={{ left: tip.x + 14, top: tip.y + 12 }}>
          <div className="big">{fmtPct(share(cur, tip.i), 1)} of days</div>
          <div className="sub">
            {fmtPct(tip.i * bw)}-{fmtPct((tip.i + 1) * bw)} success · current plan
            {hard ? ` · with spares ${fmtPct(share(hard, tip.i), 1)}` : ''}
          </div>
        </div>
      )}
    </div>
  )
}

function Stat({ label, now, after }: { label: string; now: number; after?: number }) {
  return (
    <div className="stat">
      <b className="num">
        {fmtPct(now, 1)}
        {after !== undefined && <span style={{ color: C.accentInk }}> → {fmtPct(after, 1)}</span>}
      </b>
      <span>{label}</span>
    </div>
  )
}

export default function RobustPanel() {
  const open = useStore((s) => s.robustOpen)
  const setOpen = useStore((s) => s.setRobustOpen)
  const data = useStore((s) => s.robust)
  const load = useStore((s) => s.loadRobust)
  const proposeSpares = useStore((s) => s.proposeSpares)
  const whatIf = useStore((s) => s.whatIfLost)
  const select = useStore((s) => s.select)
  const busy = useStore((s) => s.busy)
  const pending = useStore((s) => !!s.proposal)
  const now = useStore((s) => s.app?.world.now ?? 0)

  const [tried, setTried] = useState(false)
  useEffect(() => {
    if (!open) return setTried(false)
    if (!data && !busy && !tried) {
      setTried(true)
      void load()
    }
  }, [open, data, busy, tried, load])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, setOpen])

  if (!open) return null
  let body: ReactNode = <div className="empty">Executing the plan 2,000 times...</div>
  if (data) {
    const { current: cur, hardened: hard } = data
    body = (
      <>
        <div className="robust-grid">
          <Distribution cur={cur} hard={hard} />
          <div>
            <div className="stat-row" style={{ marginTop: 0 }}>
              <Stat label="expected (mean day)" now={cur.mean} after={hard?.mean} />
              <Stat label="bad day (p05)" now={cur.p05} after={hard?.p05} />
            </div>
            <div className="stat-row" style={{ marginTop: 0 }}>
              <Stat label="typical day (p50)" now={cur.p50} after={hard?.p50} />
              <Stat label="good day (p95)" now={cur.p95} after={hard?.p95} />
            </div>
            {hard ? (
              <div className="robust-action">
                <p>
                  <b>Hold {hard.spares} ground spares</b> for {hard.missions_with_spares} missions using aircraft that are idle anyway.
                  No mission, flying aircraft, crew or TOT changes. A spare starts up with its package and launches if a primary is
                  unserviceable; the crew walks to it.
                </p>
                <button className="btn primary" disabled={!!busy || pending} onClick={() => void proposeSpares()}>
                  Propose ground spares → review
                </button>
              </div>
            ) : (
              <div className="robust-action">
                <p>
                  <b>Ground spares are held:</b> {cur.spares} spares on {cur.missions_with_spares} missions. They are re-chosen after
                  every retask, and a spare is the first substitute when a primary goes unserviceable.
                </p>
              </div>
            )}
          </div>
        </div>
        <div className="robust-cols">
          <div>
            <h5>What fails most</h5>
            <p className="muted robust-note">Share of simulated days the mission fails, and the first thing that went wrong.</p>
            {cur.fragile.length === 0 && <p className="muted">Nothing fails in simulation.</p>}
            {cur.fragile.map((f) => (
              <div key={f.mission} className="robust-row" onClick={() => { setOpen(false); select({ kind: 'mission', id: f.mission }) }}>
                <div className="robust-row-head">
                  <b>{f.mission}</b> <span className="prio">P{f.priority}</span>
                  <span className="num" style={{ marginLeft: 'auto' }}>fails {fmtPct(f.p_fail)}</span>
                </div>
                <span className="meter wide"><i style={{ width: `${f.p_fail * 100}%` }} /></span>
                <div className="muted robust-cause">
                  {(Object.keys(CAUSE_LABEL) as FailCause[])
                    .filter((k) => f.causes[k] >= 0.005)
                    .sort((a, b) => f.causes[b] - f.causes[a])
                    .map((k) => `${CAUSE_LABEL[k]} ${fmtPct(f.causes[k])}`)
                    .join(' · ')}
                </div>
              </div>
            ))}
          </div>
          <div>
            <h5>Single points of failure</h5>
            <p className="muted robust-note">
              If this asset goes unserviceable from {fmtTime(now)} and nothing is replanned, these missions fail (SEAD lost means
              its strike is lost too). <b className="ink2">What if?</b> asks the engine to recover.
            </p>
            {cur.single_points.length === 0 && <p className="muted">No single asset carries a mission alone.</p>}
            {cur.single_points.map((sp) => (
              <div key={sp.asset} className="robust-row static">
                <div className="robust-row-head">
                  <b>{sp.asset}</b>
                  <span className="muted">{sp.tanker ? 'tanker' : sp.type}</span>
                  <span className="num" style={{ marginLeft: 'auto' }}>−{(sp.value * 100).toFixed(1)} pts</span>
                </div>
                <div className="robust-cause">{sp.missions.join(', ')}</div>
                <button className="btn small" disabled={!!busy || pending} onClick={() => void whatIf(sp.asset)}>
                  What if? ▸
                </button>
              </div>
            ))}
          </div>
        </div>
      </>
    )
  }
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && setOpen(false)}>
      <div className="modal" role="dialog" aria-label="Robustness">
        <div className="modal-head">
          <div>
            <h2>Robustness</h2>
            <div className="muted" style={{ fontSize: 12, maxWidth: 820 }}>
              The plan as committed at {fmtTime(now)}, executed {data?.current.runs.toLocaleString() ?? '2,000'} times with no
              replanning: aircraft unserviceable at start-up (maintenance model), losses before the target, tanker no-shows,
              and a strike aborts if its SEAD failed. The mean equals the Expected value tile.
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn small" disabled={!!busy} onClick={() => void load()}>Recompute</button>
            <button className="btn small" onClick={() => setOpen(false)} aria-label="Close">Close</button>
          </div>
        </div>
        {body}
        {pending && <p className="muted" style={{ fontSize: 11, margin: '10px 0 0' }}>Approve or reject the pending proposal first.</p>}
      </div>
    </div>
  )
}
