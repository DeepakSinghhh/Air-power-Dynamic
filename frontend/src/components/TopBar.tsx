import { useEffect, useRef, useState } from 'react'

import { useStore, useView } from '../store'
import type { Kpis, ScenarioKind } from '../types'
import { fmtPct, fmtTime } from '../util'
import CoaPanel from './CoaPanel'
import RobustPanel from './RobustPanel'
import { WeatherMenu } from './Weather'

type Better = 'up' | 'down' | 'none'

function Tile(props: {
  className?: string
  label: string
  value: string
  delta?: number
  deltaText?: string
  better?: Better
  refLabel?: string
  title?: string
}) {
  const { className, label, value, delta, deltaText, better = 'none', refLabel, title } = props
  let cls = ''
  let glyph = ''
  if (delta !== undefined && Math.abs(delta) > 1e-9) {
    const up = delta > 0
    glyph = up ? '▲' : '▼'
    if (better !== 'none') cls = (up ? 'up' : 'down') + ((up ? better === 'up' : better === 'down') ? '-good' : '-bad')
  }
  return (
    <div className={`tile${className ? ` ${className}` : ''}`} title={title ?? (refLabel ? `Change ${refLabel}` : undefined)}>
      <label>{label}</label>
      <b className="num">{value}</b>
      {deltaText !== undefined && (
        <small>
          <span className={cls}>
            {glyph} {deltaText}
          </span>{' '}
          <span className="ref">{refLabel}</span>
        </small>
      )}
    </div>
  )
}

function kpiTiles(k: Kpis, ref: Kpis | null, refLabel: string, hadr: boolean) {
  const pts = (a: number, b: number) => `${a - b >= 0 ? '+' : ''}${((a - b) * 100).toFixed(1)} pts`
  const n = (a: number, b: number) => `${a - b >= 0 ? '+' : ''}${a - b}`
  return [
    <Tile key="f" label="Mission fulfilment" value={fmtPct(k.priority_weighted_fulfilment, 1)}
      title="Priority-weighted share of missions planned"
      delta={ref ? k.priority_weighted_fulfilment - ref.priority_weighted_fulfilment : undefined}
      deltaText={ref ? pts(k.priority_weighted_fulfilment, ref.priority_weighted_fulfilment) : undefined}
      better="up" refLabel={refLabel} />,
    <Tile key="ev" label="Expected value" value={fmtPct(k.expected_value, 1)}
      title="Priority-weighted value expected on the day: serviceability (with ground spares), tanker availability, SEAD before strike, losses before the target"
      delta={ref ? k.expected_value - ref.expected_value : undefined}
      deltaText={ref ? pts(k.expected_value, ref.expected_value) : undefined}
      better="up" refLabel={refLabel} />,
    <Tile key="m" label="Missions planned" value={`${k.missions_planned} / ${k.missions_total}`}
      delta={ref ? k.missions_planned - ref.missions_planned : undefined}
      deltaText={ref ? n(k.missions_planned, ref.missions_planned) : undefined}
      better="up" refLabel="" />,
    <Tile key="s" className="tile-sorties" label="Sorties" value={`${k.sorties}`}
      deltaText={[k.tanker_sorties ? `+${k.tanker_sorties} tanker` : 'no tanker', k.spares ? `${k.spares} spares` : ''].filter(Boolean).join(' · ')} />,
    hadr ? (
      <Tile key="t" label="Relief lifted" value={`${Math.round(k.cargo_planned_t)} / ${Math.round(k.cargo_total_t)} t`}
        title="Tonnes of relief, rescue and stores planned for delivery, of the total requested"
        delta={ref ? k.cargo_planned_t - ref.cargo_planned_t : undefined}
        deltaText={ref ? `${k.cargo_planned_t - ref.cargo_planned_t >= 0 ? '+' : ''}${Math.round(k.cargo_planned_t - ref.cargo_planned_t)} t` : undefined}
        better="up" refLabel="" />
    ) : (
      <Tile key="r" label="Mean sortie risk" value={fmtPct(k.mean_sortie_risk, 1)}
        delta={ref ? k.mean_sortie_risk - ref.mean_sortie_risk : undefined}
        deltaText={ref ? pts(k.mean_sortie_risk, ref.mean_sortie_risk) : undefined}
        better="down" refLabel="" />
    ),
  ]
}

