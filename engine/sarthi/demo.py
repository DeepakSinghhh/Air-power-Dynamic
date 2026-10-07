"""Scripted demo: plan a notional 24 h air tasking day, then inject a cascade of events.

    python -m sarthi.demo [--seed 7] [--time-limit 10]
"""
from __future__ import annotations

import argparse

from . import candidates, greedy, optimizer
from .events import AircraftDown, BaseClosure, NewMission, NewThreat
from .geo import fmt_time
from .kpi import kpis, stress_test
from .models import Mission, Plan, Role, Threat, World
from .retask import RetaskResult, retask
from .validate import validate


def banner(text: str) -> None:
    print("\n" + "=" * 78 + f"\n{text}\n" + "=" * 78)


def show_plan(world: World, plan: Plan, limit: int = 12) -> None:
    print(f"{'MISSION':<9}{'ROLE':<8}{'P':>2}  {'TOT':>5}  {'PACKAGE':<44}{'RISK':>5}")
    rows = sorted(plan.assignments.values(), key=lambda a: a.tot)
    for a in rows[:limit]:
        m = world.missions[a.mission]
        pkg = ", ".join(s.tail for s in a.sorties)
        if a.tankers:
            pkg += f" +AAR {','.join(a.tankers)}"
        risk = max((s.risk for s in a.sorties), default=0)
        print(f"{a.mission:<9}{m.role.value:<8}{m.priority:>2}  {fmt_time(a.tot):>5}  {pkg[:43]:<44}{risk:>5.0%}")
    if len(rows) > limit:
        print(f"... {len(rows) - limit} more")


def show_kpis(label: str, k: dict) -> None:
    print(f"{label:<10} planned {k['missions_planned']}/{k['missions_total']}  "
          f"priority-weighted {k['priority_weighted_fulfilment']:.0%}  expected value {k['expected_value']:.0%}  "
          f"sorties {k['sorties']}  mean risk {k['mean_sortie_risk']:.1%}  solve {k['solve_seconds']}s")


def show_retask(res: RetaskResult, before: dict) -> None:
    for n in res.notes:
        print(f"EVENT  {n}")
    after = kpis(res.world, res.plan)
    d = res.diff
    print(f"\nRetask solved in {res.plan.solve_seconds}s ({res.plan.status}). "
          f"Priority-weighted fulfilment {before['priority_weighted_fulfilment']:.0%} -> "
          f"{after['priority_weighted_fulfilment']:.0%}.")
    print(f"Changes: {d.aircraft_changes} aircraft, {d.crew_changes} crews, {d.tot_shifts} TOT shifts; "
          f"{d.untouched_missions} missions untouched.")
    if res.naive_diff:
        n = res.naive_diff
        print(f"Naive re-plan from scratch would change {n.aircraft_changes} aircraft, {n.crew_changes} crews, "
              f"{n.tot_shifts} TOT shifts.")
    for c in d.changes:
        print(f"  [{c.change:<8}] {c.mission} (P{c.priority})")
        for line in c.details:
            print(f"      {line}")
    errs = validate(res.world, res.plan, candidates.build(res.world),
                    frozen=optimizer.frozen_missions(res.world, res.plan))
    print(f"Constraint check: {'PASS' if not errs else errs[:3]}")


