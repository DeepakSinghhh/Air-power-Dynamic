"""Dynamic retasking: apply events, re-optimise with minimal disruption, diff the plans."""
from __future__ import annotations

from pydantic import BaseModel, Field

from . import optimizer
from .candidates import build
from .events import apply_events
from .geo import fmt_time
from .models import Plan, World


class MissionChange(BaseModel):
    mission: str
    priority: int
    change: str                 # ADDED | DROPPED | MODIFIED
    details: list[str] = Field(default_factory=list)


class PlanDiff(BaseModel):
    changes: list[MissionChange] = Field(default_factory=list)
    aircraft_changes: int = 0
    crew_changes: int = 0
    tot_shifts: int = 0
    untouched_missions: int = 0


class RetaskResult(BaseModel):
    world: World
    plan: Plan
    diff: PlanDiff
    notes: list[str]
    naive_diff: PlanDiff | None = None  # same events, re-planned from scratch (for comparison)


def retask(world: World, plan: Plan, events: list, time_limit: float = 10.0,
           compare_naive: bool = False) -> RetaskResult:
    new_world, notes = apply_events(world, events)
    cands = build(new_world)
    new_plan = optimizer.solve(new_world, cands, baseline=plan, time_limit=time_limit)
    res = RetaskResult(world=new_world, plan=new_plan, diff=diff_plans(new_world, plan, new_plan), notes=notes)
    if compare_naive:
        naive = optimizer.solve(new_world, cands, baseline=plan, time_limit=time_limit, churn=False)
        res.naive_diff = diff_plans(new_world, plan, naive)
    return res


def diff_plans(world: World, old: Plan, new: Plan) -> PlanDiff:
    d = PlanDiff()
    ids = sorted(set(old.assignments) | set(new.assignments),
                 key=lambda m: -(world.missions[m].priority if m in world.missions else 0))
    for mid in ids:
        a, b = old.assignments.get(mid), new.assignments.get(mid)
        prio = world.missions[mid].priority if mid in world.missions else 0
        if a and not b:
            reasons = new.unassigned.get(mid) or ["Mission cancelled."]
            d.changes.append(MissionChange(mission=mid, priority=prio, change="DROPPED", details=reasons))
            d.aircraft_changes += len(a.sorties)
            continue
        if b and not a:
            d.changes.append(MissionChange(mission=mid, priority=prio, change="ADDED", details=[
                f"TOT {fmt_time(b.tot)}: " + ", ".join(s.tail for s in b.sorties)
                + (f" + tanker {', '.join(b.tankers)}" if b.tankers else "")]))
            d.aircraft_changes += len(b.sorties)
            continue
        details = []
        ta, tb = {s.tail for s in a.sorties}, {s.tail for s in b.sorties}
        for t in sorted(tb - ta):
            details.append(f"+ {t}")
        for t in sorted(ta - tb):
            details.append(f"- {t}")
        d.aircraft_changes += len(tb - ta) + len(ta - tb)
        ca, cb = {s.crew for s in a.sorties}, {s.crew for s in b.sorties}
        if ca != cb:
            n = len(cb - ca)
            d.crew_changes += n
            details.append(f"crews changed: {n}")
        old_r = {s.tail: s for s in a.sorties}
        for s in b.sorties:
            o = old_r.get(s.tail)
            if o and abs(o.route_km - s.route_km) > 10:
                details.append(f"{s.tail} rerouted {o.route_km:.0f} -> {s.route_km:.0f} km, "
                               f"risk {o.risk:.0%} -> {s.risk:.0%}")
        if a.tot != b.tot:
            d.tot_shifts += 1
            details.append(f"TOT {fmt_time(a.tot)} -> {fmt_time(b.tot)}")
        if set(a.tankers) != set(b.tankers):
            details.append(f"tankers {sorted(a.tankers) or '-'} -> {sorted(b.tankers) or '-'}")
        if details:
            d.changes.append(MissionChange(mission=mid, priority=prio, change="MODIFIED", details=details))
        else:
            d.untouched_missions += 1
    return d
