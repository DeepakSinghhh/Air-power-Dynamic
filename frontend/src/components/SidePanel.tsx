import type { ReactNode } from 'react'

import { eventTime, useStore, useView, type View } from '../store'
import { C, FAMILY_COLOR, ROLE_NAME, STATUS_COLOR, STATUS_ICON, roleColor } from '../theme'
import type { Base, EngineEvent, Mission, Threat } from '../types'
import { FogChart, useMetForBase } from './Weather'
import { baseStatus, fmtDur, reserveAt, fmtPct, fmtTime, haversineKm, routePath, serviceableAt } from '../util'

export default function SidePanel() {
  const view = useView()!
  const selection = useStore((s) => s.selection)
  let detail: ReactNode = <PlanSummary view={view} />
  if (selection?.kind === 'mission' && view.world.missions[selection.id]) detail = <MissionCard view={view} m={view.world.missions[selection.id]} />
  else if (selection?.kind === 'base') detail = <BaseCard view={view} b={view.world.bases[selection.id]} />
  else if (selection?.kind === 'threat' && view.world.threats[selection.id]) detail = <ThreatCard view={view} t={view.world.threats[selection.id]} />
  else if (selection?.kind === 'aircraft') detail = <AircraftCard view={view} tail={selection.id} />
  return (
    <aside className="panel right" aria-label="Details">
      <div className="panel-body">
        {view.proposal && <DiffPanel view={view} />}
        <div className="dim-when-busy">{detail}</div>
      </div>
    </aside>
  )
}

function useAct() {
  const propose = useStore((s) => s.propose)
  const busy = useStore((s) => s.busy)
  const pending = useStore((s) => !!s.proposal)
  return {
    disabled: !!busy || pending,
    act: (label: string, make: (at: number) => EngineEvent) => void propose([make(eventTime())], label),
  }
}

// ---------- proposal review ----------

function DiffPanel({ view }: { view: View }) {
  const approve = useStore((s) => s.approve)
  const reject = useStore((s) => s.reject)
  const label = useStore((s) => s.proposalLabel)
  const busy = useStore((s) => s.busy)
  const select = useStore((s) => s.select)
  const p = view.proposal!
  const before = view.reference
  const naive = p.naive_diff
  return (
    <>
      <div className="card proposal-head">
        <div className="muted" style={{ fontSize: 11, letterSpacing: '0.08em' }}>PROPOSED RETASK · AWAITING DECISION</div>
        <h2>{label}</h2>
        {p.notes.map((n, i) => <p key={i} className="ink2" style={{ fontSize: 12 }}>{n}</p>)}
        <div className="stat-row">
          {p.diff.aircraft_changes === 0 && p.diff.spare_changes > 0 ? (
            <div className="stat">
              <b className="num">{p.diff.spare_changes}</b>
              <span>ground spares changed · 0 flying aircraft reassigned</span>
            </div>
          ) : (
            <div className="stat">
              <b className="num">{p.diff.aircraft_changes}</b>
              <span>aircraft reassigned{naive ? ` (naive re-plan: ${naive.aircraft_changes})` : ''}</span>
            </div>
          )}
          <div className="stat">
            <b className="num">{p.diff.untouched_missions}</b>
            <span>missions untouched</span>
          </div>
        </div>
        <div className="stat-row" style={{ marginTop: 0 }}>
          <div className="stat">
            <b className="num">{before ? fmtPct(before.priority_weighted_fulfilment) : '-'} → {fmtPct(p.kpis.priority_weighted_fulfilment)}</b>
            <span>mission fulfilment</span>
          </div>
          {p.diff.aircraft_changes === 0 && p.diff.spare_changes > 0 && before ? (
            <div className="stat">
              <b className="num">{fmtPct(before.expected_value)} → {fmtPct(p.kpis.expected_value)}</b>
              <span>expected value on the day</span>
            </div>
          ) : (
            <div className="stat">
              <b className="num">{p.plan.solve_seconds.toFixed(1)} s</b>
              <span>{p.plan.status.toLowerCase()} · {p.diff.crew_changes} crew, {p.diff.tot_shifts} TOT changes</span>
            </div>
          )}
        </div>
        <div className="btn-row">
          <button className="btn primary" disabled={!!busy} onClick={() => void approve()}>Approve &amp; issue changes</button>
          <button className="btn danger" disabled={!!busy} onClick={() => void reject()}>Reject</button>
        </div>
      </div>
      {p.diff.changes.length === 0 && <div className="empty">No changes needed: the plan absorbs this event.</div>}
      {p.diff.changes.map((c) => (
        <div key={c.mission} className="change" onClick={() => select({ kind: 'mission', id: c.mission })}>
          <div className="head">
            <span className={`chg-icon chg-${c.change}`}>{c.change === 'ADDED' ? '+' : c.change === 'DROPPED' ? '−' : '~'}</span>
            <b>{c.mission}</b> <span className="prio">P{c.priority}</span>
            <span className="muted" style={{ fontSize: 12 }}>{c.change.toLowerCase()}</span>
          </div>
          <ul>{c.details.slice(0, 6).map((d, i) => <li key={i}>{d}</li>)}</ul>
        </div>
      ))}
    </>
  )
}

