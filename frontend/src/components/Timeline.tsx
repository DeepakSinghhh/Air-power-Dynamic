import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { useStore, useView, type View } from '../store'
import { C, FAMILY_COLOR, roleColor } from '../theme'
import type { Aircraft, Assignment, Mission, Plan, Sortie } from '../types'
import { baseStatus, fmtPct, fmtTime, missionOrder, sortieKey } from '../util'

const LABEL_W = 210
const AXIS_H = 34
const NIGHT_START = 19 * 60
const NIGHT_END = 5 * 60

type Row =
  | { kind: 'group'; key: string; h: number; base: string }
  | { kind: 'mission'; key: string; h: number; mission: Mission }
  | { kind: 'aircraft'; key: string; h: number; ac: Aircraft }

interface Bar {
  key: string
  mission: string
  tail: string
  sortie: Sortie
  a: Assignment
  tanker: boolean
  spare: boolean
}

interface Tip {
  x: number
  y: number
  body: ReactNode
}

function horizon(view: View): number {
  let end = 1440
  for (const p of [view.plan, view.previous]) {
    for (const a of Object.values(p?.assignments ?? {}))
      for (const s of [...a.sorties, ...a.tanker_sorties]) end = Math.max(end, s.recover + 120)
  }
  return Math.ceil(end / 60) * 60
}

function sortiesByTail(plan: Plan | null): Map<string, Bar[]> {
  const out = new Map<string, Bar[]>()
  for (const a of Object.values(plan?.assignments ?? {})) {
    const add = (s: Sortie, tanker: boolean, spare = false) => {
      const list = out.get(s.tail) ?? []
      list.push({ key: `${sortieKey(a.mission, s.tail)}|${s.launch}${spare ? '|spare' : ''}`, mission: a.mission, tail: s.tail, sortie: s, a, tanker, spare })
      out.set(s.tail, list)
    }
    a.sorties.forEach((s) => add(s, false))
    a.tanker_sorties.forEach((s) => add(s, true))
    ;(a.spares ?? []).forEach((s) => add(s, false, true))
  }
  return out
}

