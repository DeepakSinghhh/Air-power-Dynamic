import { useEffect, useState } from 'react'

import { api } from '../api'
import { useStore, useView } from '../store'
import { C, STATUS_COLOR, STATUS_ICON } from '../theme'
import type { Feed, ReadinessBase, ReadinessResponse } from '../types'
import { baseStatus, fmtDur, fmtPct, fmtTime } from '../util'

type Fresh = 'fresh' | 'stale' | 'old'
const FRESH: Record<Fresh, { color: string; icon: string; text: string }> = {
  fresh: { color: C.good, icon: '●', text: 'fresh' },
  stale: { color: C.warning, icon: '▲', text: 'stale' },
  old: { color: C.critical, icon: '✕', text: 'old' },
}
const freshness = (f: Feed): Fresh => (f.age_min <= 120 ? 'fresh' : f.age_min <= 360 ? 'stale' : 'old')

function FeedBadge({ f }: { f: Feed }) {
  const s = FRESH[freshness(f)]
  return (
    <span className="feed" title={`${f.label}: last update ${fmtTime(f.as_of)}, ${fmtDur(f.age_min)} before view time (${s.text})`}>
      <span style={{ color: s.color }} aria-hidden>{s.icon}</span>
      <b>{f.label}</b>
      <span className="num">{fmtTime(f.as_of)}</span>
      <span className="muted">· {fmtDur(Math.max(0, f.age_min))} ago</span>
    </span>
  )
}

/** Crews fit to fly, hour by hour (fatigue model + night currency): darker = more of the crews fit. */
function CrewStrip({ b, hours }: { b: ReadinessBase; hours: number[] }) {
  const n = Math.max(1, b.crews_available)
  const low = Math.min(...b.crews_fit_hourly)
  const lowAt = hours[b.crews_fit_hourly.indexOf(low)]
  return (
    <div className="crew-strip-wrap">
      <div className="crew-strip" role="img" aria-label={`Crews fit per hour, lowest ${low} of ${n} at ${fmtTime(lowAt)}`}>
        {b.crews_fit_hourly.map((v, i) => (
          <i key={hours[i]} style={{ background: C.accent, opacity: 0.12 + 0.88 * (v / n) }}
            title={`${fmtTime(hours[i])} · ${v} of ${n} crews fit`} />
        ))}
      </div>
      <span className="muted">low {low}/{n} at {fmtTime(lowAt)}</span>
    </div>
  )
}

export default function Readiness() {
  const view = useView()!
  const viewTime = useStore((s) => s.viewTime)
  const version = useStore((s) => s.app?.version ?? 0)
  const select = useStore((s) => s.select)
  const [data, setData] = useState<ReadinessResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const at = Math.max(view.world.now, Math.floor(viewTime / 30) * 30)
  const pid = view.proposal?.id

  useEffect(() => {
    let live = true
    api.readiness(at, !!pid)
      .then((r) => live && (setData(r), setError(null)))
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)))
    return () => {
      live = false
    }
  }, [at, version, pid])

  if (error) return <div className="empty">Readiness unavailable: {error}</div>
  if (!data) return <div className="empty">Loading readiness...</div>
  return (
    <div className="readiness dim-when-busy">
      <div className="feeds">
        <span className="muted feeds-title">Data feeds at {fmtTime(data.at)}{view.proposal ? ' (proposal)' : ''}</span>
        {data.feeds.map((f) => <FeedBadge key={f.id} f={f} />)}
      </div>
      <table className="ready-table">
        <thead>
          <tr>
            <th>Base</th>
            <th>Aircraft serviceable · tasked ahead</th>
            <th className="r">P(svc)</th>
            <th className="r">Spares</th>
            <th className="r">Alert reserve</th>
            <th>Crews fit now</th>
            <th>Crews fit, next 24 h</th>
            <th>Weapons left after plan</th>
            <th>Airfield</th>
          </tr>
        </thead>
        <tbody>
          {data.bases.map((b) => {
            const base = view.world.bases[b.base]
            const st = baseStatus(base, data.at)
            const svc = b.types.reduce((s, t) => s + t.serviceable, 0)
            const pMean = svc ? b.types.reduce((s, t) => s + t.p_mean * t.serviceable, 0) / svc : 0
            return (
              <tr key={b.base} onClick={() => select({ kind: 'base', id: b.base })}>
                <td>
                  <span style={{ color: STATUS_COLOR[st.status] }} aria-hidden>{STATUS_ICON[st.status]}</span>{' '}
                  <b>{b.name}</b> <span className="muted">{b.base}</span>
                </td>
                <td>
                  {b.types.map((t) => (
                    <div key={t.type} className="ready-type">
                      <span className="num">{t.serviceable}/{t.total}</span> {t.type}
                      {t.tasked ? <span className="muted"> · {t.tasked} tasked</span> : null}
                    </div>
                  ))}
                  {b.types.length === 0 && <span className="muted">no aircraft</span>}
                </td>
                <td className="r num">{svc ? fmtPct(pMean) : '-'}</td>
                <td className="r num">{b.spares || <span className="muted">-</span>}</td>
                <td className="r num">{b.fighters ? `${b.reserve} of ${b.fighters}` : <span className="muted">-</span>}</td>
                <td>
                  <span className="num">{b.crews_fit_now}/{b.crews_available}</span>
                  <span className="muted"> · {b.crews_night} night</span>
                  {b.crews_available < b.crews_total && <span className="muted"> · {b.crews_total - b.crews_available} off</span>}
                </td>
                <td><CrewStrip b={b} hours={data.hours} /></td>
                <td>
                  {b.weapons.length === 0 && <span className="muted">-</span>}
                  {b.weapons.map((w) => {
                    const low = w.left < Math.max(4, 0.25 * w.stock)
                    return (
                      <span key={w.weapon} className="tag" style={{ marginRight: 4 }}
                        title={`${w.weapon}: ${w.stock} in stock, ${w.planned} planned, ${w.left} left`}>
                        {low && <span style={{ color: C.warning }} aria-hidden>▲</span>}
                        {w.weapon} <span className="num">{w.left}/{w.stock}</span>
                      </span>
                    )
                  })}
                </td>
                <td className="ready-wx">
                  {b.closures.length === 0
                    ? <span className="muted">open all day</span>
                    : b.closures.slice(0, 2).map((c, i) => (
                      <div key={i}>{fmtTime(c.start)}-{fmtTime(c.end)} {c.reason}{c.probability != null ? ` (P ${fmtPct(c.probability)})` : ''}</div>
                    ))}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