// ---------- summaries & cards ----------

function PlanSummary({ view }: { view: View }) {
  const history = useStore((s) => s.app?.history ?? [])
  const select = useStore((s) => s.select)
  const { world, plan } = view
  const unplanned = Object.values(world.missions).filter((m) => !plan?.assignments[m.id]).sort((a, b) => b.priority - a.priority)
  return (
    <>
      <div className="card">
        <h5 style={{ marginTop: 0 }}>Plan</h5>
        <dl className="kv">
          <dt>Solver</dt><dd>{plan?.solver} · {plan?.status.toLowerCase()} in {plan?.solve_seconds.toFixed(1)} s</dd>
          <dt>Decision time</dt><dd>{fmtTime(world.now)} (missions launched earlier are frozen)</dd>
          <dt>Fleet</dt><dd>{Object.values(world.aircraft).filter((a) => a.serviceable).length}/{Object.keys(world.aircraft).length} serviceable · {Object.keys(world.crews).length} crews</dd>
          <dt>Threats</dt><dd>{Object.keys(world.threats).length} known SAM sites</dd>
          <dt>Intent</dt><dd>{world.intent.name}</dd>
        </dl>
        <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
          Select a mission, base, threat or aircraft on the map, list or timeline. Use <b>Inject event</b> to test the plan.
        </p>
      </div>
      <div className="card">
        <h5 style={{ marginTop: 0 }}>Why not planned ({unplanned.length})</h5>
        {unplanned.length === 0 && <p className="muted">Every mission is planned.</p>}
        {unplanned.map((m) => (
          <div key={m.id} style={{ marginBottom: 8, cursor: 'pointer' }} onClick={() => select({ kind: 'mission', id: m.id })}>
            <b>{m.id}</b> <span className="prio">P{m.priority}</span> <span className="muted">{m.role}</span>
            <ul className="why">{(plan?.unassigned[m.id] ?? []).slice(0, 2).map((w, i) => <li key={i}>{w}</li>)}</ul>
          </div>
        ))}
      </div>
      <div className="card">
        <h5 style={{ marginTop: 0 }}>Decision log</h5>
        {history.length === 0 && <p className="muted">No retasking decisions yet.</p>}
        {[...history].reverse().map((h) => (
          <div key={h.id} style={{ marginBottom: 6, fontSize: 12 }}>
            <span className="tag">
              <span className="dot" style={{ background: h.decision === 'approved' ? C.good : C.critical }} />
              {h.decision}
            </span>{' '}
            <span className="num">{fmtTime(h.now)}</span> · {h.notes.join('; ')}
            {h.aircraft_changes !== undefined && (
              <span className="muted"> · {h.aircraft_changes} aircraft changed (naive {h.naive_aircraft_changes})</span>
            )}
          </div>
        ))}
      </div>
    </>
  )
}