export default function Timeline() {
  const view = useView()!
  const mode = useStore((s) => s.timelineMode)
  const setMode = useStore((s) => s.setTimelineMode)
  const hideIdle = useStore((s) => s.hideIdle)
  const setHideIdle = useStore((s) => s.setHideIdle)
  const viewTime = useStore((s) => s.viewTime)
  const setViewTime = useStore((s) => s.setViewTime)
  const playing = useStore((s) => s.playing)
  const setPlaying = useStore((s) => s.setPlaying)
  const speed = useStore((s) => s.speed)
  const setSpeed = useStore((s) => s.setSpeed)
  const selection = useStore((s) => s.selection)
  const select = useStore((s) => s.select)
  const hoverMission = useStore((s) => s.hoverMission)
  const setHoverMission = useStore((s) => s.setHoverMission)
  const metResp = useStore((s) => s.met)

  const scrollRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(1200)
  const [zoom, setZoom] = useState(1)
  const [tip, setTip] = useState<Tip | null>(null)

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setWidth(el.clientWidth))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const { world, plan, previous, proposal } = view
  const T1 = horizon(view)
  const contentW = Math.max(400, (width - LABEL_W - 2) * zoom)
  const x = (t: number) => (t / T1) * contentW
  const tFromX = (px: number) => (px / contentW) * T1

  // Playback.
  useEffect(() => {
    if (!playing) return
    let raf = 0
    let last = performance.now()
    const step = (now: number) => {
      const dt = (now - last) / 1000
      last = now
      const s = useStore.getState()
      const next = s.viewTime + dt * s.speed
      if (next >= T1) {
        s.setViewTime(T1)
        s.setPlaying(false)
        return
      }
      s.setViewTime(next)
      raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [playing, T1])

  // Keep the cursor in view while playing.
  useEffect(() => {
    const el = scrollRef.current
    if (!el || !playing) return
    const cx = x(viewTime)
    if (cx < el.scrollLeft || cx > el.scrollLeft + el.clientWidth - LABEL_W - 40)
      el.scrollLeft = Math.max(0, cx - 120)
  })

  const changeOf = useMemo(() => {
    const m = new Map<string, string>()
    proposal?.diff.changes.forEach((c) => m.set(c.mission, c.change))
    return m
  }, [proposal])

  const current = useMemo(() => sortiesByTail(plan), [plan])
  const before = useMemo(() => sortiesByTail(previous), [previous])

  const rows: Row[] = useMemo(() => {
    if (mode === 'missions') {
      return missionOrder(world, plan?.assignments ?? {}).map((id) => ({
        kind: 'mission' as const, key: id, h: 26, mission: world.missions[id],
      }))
    }
    const out: Row[] = []
    const bases = Object.values(world.bases).filter((b) => Object.values(world.aircraft).some((a) => a.base === b.id))
    for (const b of bases) {
      const acs = Object.values(world.aircraft)
        .filter((a) => a.base === b.id)
        .filter((a) => !hideIdle || current.has(a.tail) || before.has(a.tail) || !a.serviceable)
        .sort((p, q) => p.type.localeCompare(q.type) || p.tail.localeCompare(q.tail))
      out.push({ kind: 'group', key: `g-${b.id}`, h: 22, base: b.id })
      acs.forEach((ac) => out.push({ kind: 'aircraft', key: ac.tail, h: 20, ac }))
    }
    return out
  }, [mode, world, plan, hideIdle, current, before])

  const offsets = useMemo(() => {
    const o = new Map<string, number>()
    let y = 0
    rows.forEach((r) => {
      o.set(r.key, y)
      y += r.h
    })
    return { map: o, total: y }
  }, [rows])

  const focus = hoverMission ?? (selection?.kind === 'mission' ? selection.id : null)

  const showTip = (e: React.PointerEvent, body: ReactNode) => setTip({ x: e.clientX, y: e.clientY, body })

  // ---------- axis interaction (scrub view time) ----------
  const scrub = (e: React.PointerEvent<SVGSVGElement>) => {
    const svg = e.currentTarget
    const set = (clientX: number) => {
      const r = svg.getBoundingClientRect()
      setViewTime(Math.round(tFromX(clientX - r.left)))
    }
    set(e.clientX)
    setPlaying(false)
    const move = (ev: PointerEvent) => set(ev.clientX)
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  const hours: number[] = []
  const stepH = zoom >= 3 ? 30 : zoom >= 1.5 ? 60 : 120
  for (let t = 0; t <= T1; t += stepH) hours.push(t)
  const nights: [number, number][] = []
  for (let d = -1; d * 1440 < T1; d++) nights.push([d * 1440 + NIGHT_START, (d + 1) * 1440 + NIGHT_END])

  // ---------- mission-row rendering ----------
  const missionRow = (m: Mission, y: number, h: number) => {
    const a = plan?.assignments[m.id]
    const old = previous?.assignments[m.id]
    const color = roleColor(m.role)
    const cy = y + h / 2
    const els: ReactNode[] = []
    const dim = focus && focus !== m.id ? 0.35 : 1
    els.push(
      <rect key="win" x={x(m.tot_earliest)} y={cy - 7} width={Math.max(2, x(m.tot_latest) - x(m.tot_earliest))} height={14}
        rx={3} fill={C.surface2} stroke={a ? 'none' : C.muted} strokeDasharray={a ? undefined : '3 3'} />,
    )
    if (old && (!a || old.tot !== a.tot || changeOf.get(m.id) === 'MODIFIED')) {
      const s0 = Math.min(...old.sorties.map((s) => s.launch))
      const s1 = Math.max(...old.sorties.map((s) => s.recover))
      els.push(
        <rect key="ghost" x={x(s0)} y={cy - 5} width={x(s1) - x(s0)} height={10} rx={3} fill="none"
          stroke={C.ink2} strokeOpacity={0.7} strokeDasharray="4 3" />,
        <path key="ghost-tot" d={diamond(x(old.tot), cy, 5)} fill="none" stroke={C.ink2} strokeOpacity={0.8} />,
      )
    }
    if (a) {
      const s0 = Math.min(...a.sorties.map((s) => s.launch))
      const s1 = Math.max(...a.sorties.map((s) => s.recover))
      const on1 = a.tot + m.on_station_min
      const seg = (k: string, t0: number, t1: number, op: number) =>
        t1 > t0 && (
          <rect key={k} x={x(t0) + 1} y={cy - 4} width={Math.max(1, x(t1) - x(t0) - 2)} height={8} rx={2}
            fill={color} fillOpacity={op * dim} />
        )
      els.push(seg('in', s0, a.tot, 0.55), seg('on', a.tot, on1, 1), seg('out', on1, s1, 0.55))
      a.tanker_sorties.forEach((ts, i) =>
        els.push(
          <rect key={`tk${i}`} x={x(ts.launch)} y={y + 2} width={x(ts.recover) - x(ts.launch)} height={2.5} rx={1}
            fill={FAMILY_COLOR.support} fillOpacity={0.9 * dim} />,
        ),
      )
      els.push(<path key="tot" d={diamond(x(a.tot), cy, 5.5)} fill={C.ink} stroke={C.surface} strokeWidth={2} opacity={dim} />)
      if (changeOf.get(m.id) === 'ADDED')
        els.push(<rect key="new" x={x(s0) - 3} y={cy - 8} width={x(s1) - x(s0) + 6} height={16} rx={4} fill="none" stroke={C.good} strokeWidth={1.5} />)
      if (a.spares?.length)
        els.push(
          <text key="sp" x={x(Math.max(s1, m.tot_latest)) + 6} y={cy + 4} fontSize={10} fill={C.muted}>
            +{a.spares.length} spare{a.spares.length > 1 ? 's' : ''}
          </text>,
        )
    } else {
      els.push(
        <text key="np" x={x(m.tot_latest) + 6} y={cy + 4} fontSize={11} fill={C.muted}>
          not planned
        </text>,
      )
    }
    return (
      <g key={m.id} className="bar"
        onPointerEnter={() => setHoverMission(m.id)}
        onPointerLeave={() => { setHoverMission(null); setTip(null) }}
        onPointerMove={(e) => showTip(e, missionTip(m, a, old, plan))}
        onClick={() => select({ kind: 'mission', id: m.id })}>
        <rect x={0} y={y} width={contentW} height={h} fill="transparent" />
        {els}
      </g>
    )
  }

  // Dependency links (SEAD before strike) in mission view.
  const links: ReactNode[] = []
  if (mode === 'missions' && plan) {
    for (const m of Object.values(world.missions)) {
      const a = plan.assignments[m.id]
      const d = m.depends_on ? plan.assignments[m.depends_on] : undefined
      const ym = offsets.map.get(m.id)
      const yd = m.depends_on ? offsets.map.get(m.depends_on) : undefined
      if (!a || !d || ym === undefined || yd === undefined) continue
      links.push(
        <path key={`dep-${m.id}`} d={`M${x(d.tot)},${yd + 13} C${x(d.tot) + 12},${yd + 13} ${x(a.tot) - 12},${ym + 13} ${x(a.tot)},${ym + 13}`}
          fill="none" stroke={C.ink2} strokeOpacity={0.6} strokeWidth={1} markerEnd="url(#arrow)" />,
      )
    }
  }

  // ---------- aircraft-row rendering ----------
  const aircraftRow = (ac: Aircraft, y: number, h: number) => {
    const els: ReactNode[] = []
    const cy = y + h / 2
    const type = world.types[ac.type]
    const cur = current.get(ac.tail) ?? []
    const old = before.get(ac.tail) ?? []
    const curKeys = new Set(cur.map((b) => b.key))
    const oldKeys = new Set(old.map((b) => b.key))
    if (!ac.serviceable)
      els.push(<rect key="us" x={0} y={y + 1} width={contentW} height={h - 2} fill="url(#hatch-critical)" opacity={0.6} />)
    old.filter((b) => !curKeys.has(b.key)).forEach((b) =>
      els.push(
        <rect key={`g-${b.key}`} x={x(b.sortie.launch)} y={cy - 6} width={x(b.sortie.recover) - x(b.sortie.launch)} height={12}
          rx={3} fill="none" stroke={C.ink2} strokeOpacity={0.75} strokeDasharray="4 3" />,
      ),
    )
    for (const b of cur) {
      const m = world.missions[b.mission]
      if (!m) continue
      const s = b.sortie
      if (b.spare) {
        // Ground spare: booked for the whole sortie (dashed), standing by while the package starts up (filled).
        const dim = focus && focus !== b.mission ? 0.35 : 1
        const color = roleColor(m.role)
        const hold = s.launch + type.prep_min + 15
        const wpx = x(s.recover) - x(s.launch)
        els.push(
          <g key={b.key} className="bar"
            onPointerEnter={() => setHoverMission(b.mission)}
            onPointerLeave={() => { setHoverMission(null); setTip(null) }}
            onPointerMove={(e) => showTip(e, spareTip(b, m))}
            onClick={(e) => { e.stopPropagation(); select({ kind: 'mission', id: b.mission }) }}>
            <rect x={x(s.launch) + 0.5} y={cy - 5.5} width={Math.max(1, wpx - 1)} height={11} rx={2} fill="none"
              stroke={color} strokeOpacity={0.8 * dim} strokeDasharray="3 2" />
            <rect x={x(s.launch) + 1} y={cy - 5} width={Math.max(1, x(hold) - x(s.launch) - 2)} height={10} rx={2}
              fill={color} fillOpacity={0.35 * dim} />
            {wpx > 70 && (
              <text x={x(hold) + 4} y={cy + 3.5} fontSize={10} fill={C.ink2} opacity={dim} pointerEvents="none">
                spare {b.mission}
              </text>
            )}
          </g>,
        )
        continue
      }
      const color = b.tanker ? FAMILY_COLOR.support : roleColor(m.role)
      const dim = focus && focus !== b.mission ? 0.35 : 1
      const prepEnd = s.launch + type.prep_min
      const onStart = b.tanker ? s.launch + type.prep_min + (s.route_km / type.speed_kmh) * 60 : b.a.tot
      const onEnd = b.tanker ? s.recover - (s.route_km / type.speed_kmh) * 60 : b.a.tot + m.on_station_min
      const seg = (k: string, t0: number, t1: number, op: number) =>
        t1 > t0 && (
          <rect key={k} x={x(t0) + 1} y={cy - 6} width={Math.max(1, x(t1) - x(t0) - 2)} height={12} rx={2}
            fill={color} fillOpacity={op * dim} />
        )
      const wpx = x(s.recover) - x(s.launch)
      els.push(
        <g key={b.key} className="bar"
          onPointerEnter={() => setHoverMission(b.mission)}
          onPointerLeave={() => { setHoverMission(null); setTip(null) }}
          onPointerMove={(e) => showTip(e, sortieTip(b, m, world.crews[s.crew ?? '']?.id))}
          onClick={(e) => { e.stopPropagation(); select({ kind: 'mission', id: b.mission }) }}>
          <rect x={x(s.recover)} y={cy - 6} width={x(s.recover + type.turnaround_min) - x(s.recover)} height={12}
            fill="url(#hatch-muted)" opacity={dim} />
          {seg('p', s.launch, prepEnd, 0.3)}
          {seg('i', prepEnd, onStart, 0.6)}
          {seg('o', onStart, onEnd, 1)}
          {seg('e', onEnd, s.recover, 0.6)}
          {!oldKeys.has(b.key) && previous && (
            <rect x={x(s.launch) - 2} y={cy - 8} width={wpx + 4} height={16} rx={4} fill="none" stroke={C.good} strokeWidth={1.5} />
          )}
          {wpx > 52 && (
            <text x={x(s.launch) + 4} y={cy + 3.5} fontSize={10} fontWeight={600} fill="#fff" opacity={dim} pointerEvents="none">
              {b.tanker ? `AAR ${b.mission}` : b.mission}
            </text>
          )}
        </g>,
      )
    }
    return (
      <g key={ac.tail}>
        <rect x={0} y={y} width={contentW} height={h} fill="transparent"
          onClick={() => select({ kind: 'aircraft', id: ac.tail })} />
        {els}
      </g>
    )
  }

  const groupRow = (baseId: string, y: number, h: number) => {
    const b = world.bases[baseId]
    const fog = metResp?.forecast.bases[baseId]
    return (
      <g key={`g-${baseId}`}>
        <rect x={0} y={y} width={contentW} height={h} fill={C.surface2} />
        {fog?.times.map((t, i) =>
          fog.p_fog[i] >= 0.1 ? (
            <rect key={`f${t}`} x={x(t) + 0.5} y={y + 3} width={Math.max(1, x(t + 60) - x(t) - 1)} height={h - 6} rx={2}
              fill={C.accent} fillOpacity={0.15 + 0.7 * fog.p_fog[i]}>
              <title>{`${fmtTime(t)} P(fog) ${fmtPct(fog.p_fog[i])}`}</title>
            </rect>
          ) : null,
        )}
        {b.closures.map((c, i) => (
          <g key={i}>
            <rect x={x(c.start)} y={y} width={x(c.end) - x(c.start)} height={h} fill={C.critical} fillOpacity={0.25} />
            <text x={x(c.start) + 4} y={y + 15} fontSize={10} fill={C.ink}>✕ {c.reason}</text>
          </g>
        ))}
      </g>
    )
  }

  // Closures shade all rows of a base in aircraft view.
  const closureBands: ReactNode[] = []
  if (mode === 'aircraft') {
    let groupStart = -1
    let base = ''
    const flush = (end: number) => {
      if (groupStart < 0) return
      world.bases[base].closures.forEach((c, i) =>
        closureBands.push(
          <rect key={`cb-${base}-${i}`} x={x(c.start)} y={groupStart} width={x(c.end) - x(c.start)} height={end - groupStart}
            fill="url(#hatch-critical)" opacity={0.5} pointerEvents="none" />,
        ),
      )
    }
    rows.forEach((r) => {
      const y = offsets.map.get(r.key)!
      if (r.kind === 'group') {
        flush(y)
        groupStart = y
        base = r.base
      }
    })
    flush(offsets.total)
  }

  const nowX = x(world.now)
  const viewX = x(viewTime)

  return (
    <section className="timeline" aria-label="Synchronisation matrix">
      <div className="tl-toolbar">
        <div className="seg" role="group" aria-label="Timeline view">
          <button aria-pressed={mode === 'missions'} onClick={() => setMode('missions')}>Missions</button>
          <button aria-pressed={mode === 'aircraft'} onClick={() => setMode('aircraft')}>Aircraft</button>
        </div>
        {mode === 'aircraft' && (
          <label>
            <input type="checkbox" checked={hideIdle} onChange={(e) => setHideIdle(e.target.checked)} /> Hide idle aircraft
          </label>
        )}
        <button className="btn small" onClick={() => setPlaying(!playing)} aria-label={playing ? 'Pause' : 'Play'}>
          {playing ? '❚❚ Pause' : '▶ Play'}
        </button>
        <label>
          Speed
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}
            style={{ background: C.surface2, color: C.ink, border: `1px solid ${C.axis}`, borderRadius: 4 }}>
            {[10, 30, 60, 120].map((v) => <option key={v} value={v}>{v} min/s</option>)}
          </select>
        </label>
        <button className="btn small" onClick={() => setViewTime(world.now)}>Jump to now</button>
        <div className="spacer" />
        <span className="muted" style={{ fontSize: 12 }}>
          {mode === 'missions'
            ? '▭ TOT window · bar: ingress | on station | egress · ◆ TOT · ⤳ SEAD before strike'
            : 'bar: start-up | transit | on station | return · hatched: turnaround · dashed: ground spare · red hatch: base closed · blue cells: P(fog)'}
        </span>
        <div className="seg" role="group" aria-label="Zoom">
          <button onClick={() => setZoom(Math.max(1, zoom / 1.5))}>−</button>
          <button aria-pressed={zoom === 1} onClick={() => setZoom(1)}>Fit</button>
          <button onClick={() => setZoom(Math.min(8, zoom * 1.5))}>+</button>
        </div>
      </div>
      <div className="tl-scroll dim-when-busy" ref={scrollRef} onPointerLeave={() => setTip(null)}>
        <div className="tl-canvas" style={{ gridTemplateColumns: `${LABEL_W}px ${contentW}px`, gridTemplateRows: `${AXIS_H}px ${offsets.total}px` }}>
          <div className="tl-corner">{mode === 'missions' ? `${rows.length} missions` : 'Aircraft by base'}</div>
          <svg className="tl-axis" width={contentW} height={AXIS_H} onPointerDown={scrub} role="slider"
            aria-label="View time" aria-valuemin={0} aria-valuemax={T1} aria-valuenow={Math.round(viewTime)}>
            {nights.map(([a, b], i) => (
              <rect key={i} x={x(a)} y={0} width={x(b) - x(a)} height={AXIS_H} fill="#fff" fillOpacity={0.03} />
            ))}
            {hours.map((t) => (
              <g key={t}>
                <line x1={x(t)} x2={x(t)} y1={AXIS_H - 8} y2={AXIS_H} stroke={C.axis} />
                <text x={x(t) + 3} y={AXIS_H - 10} fontSize={11} fill={C.muted} className="num">{fmtTime(t)}</text>
              </g>
            ))}
            <rect x={Math.max(1, nowX - 22)} y={2} width={44} height={14} rx={7} fill={C.ink} />
            <text x={Math.max(23, nowX)} y={12.5} fontSize={10} fontWeight={700} fill={C.page} textAnchor="middle">NOW</text>
            <path d={`M${viewX - 6},${AXIS_H - 9} L${viewX + 6},${AXIS_H - 9} L${viewX},${AXIS_H - 1} Z`} fill={C.accentInk} />
          </svg>
          <div className="tl-labels">
            {rows.map((r) => <RowLabel key={r.key} row={r} view={view} changeOf={changeOf} />)}
          </div>
          <svg className="tl-content" width={contentW} height={offsets.total}
            onClick={(e) => {
              if (e.target === e.currentTarget) select(null)
            }}>
            <defs>
              <pattern id="hatch-muted" patternUnits="userSpaceOnUse" width="5" height="5" patternTransform="rotate(45)">
                <line x1="0" y1="0" x2="0" y2="5" stroke={C.muted} strokeWidth="1.5" strokeOpacity="0.6" />
              </pattern>
              <pattern id="hatch-critical" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(135)">
                <line x1="0" y1="0" x2="0" y2="6" stroke={C.critical} strokeWidth="1.5" strokeOpacity="0.55" />
              </pattern>
              <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M0,0 L8,4 L0,8 Z" fill={C.ink2} fillOpacity={0.7} />
              </marker>
            </defs>
            {nights.map(([a, b], i) => (
              <rect key={`n${i}`} x={x(a)} y={0} width={x(b) - x(a)} height={offsets.total} fill="#fff" fillOpacity={0.02} pointerEvents="none" />
            ))}
            {hours.map((t) => (
              <line key={t} x1={x(t)} x2={x(t)} y1={0} y2={offsets.total} stroke={C.grid} pointerEvents="none" />
            ))}
            {rows.map((r) => {
              const y = offsets.map.get(r.key)!
              const sel =
                (r.kind === 'mission' && selection?.kind === 'mission' && selection.id === r.mission.id) ||
                (r.kind === 'aircraft' && selection?.kind === 'aircraft' && selection.id === r.ac.tail)
              return (
                <g key={r.key}>
                  {sel && <rect x={0} y={y} width={contentW} height={r.h} fill={C.accent} fillOpacity={0.1} />}
                  <line x1={0} x2={contentW} y1={y + r.h} y2={y + r.h} stroke="#fff" strokeOpacity={0.03} />
                  {r.kind === 'mission' && missionRow(r.mission, y, r.h)}
                  {r.kind === 'aircraft' && aircraftRow(r.ac, y, r.h)}
                  {r.kind === 'group' && groupRow(r.base, y, r.h)}
                </g>
              )
            })}
            {closureBands}
            {links}
            <rect x={0} y={0} width={Math.max(0, nowX)} height={offsets.total} fill={C.page} fillOpacity={0.35} pointerEvents="none" />
            <line x1={nowX} x2={nowX} y1={0} y2={offsets.total} stroke={C.ink} strokeWidth={1.5} pointerEvents="none" />
            <line x1={viewX} x2={viewX} y1={0} y2={offsets.total} stroke={C.accentInk} strokeWidth={1} pointerEvents="none" />
          </svg>
        </div>
      </div>
      {tip && (
        <div className="tooltip" style={{ left: tip.x + 14, top: Math.min(tip.y + 14, window.innerHeight - 160) }}>
          {tip.body}
        </div>
      )}
    </section>
  )
}

function diamond(cx: number, cy: number, r: number) {
  return `M${cx},${cy - r} L${cx + r},${cy} L${cx},${cy + r} L${cx - r},${cy} Z`
}

function RowLabel({ row, view, changeOf }: { row: Row; view: View; changeOf: Map<string, string> }) {
  const selection = useStore((s) => s.selection)
  const select = useStore((s) => s.select)
  const setHoverMission = useStore((s) => s.setHoverMission)
  const viewTime = useStore((s) => s.viewTime)
  const { world, plan } = view
  if (row.kind === 'group') {
    const b = world.bases[row.base]
    const st = baseStatus(b, viewTime)
    const n = Object.values(world.aircraft).filter((a) => a.base === b.id)
    return (
      <div className="tl-label group" style={{ height: row.h, cursor: 'pointer' }} title={st.text}
        onClick={() => select({ kind: 'base', id: b.id })}>
        <span className="swatch" style={{ background: st.status === 'open' ? C.good : st.status === 'closing' ? C.warning : C.critical }} />
        <span className="t">{b.name.toUpperCase()}</span>
        <span className="muted" style={{ fontWeight: 400 }}>{n.filter((a) => a.serviceable).length}/{n.length} svc</span>
      </div>
    )
  }
  if (row.kind === 'mission') {
    const m = row.mission
    const a = plan?.assignments[m.id]
    const chg = changeOf.get(m.id)
    const sel = selection?.kind === 'mission' && selection.id === m.id
    return (
      <div className={`tl-label${sel ? ' sel' : ''}`} style={{ height: row.h }}
        onClick={() => select({ kind: 'mission', id: m.id })}
        onPointerEnter={() => setHoverMission(m.id)} onPointerLeave={() => setHoverMission(null)}>
        <span className={`swatch${a ? '' : ' hollow'}`} style={{ background: roleColor(m.role), borderColor: roleColor(m.role) }} />
        <b>{m.id}</b>
        <span className="prio">P{m.priority}</span>
        <span className="t muted">{m.role}</span>
        {chg && <span className={`chg-icon chg-${chg}`} title={chg}>{chg === 'ADDED' ? '+' : chg === 'DROPPED' ? '−' : '~'}</span>}
      </div>
    )
  }
  const ac = row.ac
  const sel = selection?.kind === 'aircraft' && selection.id === ac.tail
  return (
    <div className={`tl-label${sel ? ' sel' : ''}${ac.serviceable ? '' : ' us'}`} style={{ height: row.h, paddingLeft: 22 }}
      onClick={() => select({ kind: 'aircraft', id: ac.tail })}>
      <span className="t">{ac.tail.split('-').slice(1).join('-')}</span>
      {!ac.serviceable && <span className="tag">U/S</span>}
      <span className="muted num" style={{ marginLeft: 'auto', fontSize: 11 }}>{fmtPct(ac.p_serviceable)}</span>
    </div>
  )
}

function missionTip(m: Mission, a: Assignment | undefined, old: Assignment | undefined, plan: Plan | null) {
  return (
    <>
      <div className="big">{a ? `TOT ${fmtTime(a.tot)}` : 'Not planned'}</div>
      <div className="line"><span className="key" style={{ borderColor: roleColor(m.role) }} />{m.id} · {m.role} · P{m.priority}</div>
      <div className="sub">Window {fmtTime(m.tot_earliest)}-{fmtTime(m.tot_latest)} · {m.label}</div>
      {a && (
        <div className="sub">
          {a.sorties.length} aircraft · launch {fmtTime(Math.min(...a.sorties.map((s) => s.launch)))} · max risk{' '}
          {fmtPct(Math.max(...a.sorties.map((s) => s.risk)), 1)}{a.tankers.length ? ` · AAR ${a.tankers.join(', ')}` : ''}
        </div>
      )}
      {old && a && old.tot !== a.tot && <div className="sub">Was TOT {fmtTime(old.tot)}</div>}
      {!a && plan?.unassigned[m.id]?.[0] && <div className="sub">{plan.unassigned[m.id][0]}</div>}
    </>
  )
}

function spareTip(b: Bar, m: Mission) {
  const s = b.sortie
  return (
    <>
      <div className="big">Ground spare</div>
      <div className="line">
        <span className="key" style={{ borderColor: roleColor(m.role) }} />
        {b.tail} · {b.mission} ({m.role})
      </div>
      <div className="sub">
        Starts up with the package at {fmtTime(s.launch)}; launches if a primary is unserviceable (its crew walks to the spare).
        Booked until {fmtTime(s.recover)} so the plan stays feasible either way.
      </div>
    </>
  )
}

function sortieTip(b: Bar, m: Mission, crew: string | undefined) {
  const s = b.sortie
  return (
    <>
      <div className="big">{fmtTime(s.launch)}-{fmtTime(s.recover)}</div>
      <div className="line">
        <span className="key" style={{ borderColor: b.tanker ? FAMILY_COLOR.support : roleColor(m.role) }} />
        {b.tail} · {b.tanker ? `tanker for ${b.mission}` : `${b.mission} (${m.role})`}
      </div>
      {!b.tanker && (
        <div className="sub">
          Crew {crew ?? '-'} · {s.route_km.toFixed(0)} km · risk {fmtPct(s.risk, 1)}{s.needs_aar ? ' · AAR' : ''}
        </div>
      )}
    </>
  )
}
