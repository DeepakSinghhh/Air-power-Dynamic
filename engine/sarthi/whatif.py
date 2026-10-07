"""What would it take? Counterfactual explanations for an unplanned mission.

"Why not?" says what blocks a mission; this module says what would unblock it, and at what cost. A few
single relaxations (accept more route risk, raise the priority, widen the time window, resupply
weapons) are each re-solved as a minimal-disruption retask, in parallel. Each result is a ready-made
event list, so the commander can turn the one they like into a normal proposal.
"""
from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from .candidates import build
from .events import PriorityChange, Resupply, RiskAcceptance, WindowChange
from .kpi import kpis
from .models import Plan, World
from .retask import retask


class Option(BaseModel):
    id: str
    label: str
    detail: str
    events: list


class Outcome(BaseModel):
    id: str
    label: str
    detail: str
    planned: bool
    aircraft_changes: int
    dropped: list[str]
    fulfilment_before: float
    fulfilment_after: float
    events: list


def options(world: World, plan: Plan, mid: str, cands=None, _prefix: str = "") -> list[Option]:
    m = world.missions[mid]
    at = world.now
    cands = cands or build(world)
    out: list[Option] = []
    # A strike blocked by its unplanned SEAD: relax the SEAD instead.
    if m.depends_on and m.depends_on in world.missions and m.depends_on not in plan.assignments and not _prefix:
        out += [o.model_copy(update={"id": f"dep-{o.id}"})
                for o in options(world, plan, m.depends_on, cands, _prefix=f"{m.depends_on}: ")
                if o.id != "priority"]
    rej = cands.rejects.get(mid, {})
    risky = [r for (x, _), r in cands.routes.items() if x == mid and math.isfinite(r.km)]
    ceiling = world.risk_ceiling(m)
    if risky and any("risk" in k for k in rej):
        best = min(r.risk for r in risky)
        target = min(0.9, math.ceil((best + 0.02) * 20) / 20)  # next 5% step above the least-risk route
        if target > ceiling:
            out.append(Option(id="risk", label=f"{_prefix}Accept route risk up to {target:.0%}",
                              detail=f"now {ceiling:.0%}; least-risk route is {best:.0%}",
                              events=[RiskAcceptance(at=at, mission=mid,
                                                     max_risk=round(target / max(world.intent.risk_scale, 1e-6), 3))]))
    if m.priority < 10 and not _prefix:
        out.append(Option(id="priority", label=f"Raise to P10 (now P{m.priority})",
                          detail="takes aircraft and crews from lower-priority missions if it must",
                          events=[PriorityChange(at=at, mission=mid, priority=10)]))
    lo, hi = max(world.now, m.tot_earliest - 60), m.tot_latest + 60
    if (lo, hi) != (m.tot_earliest, m.tot_latest):
        out.append(Option(id="window", label=f"{_prefix}Widen the TOT window by an hour each side",
                          detail=f"{_t(m.tot_earliest)}-{_t(m.tot_latest)} → {_t(lo)}-{_t(hi)}",
                          events=[WindowChange(at=at, mission=mid, tot_earliest=lo, tot_latest=hi)]))
    if m.weapon:
        need = max(1, m.package) * m.weapons_per_aircraft
        # The base whose aircraft could fly it if they had the weapons: role, carriage and range ignoring stock.
        able = {}
        for a in world.aircraft.values():
            t = world.types[a.type]
            r = cands.routes.get((mid, a.base))
            if m.role in t.roles and t.weapons.get(m.weapon, 0) >= m.weapons_per_aircraft and a.serviceable \
                    and r is not None and r.km <= t.combat_radius_km * world.aar_extension and r.risk <= ceiling:
                able[a.base] = able.get(a.base, 0) + 1
        short = [b for b in able if world.bases[b].stocks.get(m.weapon, 0) - _used(world, plan, b, m.weapon) < need]
        if short:
            b = max(short, key=lambda b: able[b])
            out.append(Option(id="resupply", label=f"{_prefix}Resupply {need}× {m.weapon} to {world.bases[b].name}",
                              detail=f"{able[b]} capable aircraft there; stock left after the plan is "
                                     f"{world.bases[b].stocks.get(m.weapon, 0) - _used(world, plan, b, m.weapon)}",
                              events=[Resupply(at=at, base=b, weapon=m.weapon, qty=need)]))
    return out


def what_would_it_take(world: World, plan: Plan, mid: str, time_limit: float = 3.0) -> list[Outcome]:
    opts = options(world, plan, mid)
    if not opts:
        return []
    before = kpis(world, plan)["priority_weighted_fulfilment"]
    workers = max(2, (os.cpu_count() or 4) // len(opts) + 1)

    def run(o: Option) -> Outcome:
        res = retask(world, plan, o.events, time_limit, workers=workers)
        res_plan = res.plan
        dropped = sorted((x for x in plan.assignments if x not in res_plan.assignments and x != mid),
                         key=lambda x: -world.missions[x].priority)
        return Outcome(id=o.id, label=o.label, detail=o.detail, planned=mid in res_plan.assignments,
                       aircraft_changes=res.diff.aircraft_changes,
                       dropped=[f"{x} (P{world.missions[x].priority})" for x in dropped],
                       fulfilment_before=before,
                       # Judged with the original priorities, so raising one is not mistaken for a loss.
                       fulfilment_after=kpis(world, res_plan)["priority_weighted_fulfilment"],
                       events=[e.model_dump() for e in o.events])

    with ThreadPoolExecutor(max_workers=len(opts)) as pool:
        out = list(pool.map(run, opts))
    # Ones that work first, least disruptive first.
    return sorted(out, key=lambda o: (not o.planned, len(o.dropped), o.aircraft_changes))


def _used(world: World, plan: Plan, base: str, weapon: str) -> int:
    n = 0
    for mid, a in plan.assignments.items():
        m = world.missions.get(mid)
        if m and m.weapon == weapon:
            n += sum(m.weapons_per_aircraft for s in a.sorties if s.base == base)
    return n


def _t(t: int) -> str:
    from .geo import fmt_time
    return fmt_time(t)