def _survivable_point(world: World) -> tuple[float, float]:
    """Lowest-hazard point in the notional adversary area near the forward line."""
    import numpy as np

    from .scenario import RED_BOX
    from .threats import RiskField
    f = RiskField(world)
    box = (f.LAT >= RED_BOX[0] + 0.5) & (f.LAT <= RED_BOX[1] - 0.5) & (f.LON >= 73.3) & (f.LON <= RED_BOX[3])
    h = np.where(box, f.hazard, np.inf)
    i, j = np.unravel_index(int(np.argmin(h)), h.shape)
    return round(float(f.LAT[i, j]), 2), round(float(f.LON[i, j]), 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--time-limit", type=float, default=10.0)
    args = ap.parse_args()

    from .scenario import generate
    world = generate(args.seed)
    banner(f"NOTIONAL SCENARIO  seed={args.seed}: {len(world.bases)} bases, {len(world.aircraft)} aircraft, "
           f"{len(world.crews)} crews, {len(world.threats)} threats, {len(world.missions)} missions")

    cands = candidates.build(world)
    g = greedy.solve(world, cands)
    plan = optimizer.solve(world, cands, hint=g, time_limit=args.time_limit)
    show_kpis("GREEDY", kpis(world, g))
    show_kpis("SARTHI", kpis(world, plan))
    print()
    show_plan(world, plan)
    st = stress_test(world, plan)
    print(f"\nStress test (2000 runs, no replanning): fulfilment p05 {st['p05']:.0%} / p50 {st['p50']:.0%} "
          f"/ p95 {st['p95']:.0%}; most fragile: {st['most_fragile'][:3]}")
    for mid, why in plan.unassigned.items():
        print(f"NOT PLANNED {mid}: {' '.join(why)}")

    # Event 1: fog forecast closes the busiest fighter base.
    busiest = max(world.bases, key=lambda b: sum(s.base == b for a in plan.assignments.values() for s in a.sorties))
    ev1 = BaseClosure(at=120, base=busiest, start=300, end=570, reason="fog forecast, RVR below minima")
    banner("T+02:00  PREDICTED WEATHER CLOSURE")
    res = retask(world, plan, [ev1], args.time_limit, compare_naive=True)
    show_retask(res, kpis(world, plan))
    world, plan = res.world, res.plan

    # Event 2: pop-up SAM on the ingress route of the highest-priority strike still to launch.
    strikes = [a for a in plan.assignments.values() if world.missions[a.mission].role == Role.STRIKE
               and min(s.launch for s in a.sorties) > 180]
    if strikes:
        tgt = max(strikes, key=lambda a: world.missions[a.mission].priority)
        path = tgt.sorties[0].route
        lat, lon = path[len(path) * 2 // 3] if path else (world.missions[tgt.mission].lat, world.missions[tgt.mission].lon)
        ev2 = NewThreat(at=180, threat=Threat(id="POPUP-1", kind="SAM-MR", lat=lat, lon=lon,
                                              radius_km=45, pk=0.5, mobile_kmh=12))
        banner(f"T+03:00  POP-UP SAM ON {tgt.mission} INGRESS")
        before = kpis(world, plan)
        res = retask(world, plan, [ev2], args.time_limit, compare_naive=True)
        show_retask(res, before)
        world, plan = res.world, res.plan

    # Event 3: predictive maintenance flags three tasked aircraft.
    later = sorted({s.tail for a in plan.assignments.values() for s in a.sorties if s.launch > 300})[:3]
    ev3 = AircraftDown(at=240, tails=later, reason="predicted unserviceable (maintenance analytics)")
    banner("T+04:00  PREDICTIVE MAINTENANCE ALERT")
    before = kpis(world, plan)
    res = retask(world, plan, [ev3], args.time_limit, compare_naive=True)
    show_retask(res, before)
    world, plan = res.world, res.plan

    # Event 4: time-sensitive target, placed where a survivable route exists.
    lat, lon = _survivable_point(world)
    tst = Mission(id="TST-01", role=Role.STRIKE, priority=10, lat=lat, lon=lon, tot_earliest=330,
                  tot_latest=390, package=2, weapon="PGM", weapons_per_aircraft=2, max_risk=0.35,
                  label="Time-sensitive target")
    banner("T+05:00  TIME-SENSITIVE TARGET (P10, TOT 05:30-06:30)")
    before = kpis(world, plan)
    res = retask(world, plan, [NewMission(at=300, mission=tst)], args.time_limit, compare_naive=True)
    show_retask(res, before)
    if "TST-01" not in res.plan.assignments:
        print("TST-01 not planned:", " ".join(res.plan.unassigned.get("TST-01", [])))


if __name__ == "__main__":
    main()
