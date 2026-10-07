import { useEffect, useState, type ReactNode } from 'react'

import { useStore } from '../store'
import { C } from '../theme'
import type { Coa, Intent } from '../types'
import { fmtPct, fmtTime } from '../util'

type Better = 'up' | 'down' | null

interface Row {
  label: string
  hint?: string
  get: (c: Coa) => number
  fmt: (v: number, c: Coa) => string
  better: Better
}

const ROWS: Row[] = [
  { label: 'Mission fulfilment', hint: 'priority-weighted', get: (c) => c.kpis.priority_weighted_fulfilment, fmt: (v) => fmtPct(v, 1), better: 'up' },
  { label: 'Expected value', hint: 'after serviceability and attrition', get: (c) => c.kpis.expected_value, fmt: (v) => fmtPct(v, 1), better: 'up' },
  { label: 'Missions planned', get: (c) => c.kpis.missions_planned, fmt: (v, c) => `${v} / ${c.kpis.missions_total}`, better: 'up' },
  { label: 'Expected aircraft losses', hint: 'sum of sortie loss probabilities', get: (c) => c.metrics.expected_losses, fmt: (v) => v.toFixed(1), better: 'down' },
  { label: 'Worst sortie risk', get: (c) => c.kpis.max_sortie_risk, fmt: (v) => fmtPct(v), better: 'down' },
  { label: 'Sorties flown', get: (c) => c.kpis.sorties, fmt: (v) => String(v), better: null },
  { label: 'Guided weapons expended', get: (c) => c.metrics.munitions_total, fmt: (v) => String(v), better: 'down' },
  { label: 'Fighters on the ground at peak', hint: 'surge capacity left', get: (c) => c.metrics.min_fighters_on_ground, fmt: (v, c) => `${v} / ${c.metrics.fighters_total}`, better: 'up' },
  { label: 'Robustness', hint: 'p05 fulfilment over 1,000 simulated days', get: (c) => c.robustness_p05, fmt: (v) => fmtPct(v), better: 'up' },
  { label: 'Aircraft reassigned vs current plan', get: (c) => c.diff.aircraft_changes, fmt: (v) => String(v), better: 'down' },
]

function intentChips(i: Intent): string[] {
  const out: string[] = []
  if (i.risk_scale !== 1) out.push(`risk ceilings ×${i.risk_scale}`)
  if (i.loss_weight) out.push(`each expected loss costs ${i.loss_weight} priority pts`)
  if (i.offensive_floor) out.push(`strike/SEAD only P${i.offensive_floor}+`)
  if (i.reserve_fraction) out.push(`${fmtPct(i.reserve_fraction)} of fighters held`)
  if (i.reserve_extra) out.push(`+${i.reserve_extra} alert fighters/base`)
  if (i.munitions_weight) out.push('conserve guided weapons')
  if (i.sortie_cost) out.push('economy of sorties')
  return out.length ? out : ['tasked risk ceilings, no extra reserve']
}