function MissionCard({ view, m }: { view: View; m: Mission }) {
  const select = useStore((s) => s.select)
  const { act, disabled } = useAct()
  const { plan } = view
  const a = plan?.assignments[m.id]
  return (
    <div className="card">
      <h2>
        <span className={`swatch${a ? '' : ' hollow'}`} style={{ background: roleColor(m.role), borderColor: roleColor(m.role), width: 14, height: 14 }} />
        {m.id} <span className="prio">P{m.priority}</span>
      </h2>
      <div className="ink2">{ROLE_NAME[m.role]} · {m.label}</div>
      <h5>Tasking</h5>
      <dl className="kv">
        <dt>TOT window</dt><dd>{fmtTime(m.tot_earliest)}-{fmtTime(m.tot_latest)}</dd>
        <dt>Planned TOT</dt><dd>{a ? fmtTime(a.tot) : '-'}</dd>
        <dt>Requirement</dt>
        <dd>{m.role === 'AIRLIFT' ? `${m.cargo_t} t cargo` : `${m.package} aircraft`}{m.weapon ? ` · ${m.weapons_per_aircraft}x ${m.weapon} each` : ''}</dd>
        <dt>On station</dt><dd>{fmtDur(m.on_station_min)}</dd>
        <dt>Risk ceiling</dt><dd>{fmtPct(m.max_risk)}</dd>
        <dt>Objective</dt><dd>{m.lat.toFixed(2)}N {m.lon.toFixed(2)}E</dd>
        {m.depends_on && (
          <>
            <dt>Depends on</dt>
            <dd><a href="#" onClick={(e) => { e.preventDefault(); select({ kind: 'mission', id: m.depends_on! }) }} style={{ color: C.accentInk }}>{m.depends_on}</a> {m.dep_lag_min}-{m.dep_lag_max} min before</dd>
          </>
        )}
      </dl>
      {a ? (
        <>
          <h5>Package</h5>
          <table className="mini">
            <thead><tr><th>Aircraft</th><th>Crew</th><th>Launch</th><th>Rec.</th><th>Risk</th></tr></thead>
            <tbody>
              {a.sorties.map((s) => (
                <tr key={s.tail} className="clickable" onClick={() => select({ kind: 'aircraft', id: s.tail })}>
                  <td>{s.tail}</td>
                  <td className="muted">{s.crew?.split('-').pop() ?? '-'}</td>
                  <td>{fmtTime(s.launch)}</td>
                  <td>{fmtTime(s.recover)}</td>
                  <td>{fmtPct(s.risk, 1)}{s.needs_aar ? ' · AAR' : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {a.spares.length > 0 && (
            <p className="ink2" style={{ fontSize: 12 }}>
              Ground spare{a.spares.length > 1 ? 's' : ''}:{' '}
              {a.spares.map((s, i) => (
                <span key={s.tail}>
                  {i > 0 && ', '}
                  <a href="#" style={{ color: C.accentInk }} onClick={(e) => { e.preventDefault(); select({ kind: 'aircraft', id: s.tail }) }}>{s.tail}</a>
                </span>
              ))}{' '}
              <span className="muted">· starts up with the package, launches if a primary is U/S</span>
            </p>
          )}
          {a.tanker_sorties.length > 0 && (
            <p className="ink2" style={{ fontSize: 12 }}>
              <span className="swatch" style={{ background: FAMILY_COLOR.support }} /> Tanker{' '}
              {a.tanker_sorties.map((t) => `${t.tail} (${fmtTime(t.launch)}-${fmtTime(t.recover)})`).join(', ')}
            </p>
          )}
        </>
      ) : (
        <>
          <h5>Why not planned</h5>
          <ul className="why">{(plan?.unassigned[m.id] ?? ['No explanation available.']).map((w, i) => <li key={i}>{w}</li>)}</ul>
        </>
      )}
      <h5>Commander's intent</h5>
      <div className="btn-row" style={{ marginTop: 0 }}>
        {m.priority < 10 && (
          <button className="btn small" disabled={disabled}
            onClick={() => act(`Raise ${m.id} to P10`, (at) => ({ kind: 'priority_change', at, mission: m.id, priority: 10 }))}>
            Raise to P10
          </button>
        )}
        <button className="btn small danger" disabled={disabled}
          onClick={() => act(`Cancel ${m.id}`, (at) => ({ kind: 'cancel_mission', at, mission: m.id }))}>
          Cancel mission
        </button>
      </div>
    </div>
  )
}

function BaseCard({ view, b }: { view: View; b: Base }) {
  const viewTime = useStore((s) => s.viewTime)
  const select = useStore((s) => s.select)
  const { act, disabled } = useAct()
  const { world, plan } = view
  const st = baseStatus(b, viewTime)
  const sv = serviceableAt(world, b.id)
  const tasked = new Map<string, number>()
  Object.values(plan?.assignments ?? {}).forEach((a) =>
    [...a.sorties, ...a.tanker_sorties].forEach((s) => s.base === b.id && tasked.set(s.tail, (tasked.get(s.tail) ?? 0) + 1)),
  )
  const aircraft = Object.values(world.aircraft).filter((a) => a.base === b.id)
  return (
    <div className="card">
      <h2>
        <span className="swatch" style={{ background: STATUS_COLOR[st.status], width: 14, height: 14 }} />
        {b.name} <span className="muted" style={{ fontSize: 13, fontWeight: 400 }}>{b.id}</span>
      </h2>
      <div className="ink2">{STATUS_ICON[st.status]} {st.text}</div>
      <h5>Readiness</h5>
      <dl className="kv">
        <dt>Aircraft</dt><dd>{sv.serviceable}/{sv.total} serviceable · {tasked.size} tasked</dd>
        <dt>Alert reserve</dt><dd>{reserveAt(world, b.id)} fighters held at all times{world.intent.name !== 'Max effect' ? ` (intent: ${world.intent.name})` : ''}</dd>
        <dt>Stocks</dt><dd>{Object.entries(b.stocks).map(([k, v]) => `${k} ${v}`).join(' · ') || 'none'}</dd>
        <dt>Closures</dt><dd>{b.closures.map((c) => `${fmtTime(c.start)}-${fmtTime(c.end)} ${c.reason}`).join('; ') || 'none'}</dd>
      </dl>
      <BaseFog baseId={b.id} />
      <h5>What if</h5>
      <div className="btn-row" style={{ marginTop: 0 }}>
        <button className="btn small" disabled={disabled}
          onClick={() => act(`Weather closes ${b.name} for 3 h`, (at) => ({ kind: 'base_closure', at, base: b.id, start: at + 60, end: at + 240, reason: 'weather below minima' }))}>
          Weather: close 3 h (in 1 h)
        </button>
        <button className="btn small danger" disabled={disabled}
          onClick={() => act(`Runway cratered at ${b.name}`, (at) => ({ kind: 'base_closure', at, base: b.id, start: at, end: at + 180, reason: 'runway cratered' }))}>
          Runway cratered: close 3 h now
        </button>
      </div>
      {aircraft.length > 0 && (
        <>
          <h5>Aircraft</h5>
          <table className="mini">
            <thead><tr><th>Tail</th><th>Type</th><th>P(svc)</th><th>Sorties</th></tr></thead>
            <tbody>
              {aircraft.map((a) => (
                <tr key={a.tail} className="clickable" onClick={() => select({ kind: 'aircraft', id: a.tail })}>
                  <td style={{ color: a.serviceable ? undefined : C.muted, textDecoration: a.serviceable ? undefined : 'line-through' }}>{a.tail.split('-').slice(1).join('-')}</td>
                  <td className="muted">{a.type}</td>
                  <td><span className="meter"><i style={{ width: `${a.p_serviceable * 100}%` }} /></span> {fmtPct(a.p_serviceable)}</td>
                  <td>{a.serviceable ? tasked.get(a.tail) ?? 0 : 'U/S'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}

function BaseFog({ baseId }: { baseId: string }) {
  const { met, windows, threshold } = useMetForBase(baseId)
  const now = useStore((s) => s.app?.world.now ?? 0)
  const source = useStore((s) => s.met?.forecast.label)
  if (!met) return null
  const peak = Math.max(...met.p_fog)
  const at = met.times[met.p_fog.indexOf(peak)]
  return (
    <>
      <h5>Fog forecast</h5>
      <FogChart met={met} threshold={threshold} width={320} height={met.observed_vis_m ? 72 : 64} axis now={now} />
      <p className="ink2" style={{ fontSize: 12 }}>
        Peak P(visibility &lt; 1 km) <b className="num">{fmtPct(peak)}</b> at {fmtTime(at)}
        {windows.length > 0
          ? ` · above ${fmtPct(threshold)}: ${windows.map((w) => `${fmtTime(w.start)}-${fmtTime(w.end + 1)}`).join(', ')}`
          : ` · stays below ${fmtPct(threshold)}`}
      </p>
      <p className="muted" style={{ fontSize: 11 }}>
        {source}{met.observed_station ? ` · ticks below: observed at ${met.observed_station} (white = fog, grey = clear, none = no report)` : ' · no nearby METAR to verify against'}
      </p>
    </>
  )
}

function ThreatCard({ view, t }: { view: View; t: Threat }) {
  const select = useStore((s) => s.select)
  const { world, plan, envelopes } = view
  const env = envelopes[t.id]
  const reach = env?.r_eff_km ?? t.radius_km
  // Planned routes that pass through the (uncertainty-inflated) envelope.
  const exposed = Object.values(plan?.assignments ?? {}).filter((a) => {
    const m = world.missions[a.mission]
    return a.sorties.some((s) => routePath(s, m, world.bases[s.base]).some((p) => haversineKm(p, [t.lon, t.lat]) <= reach))
  })
  return (
    <div className="card">
      <h2><span className="swatch" style={{ background: C.critical, width: 14, height: 14 }} />{t.id}</h2>
      <div className="ink2">{t.kind} · Pk {t.pk.toFixed(2)} for a full envelope crossing</div>
      <h5>Intelligence</h5>
      <dl className="kv">
        <dt>Position</dt><dd>{t.lat.toFixed(2)}N {t.lon.toFixed(2)}E</dd>
        <dt>Envelope</dt><dd>{t.radius_km.toFixed(0)} km</dd>
        <dt>Last confirmed</dt><dd>{fmtTime(t.observed_at)} ({fmtDur(env?.age_min ?? 0)} ago)</dd>
        <dt>Relocation</dt><dd>{t.mobile_kmh ? `${t.mobile_kmh} km/h → may be anywhere within ${reach.toFixed(0)} km` : 'static site'}</dd>
      </dl>
      <h5>Planned routes in reach ({exposed.length})</h5>
      {exposed.length === 0 && <p className="muted">No planned route enters this envelope; routing goes around it.</p>}
      {exposed.map((a) => (
        <div key={a.mission} style={{ cursor: 'pointer', fontSize: 12, marginBottom: 3 }} onClick={() => select({ kind: 'mission', id: a.mission })}>
          <span className="swatch" style={{ background: roleColor(world.missions[a.mission].role) }} /> <b>{a.mission}</b>{' '}
          <span className="muted">max risk {fmtPct(Math.max(...a.sorties.map((s) => s.risk)), 1)}</span>
        </div>
      ))}
    </div>
  )
}

function AircraftCard({ view, tail }: { view: View; tail: string }) {
  const select = useStore((s) => s.select)
  const { act, disabled } = useAct()
  const { world, plan } = view
  const a = world.aircraft[tail]
  if (!a) return <div className="empty">Unknown aircraft.</div>
  const sorties = Object.values(plan?.assignments ?? {}).flatMap((as) =>
    [
      ...as.sorties.map((s) => ({ s, mission: as.mission, tanker: false, spare: false })),
      ...as.tanker_sorties.map((s) => ({ s, mission: as.mission, tanker: true, spare: false })),
      ...as.spares.map((s) => ({ s, mission: as.mission, tanker: false, spare: true })),
    ].filter((x) => x.s.tail === tail),
  ).sort((x, y) => x.s.launch - y.s.launch)
  const type = world.types[a.type]
  return (
    <div className="card">
      <h2>{tail}</h2>
      <div className="ink2">{a.type} · {world.bases[a.base]?.name} · {a.serviceable ? 'serviceable' : 'unserviceable'}</div>
      <h5>Status</h5>
      <dl className="kv">
        <dt>P(serviceable)</dt><dd><span className="meter"><i style={{ width: `${a.p_serviceable * 100}%` }} /></span> {fmtPct(a.p_serviceable)} (maintenance model)</dd>
        <dt>Roles</dt><dd>{type.roles.join(', ')}</dd>
        <dt>Radius</dt><dd>{type.combat_radius_km} km · turnaround {fmtDur(type.turnaround_min)}</dd>
      </dl>
      <h5>Sorties ({sorties.length})</h5>
      {sorties.length === 0 && <p className="muted">Not tasked: available capacity.</p>}
      {sorties.map(({ s, mission, tanker, spare }) => (
        <div key={`${mission}${s.launch}${spare}`} style={{ cursor: 'pointer', fontSize: 12, marginBottom: 3 }} onClick={() => select({ kind: 'mission', id: mission })}>
          <span className="num">{fmtTime(s.launch)}-{fmtTime(s.recover)}</span> · <b>{mission}</b>{tanker ? ' (tanker)' : ''}{spare ? ' (ground spare)' : ''}{s.crew ? <span className="muted"> · crew {s.crew.split('-').pop()}</span> : null}
        </div>
      ))}
      {a.serviceable && (
        <div className="btn-row">
          <button className="btn small danger" disabled={disabled}
            onClick={() => act(`${tail} unserviceable`, (at) => ({ kind: 'aircraft_down', at, tails: [tail], reason: 'reported unserviceable' }))}>
            Ground this aircraft
          </button>
        </div>
      )}
    </div>
  )
}
