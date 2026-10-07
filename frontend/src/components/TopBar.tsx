import { useEffect, useRef, useState } from 'react'

import { useStore, useView } from '../store'
import type { Kpis } from '../types'
import { fmtPct, fmtTime } from '../util'

type Better = 'up' | 'down' | 'none'

function Tile(props: {
  label: string
  value: string
  delta?: number
  deltaText?: string
  better?: Better
  refLabel?: string
  title?: string
}) {
  const { label, value, delta, deltaText, better = 'none', refLabel, title } = props
  let cls = ''
  let glyph = ''
  if (delta !== undefined && Math.abs(delta) > 1e-9) {
    const up = delta > 0
    glyph = up ? '▲' : '▼'
    if (better !== 'none') cls = (up ? 'up' : 'down') + ((up ? better === 'up' : better === 'down') ? '-good' : '-bad')
  }
  return (
    <div className="tile" title={title}>
      <label>{label}</label>
      <b className="num">{value}</b>
      {deltaText !== undefined && (
        <small>
          <span className={cls}>
            {glyph} {deltaText}
          </span>{' '}
          {refLabel}
        </small>
      )}
    </div>
  )
}

function kpiTiles(k: Kpis, ref: Kpis | null, refLabel: string) {
  const pts = (a: number, b: number) => `${a - b >= 0 ? '+' : ''}${((a - b) * 100).toFixed(1)} pts`
  const n = (a: number, b: number) => `${a - b >= 0 ? '+' : ''}${a - b}`
  return [
    <Tile key="f" label="Mission fulfilment" value={fmtPct(k.priority_weighted_fulfilment, 1)}
      title="Priority-weighted share of missions planned"
      delta={ref ? k.priority_weighted_fulfilment - ref.priority_weighted_fulfilment : undefined}
      deltaText={ref ? pts(k.priority_weighted_fulfilment, ref.priority_weighted_fulfilment) : undefined}
      better="up" refLabel={refLabel} />,
    <Tile key="ev" label="Expected value" value={fmtPct(k.expected_value, 1)}
      title="Priority-weighted value after serviceability and attrition risk"
      delta={ref ? k.expected_value - ref.expected_value : undefined}
      deltaText={ref ? pts(k.expected_value, ref.expected_value) : undefined}
      better="up" refLabel={refLabel} />,
    <Tile key="m" label="Missions planned" value={`${k.missions_planned} / ${k.missions_total}`}
      delta={ref ? k.missions_planned - ref.missions_planned : undefined}
      deltaText={ref ? n(k.missions_planned, ref.missions_planned) : undefined}
      better="up" refLabel="" />,
    <Tile key="s" label="Sorties" value={`${k.sorties}`}
      deltaText={k.tanker_sorties ? `+${k.tanker_sorties} tanker` : 'no tanker'} />,
    <Tile key="r" label="Mean sortie risk" value={fmtPct(k.mean_sortie_risk, 1)}
      delta={ref ? k.mean_sortie_risk - ref.mean_sortie_risk : undefined}
      deltaText={ref ? pts(k.mean_sortie_risk, ref.mean_sortie_risk) : undefined}
      better="down" refLabel="" />,
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
        Inject event ▾
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
          <button className="menu-item" role="menuitem" onClick={() => { setOpen(false); setTool('SAM-MR') }}>
            <b>Drop a medium-range SAM</b>
            <span>Click anywhere on the map · 45 km envelope, Pk 0.5</span>
          </button>
          <button className="menu-item" role="menuitem" onClick={() => { setOpen(false); setTool('SAM-LR') }}>
            <b>Drop a long-range SAM</b>
            <span>Click anywhere on the map · 110 km envelope, Pk 0.55</span>
          </button>
        </div>
      )}
    </div>
  )
}

function ScenarioMenu() {
  const [open, setOpen] = useState(false)
  const [seed, setSeed] = useState(7)
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
          <div className="menu-row">
            Seed
            <input type="number" min={1} value={seed} onChange={(e) => setSeed(Number(e.target.value) || 1)} />
            <button className="btn small" onClick={() => { setOpen(false); void newScenario(seed) }}>
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
        {view?.kpis && kpiTiles(view.kpis, view.reference, view.referenceLabel)}
      </div>
      <div className="actions">
        <BusyPill />
        <EventMenu />
        <ScenarioMenu />
      </div>
    </header>
  )
}