/** Effect vs expected losses: one labelled point per COA (identity by label, not colour). */
function TradeOff({ coas, current }: { coas: Coa[]; current: string }) {
  const [tip, setTip] = useState<{ x: number; y: number; c: Coa } | null>(null)
  const W = 340, H = 250, pad = { l: 44, r: 18, t: 14, b: 38 }
  const maxL = Math.max(1, ...coas.map((c) => c.metrics.expected_losses)) * 1.2
  const minF = Math.max(0, Math.floor((Math.min(...coas.map((c) => c.kpis.priority_weighted_fulfilment)) - 0.08) * 10) / 10)
  const x = (v: number) => pad.l + (v / maxL) * (W - pad.l - pad.r)
  const y = (v: number) => pad.t + (1 - (v - minF) / (1 - minF)) * (H - pad.t - pad.b)
  const xt = [0, maxL / 3, (2 * maxL) / 3, maxL].map((v) => Math.round(v * 10) / 10)
  const yt: number[] = []
  for (let v = minF; v <= 1.0001; v += minF < 0.5 ? 0.2 : 0.1) yt.push(Math.round(v * 10) / 10)
  return (
    <div style={{ position: 'relative' }} onPointerLeave={() => setTip(null)}>
      <svg width={W} height={H} role="img" aria-label="Mission effect against expected aircraft losses">
        {yt.map((v) => (
          <g key={`y${v}`}>
            <line x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} stroke={C.grid} />
            <text x={pad.l - 6} y={y(v) + 4} fontSize={10} fill={C.muted} textAnchor="end" className="num">{fmtPct(v)}</text>
          </g>
        ))}
        {xt.map((v) => (
          <text key={`x${v}`} x={x(v)} y={H - pad.b + 14} fontSize={10} fill={C.muted} textAnchor="middle" className="num">{v}</text>
        ))}
        <line x1={pad.l} x2={W - pad.r} y1={H - pad.b} y2={H - pad.b} stroke={C.axis} />
        <text x={(pad.l + W - pad.r) / 2} y={H - 6} fontSize={11} fill={C.ink2} textAnchor="middle">Expected aircraft losses →</text>
        <text x={12} y={pad.t + (H - pad.t - pad.b) / 2} fontSize={11} fill={C.ink2} textAnchor="middle"
          transform={`rotate(-90 12 ${pad.t + (H - pad.t - pad.b) / 2})`}>Mission fulfilment →</text>
        {coas.map((c) => {
          const cx = x(c.metrics.expected_losses)
          const cy = y(c.kpis.priority_weighted_fulfilment)
          const right = cx < W - 120
          const cur = c.intent.name === current
          return (
            <g key={c.id} onPointerMove={(e) => setTip({ x: e.clientX, y: e.clientY, c })}>
              <circle cx={cx} cy={cy} r={14} fill="transparent" />
              {cur && <circle cx={cx} cy={cy} r={10} fill="none" stroke={C.accent} strokeWidth={2} />}
              <circle cx={cx} cy={cy} r={6} fill={C.ink} stroke={C.surface} strokeWidth={2} />
              <text x={right ? cx + 12 : cx - 12} y={cy + 4} fontSize={12} fill={C.ink} textAnchor={right ? 'start' : 'end'}>{c.name}</text>
            </g>
          )
        })}
      </svg>
      {tip && (
        <div className="tooltip" style={{ left: tip.x + 12, top: tip.y + 12 }}>
          <div className="big">{fmtPct(tip.c.kpis.priority_weighted_fulfilment, 1)} effect · {tip.c.metrics.expected_losses.toFixed(1)} losses</div>
          <div className="line">{tip.c.name}</div>
          <div className="sub">{tip.c.kpis.sorties} sorties · {tip.c.metrics.munitions_total} guided weapons</div>
        </div>
      )}
      <p className="muted" style={{ fontSize: 11, margin: '4px 0 0' }}>
        Up and to the left is better. Ringed: current intent.
      </p>
    </div>
  )
}

/** Plain-language trade of a COA against the current-intent COA, computed from the numbers. */
function tradeSentence(c: Coa, ref: Coa): string {
  const dEff = (c.kpis.priority_weighted_fulfilment - ref.kpis.priority_weighted_fulfilment) * 100
  const dMissions = ref.kpis.missions_planned - c.kpis.missions_planned
  const lossCut = ref.metrics.expected_losses > 0 ? 1 - c.metrics.expected_losses / ref.metrics.expected_losses : 0
  const gains: string[] = []
  const costs: string[] = []
  const missions = (n: number) => `${n} mission${n > 1 ? 's' : ''}`
  if (dEff <= -0.5) costs.push(`gives up ${(-dEff).toFixed(1)} pts of effect${dMissions > 0 ? ` (${missions(dMissions)})` : ''}`)
  else if (dEff >= 0.5) gains.push(`adds ${dEff.toFixed(1)} pts of effect${dMissions < 0 ? ` (${missions(-dMissions)})` : ''}`)
  const losses = `(${ref.metrics.expected_losses.toFixed(1)}\u00a0→\u00a0${c.metrics.expected_losses.toFixed(1)})`
  if (lossCut > 0.05) gains.push(`cuts expected losses ${fmtPct(lossCut)} ${losses}`)
  else if (lossCut < -0.05) costs.push(`raises expected losses ${fmtPct(-lossCut)} ${losses}`)
  const dMun = ref.metrics.munitions_total - c.metrics.munitions_total
  if (dMun > 0) gains.push(`saves ${dMun} guided weapons`)
  else if (dMun < 0) costs.push(`uses ${-dMun} more guided weapons`)
  const dGround = c.metrics.min_fighters_on_ground - ref.metrics.min_fighters_on_ground
  if (dGround > 0) gains.push(`keeps ${dGround} more fighters on the ground at peak`)
  else if (dGround < 0) costs.push(`leaves ${-dGround} fewer fighters on the ground at peak`)
  if (c.kpis.max_sortie_risk < ref.kpis.max_sortie_risk - 0.01)
    gains.push(`caps the worst sortie at ${fmtPct(c.kpis.max_sortie_risk)}`)
  const list = (xs: string[]) => (xs.length > 1 ? `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}` : xs[0])
  let out: string
  if (costs.length && gains.length) out = `${c.name} ${list(costs)}; in return it ${list(gains)}.`
  else if (costs.length) out = `${c.name} ${list(costs)}, with no offsetting gain.`
  else if (gains.length) out = `${c.name} ${list(gains)} at no cost on these measures.`
  else out = `${c.name} is equivalent on these measures.`
  const dropped = c.metrics.high_priority_dropped.filter((d) => !ref.metrics.high_priority_dropped.includes(d))
  if (dropped.length) out += ` Drops ${list(dropped)}.`
  return out
}