function EventMenu() {
  const [open, setOpen] = useState(false)
  const presets = useStore((s) => s.presets)
  const loadPresets = useStore((s) => s.loadPresets)
  const propose = useStore((s) => s.propose)
  const setTool = useStore((s) => s.setTool)
  const proposal = useStore((s) => s.proposal)
  const busy = useStore((s) => s.busy)
  const viewTime = useStore((s) => s.viewTime)
  const now = useStore((s) => s.app?.world.now ?? 0)
  const hadr = useStore((s) => s.app?.world.scenario === 'hadr')
  const ref = useRef<HTMLDivElement>(null)
  const at = Math.max(now, Math.round(viewTime / 5) * 5)

  useEffect(() => {
    if (open) void loadPresets()
  }, [open, at, loadPresets])

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [open])

  return (
    <div className="menu-wrap" ref={ref}>
      <button className="btn primary" disabled={!!proposal || !!busy} onClick={() => setOpen(!open)}
        title={proposal ? 'Approve or reject the pending proposal first' : 'Inject an operational event'}>
        Inject<span className="wide-only"> event</span> ▾
      </button>
      {open && (
        <div className="menu" role="menu">
          <div className="menu-row muted">
            Event time <b className="num ink2">{fmtTime(at)}</b> · scrub the timeline to change
          </div>
          <h4>Live situation</h4>
          {!presets && <div className="menu-row muted">Building options from the current plan...</div>}
          {presets?.map((p) => (
            <button key={p.id} className="menu-item" role="menuitem"
              onClick={() => {
                setOpen(false)
                void propose(p.events, p.label)
              }}>
              <b>{p.label}</b>
              <span>{p.detail}</span>
            </button>
          ))}
          <h4>Place on map</h4>
          {hadr ? (
            <button className="menu-item" role="menuitem" onClick={() => { setOpen(false); setTool('CB') }}>
              <b>Draw a thunderstorm cell</b>
              <span>Click anywhere on the map · 25 km, routes must avoid it</span>
            </button>
          ) : (
            <>
              <button className="menu-item" role="menuitem" onClick={() => { setOpen(false); setTool('SAM-MR') }}>
                <b>Drop a medium-range SAM</b>
                <span>Click anywhere on the map · 45 km envelope, Pk 0.5</span>
              </button>
              <button className="menu-item" role="menuitem" onClick={() => { setOpen(false); setTool('SAM-LR') }}>
                <b>Drop a long-range SAM</b>
                <span>Click anywhere on the map · 110 km envelope, Pk 0.55</span>
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function ScenarioMenu() {
  const [open, setOpen] = useState(false)
  const [seed, setSeed] = useState(7)
  const current = useStore((s) => s.app?.world.scenario ?? 'conflict')
  const [kind, setKind] = useState<ScenarioKind>(current)
  const newScenario = useStore((s) => s.newScenario)
  const replan = useStore((s) => s.replan)
  const busy = useStore((s) => s.busy)
  const proposal = useStore((s) => s.proposal)
  return (
    <div className="menu-wrap">
      <button className="btn" disabled={!!busy} onClick={() => setOpen(!open)}>
        Scenario ▾
      </button>
      {open && (
        <div className="menu" style={{ width: 300 }}>
          <h4>Notional scenario</h4>
          <div className="seg" role="group" aria-label="Scenario type" style={{ margin: '0 12px 6px' }}>
            <button aria-pressed={kind === 'conflict'} onClick={() => setKind('conflict')}>Western front</button>
            <button aria-pressed={kind === 'hadr'} onClick={() => setKind('hadr')}>Flood relief (HADR)</button>
          </div>
          <div className="menu-row muted" style={{ fontSize: 11 }}>
            {kind === 'hadr'
              ? 'Monsoon floods in Assam and Bihar: NDRF lift, helicopter rescue and relief drops, domestic airspace only.'
              : 'Air tasking day: strike, SEAD, counter-air, CAS, ISR, tankers and airlift against notional SAMs.'}
          </div>
          <div className="menu-row">
            Seed
            <input type="number" min={1} value={seed} onChange={(e) => setSeed(Number(e.target.value) || 1)} />
            <button className="btn small" onClick={() => { setOpen(false); void newScenario(seed, kind) }}>
              Generate &amp; plan
            </button>
          </div>
          <button className="menu-item" disabled={!!proposal}
            onClick={() => { setOpen(false); void replan() }}>
            <b>Re-optimise from scratch</b>
            <span>Ignores plan stability; use before execution starts</span>
          </button>
        </div>
      )}
    </div>
  )
}

function IntentChip() {
  const intent = useStore((s) => s.app?.world.intent.name)
  const setOpen = useStore((s) => s.setCoaOpen)
  const hasPlan = useStore((s) => !!s.app?.plan)
  const hadr = useStore((s) => s.app?.world.scenario === 'hadr')
  if (!intent || !hasPlan || hadr) return null
  return (
    <button className="intent-chip" onClick={() => setOpen(true)} title="Compare courses of action">
      <span className="wide-only">Intent: </span><b>{intent}</b><span className="wide-only"> · COAs</span> ▸
    </button>
  )
}

function RobustChip() {
  const setOpen = useStore((s) => s.setRobustOpen)
  const hasPlan = useStore((s) => !!s.app?.plan)
  if (!hasPlan) return null
  return (
    <button className="robust-chip" onClick={() => setOpen(true)} title="Stress-test the plan and hold ground spares">
      Robustness ▸
    </button>
  )
}

function BusyPill() {
  const busy = useStore((s) => s.busy)
  const [, tick] = useState(0)
  useEffect(() => {
    if (!busy) return
    const id = setInterval(() => tick((x) => x + 1), 200)
    return () => clearInterval(id)
  }, [busy])
  if (!busy) return null
  return (
    <div className="busy-pill" role="status">
      <span className="spinner" />
      {busy.label} · <span className="num">{((performance.now() - busy.since) / 1000).toFixed(1)} s</span>
    </div>
  )
}

export default function TopBar() {
  const view = useView()
  const viewTime = useStore((s) => s.viewTime)
  return (
    <header className="topbar">
      <div className="brand">
        <b>VAYU-SARTHI</b>
        <span>Air operations decision support</span>
      </div>
      <div className="clocks">
        <div className="clock" title="Decision time: missions launched before this are frozen">
          <label>Now</label>
          <b>{view ? fmtTime(view.world.now) : '--:--'}</b>
        </div>
        <div className="clock" title="Time shown on the map (drag the timeline cursor)">
          <label>View</label>
          <b className="ink2">{fmtTime(viewTime)}</b>
        </div>
      </div>
      <div className="kpis dim-when-busy">
        {view?.kpis && kpiTiles(view.kpis, view.reference, view.referenceLabel, view.world.scenario === 'hadr')}
      </div>
      <div className="actions">
        <BusyPill />
        <IntentChip />
        <RobustChip />
        {view?.world.scenario !== 'hadr' && <WeatherMenu />}
        <EventMenu />
        <ScenarioMenu />
      </div>
      <CoaPanel />
      <RobustPanel />
    </header>
  )
}
