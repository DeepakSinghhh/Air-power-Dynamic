import { useState } from 'react'

import { useStore, useView } from '../store'
import { roleColor } from '../theme'
import { fmtPct, fmtTime, maxRisk } from '../util'
import Copilot from './Copilot'

type Filter = 'all' | 'planned' | 'unplanned'

export default function MissionList() {
  const view = useView()!
  const selection = useStore((s) => s.selection)
  const select = useStore((s) => s.select)
  const setHoverMission = useStore((s) => s.setHoverMission)
  const [filter, setFilter] = useState<Filter>('all')
  const tab = useStore((s) => s.leftTab)
  const setTab = useStore((s) => s.setLeftTab)
  const { world, plan, proposal } = view
  const change = new Map(proposal?.diff.changes.map((c) => [c.mission, c.change]) ?? [])

  const missions = Object.values(world.missions)
    .filter((m) => filter === 'all' || (filter === 'planned') === !!plan?.assignments[m.id])
    .sort((a, b) => b.priority - a.priority || (plan?.assignments[a.id]?.tot ?? a.tot_earliest) - (plan?.assignments[b.id]?.tot ?? b.tot_earliest))
  const nPlanned = Object.keys(plan?.assignments ?? {}).length

  return (
    <aside className="panel" aria-label={tab === 'copilot' ? 'Copilot' : 'Missions'}>
      <div className="panel-tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'missions'} onClick={() => setTab('missions')}>Missions</button>
        <button role="tab" aria-selected={tab === 'copilot'} onClick={() => setTab('copilot')} title="Ask the copilot (press /)">
          Copilot
        </button>
      </div>
      {tab === 'copilot' ? <Copilot /> : <>
      <div className="panel-head">
        <div className="seg" role="group" aria-label="Filter missions">
          <button aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>All {Object.keys(world.missions).length}</button>
          <button aria-pressed={filter === 'planned'} onClick={() => setFilter('planned')}>Planned {nPlanned}</button>
          <button aria-pressed={filter === 'unplanned'} onClick={() => setFilter('unplanned')}>
            Not {Object.keys(world.missions).length - nPlanned}
          </button>
        </div>
      </div>
      <div className="panel-body dim-when-busy">
        {missions.map((m) => {
          const a = plan?.assignments[m.id]
          const sel = selection?.kind === 'mission' && selection.id === m.id
          const chg = change.get(m.id)
          return (
            <div key={m.id} className={`mrow${sel ? ' sel' : ''}`} role="button" tabIndex={0}
              onClick={() => select({ kind: 'mission', id: m.id })}
              onKeyDown={(e) => e.key === 'Enter' && select({ kind: 'mission', id: m.id })}
              onPointerEnter={() => setHoverMission(m.id)}
              onPointerLeave={() => setHoverMission(null)}>
              <span className={`swatch${a ? '' : ' hollow'}`} style={{ background: roleColor(m.role), borderColor: roleColor(m.role) }} />
              <div style={{ minWidth: 0 }}>
                <div>
                  <span className="id">{m.id}</span> <span className="prio">P{m.priority}</span>{' '}
                  {chg && (
                    <span className={`chg-icon chg-${chg}`} style={{ width: 16, height: 16, fontSize: 11 }} title={chg}>
                      {chg === 'ADDED' ? '+' : chg === 'DROPPED' ? '−' : '~'}
                    </span>
                  )}
                </div>
                <div className="sub">{m.role} · {m.label}</div>
              </div>
              <div className="right num">
                {a ? (
                  <>
                    <div>{fmtTime(a.tot)}</div>
                    <div className="muted">risk {fmtPct(maxRisk(a))}</div>
                  </>
                ) : (
                  <div className="muted">not planned</div>
                )}
              </div>
            </div>
          )
        })}
        {missions.length === 0 && <div className="empty">No missions in this filter.</div>}
      </div>
      </>}
    </aside>
  )
}