function bestOf(row: Row, coas: Coa[]): number | null {
  if (!row.better || coas.length < 2) return null
  const vals = coas.map(row.get)
  const best = row.better === 'up' ? Math.max(...vals) : Math.min(...vals)
  return vals.filter((v) => v === best).length === vals.length ? null : best // no "best" if all tie
}

export default function CoaPanel() {
  const open = useStore((s) => s.coaOpen)
  const setOpen = useStore((s) => s.setCoaOpen)
  const data = useStore((s) => s.coas)
  const loadCoas = useStore((s) => s.loadCoas)
  const adopt = useStore((s) => s.adoptCoa)
  const busy = useStore((s) => s.busy)
  const pending = useStore((s) => !!s.proposal)
  const current = useStore((s) => s.app?.world.intent.name ?? 'Max effect')
  const now = useStore((s) => s.app?.world.now ?? 0)

  // Compute once per opening; after a failure, wait for "Recompute" instead of retrying in a loop.
  const [tried, setTried] = useState(false)
  useEffect(() => {
    if (!open) return setTried(false)
    if (!data && !busy && !tried) {
      setTried(true)
      void loadCoas()
    }
  }, [open, data, busy, tried, loadCoas])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, setOpen])

  if (!open) return null
  const coas = data?.coas ?? []
  let body: ReactNode = <div className="empty">Planning the current situation under each intent...</div>
  if (coas.length) {
    const ref = coas.find((c) => c.intent.name === current) ?? coas[0]
    body = (
      <>
      <div className="coa-grid">
        <TradeOff coas={coas} current={current} />
        <div className="coa-trades">
          <h5>The trade, in one line each</h5>
          {coas.filter((c) => c !== ref).map((c) => <p key={c.id}>{tradeSentence(c, ref)}</p>)}
          <p className="muted">
            Compared with <b className="ink2">{ref.name}</b>, the current intent. All three plans pass the same independent
            constraint check (crew rest, stocks, airspace, weather, alert reserve).
          </p>
        </div>
      </div>
        <table className="coa-table">
          <thead>
            <tr>
              <th />
              {coas.map((c) => (
                <th key={c.id}>
                  <div className="coa-name">
                    {c.name}
                    {c.intent.name === current && <span className="tag">current intent</span>}
                  </div>
                  <div className="coa-desc">{c.description}</div>
                  <div className="coa-chips">{intentChips(c.intent).map((t) => <span key={t} className="tag">{t}</span>)}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ROWS.map((row) => {
              const best = bestOf(row, coas)
              return (
                <tr key={row.label}>
                  <td className="coa-rowlabel">
                    {row.label}
                    {row.hint && <div className="muted" style={{ fontSize: 10 }}>{row.hint}</div>}
                  </td>
                  {coas.map((c) => {
                    const v = row.get(c)
                    const isBest = best !== null && v === best
                    return (
                      <td key={c.id} className={`num${isBest ? ' coa-best' : ''}`}>
                        {row.fmt(v, c)}
                        {isBest && <span className="coa-best-tag">best</span>}
                      </td>
                    )
                  })}
                </tr>
              )
            })}
            <tr>
              <td className="coa-rowlabel">High-priority missions given up<div className="muted" style={{ fontSize: 10 }}>P8 and above</div></td>
              {coas.map((c) => (
                <td key={c.id} style={{ fontSize: 12 }}>
                  {c.metrics.high_priority_dropped.length ? c.metrics.high_priority_dropped.join(', ') : <span className="muted">none</span>}
                </td>
              ))}
            </tr>
            <tr>
              <td />
              {coas.map((c) => (
                <td key={c.id}>
                  <button className="btn primary small" disabled={!!busy || pending} onClick={() => void adopt(c.id, c.name)}>
                    Adopt → review changes
                  </button>
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </>
    )
  }
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && setOpen(false)}>
      <div className="modal" role="dialog" aria-label="Courses of action">
        <div className="modal-head">
          <div>
            <h2>Courses of action</h2>
            <div className="muted" style={{ fontSize: 12 }}>
              The situation at {fmtTime(now)} planned under three commander's intents. Launched missions stay frozen; each COA
              is the least-disruptive way to apply its intent.{data ? ` Solved in parallel in ${data.seconds} s.` : ''}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn small" disabled={!!busy} onClick={() => void loadCoas()}>Recompute</button>
            <button className="btn small" onClick={() => setOpen(false)} aria-label="Close">Close</button>
          </div>
        </div>
        <div className={busy && coas.length ? 'dim-when-busy' : undefined} style={busy && coas.length ? { opacity: 0.55 } : undefined}>
          {body}
        </div>
        <p className="muted" style={{ fontSize: 11, margin: '10px 0 0' }}>
          Adopting a COA creates a proposal. Nothing changes until you approve it, and the adopted intent then applies to every
          later retask.{pending ? ' Approve or reject the pending proposal first.' : ''}
        </p>
      </div>
    </div>
  )
}
