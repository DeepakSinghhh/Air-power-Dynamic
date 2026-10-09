"""Scripted demo: plan a notional 24 h air tasking day, then inject a cascade of events.

    python -m sarthi.demo [--seed 7] [--time-limit 10]
"""
from __future__ import annotations

import argparse

from . import candidates, greedy, optimizer
from .geo import fmt_time
from .kpi import kpis, stress_test
from .models import Plan, World
from .presets import presets
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

    # A cascade of events, each built from the plan as it stands at that moment.
    for at, pid, title in [(120, "fog", "PREDICTED WEATHER CLOSURE"),
                           (180, "popup", "POP-UP SAM"),
                           (240, "mx", "PREDICTIVE MAINTENANCE ALERT"),
                           (300, "tst", "TIME-SENSITIVE TARGET")]:
        preset = next((p for p in presets(world, plan, at) if p.id == pid), None)
        if preset is None:
            continue
        banner(f"T+{fmt_time(at)}  {title}: {preset.detail}")
        before = kpis(world, plan)
        res = retask(world, plan, preset.events, args.time_limit, compare_naive=True)
        show_retask(res, before)
        for mid in set(res.world.missions) - set(world.missions):
            if mid not in res.plan.assignments:
                print(f"{mid} not planned:", " ".join(res.plan.unassigned.get(mid, [])))
        world, plan = res.world, res.plan


if __name__ == "__main__":
    main()
