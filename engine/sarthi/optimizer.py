"""CP-SAT allocation of aircraft, crews, tankers and weapons to missions.

One model handles both the initial plan and dynamic retasking. In retask mode
the previous plan becomes a baseline: missions already launched are frozen, and
every change to a not-yet-launched mission costs a churn penalty that grows the
closer it is to launch (planned < crew briefed < aircraft armed). The solver
therefore finds the *smallest* set of changes that recovers the most value.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict

from ortools.sat.python import cp_model

from .candidates import (BRIEF_MIN, CREW_REST_MIN, DEBRIEF_MIN, MAX_AIRLIFT_PACKAGE, Candidates, build,
                         tanker_sortie)
from .explain import explain_unassigned
from .models import Assignment, Plan, Role, Sortie, World

VALUE_PER_PRIORITY = 1000
KEEP_AIRCRAFT = 300
ADD_AIRCRAFT = 100
KEEP_CREW = 100
TOT_SHIFT_PER_MIN = 2
SORTIE_COST = 120  # > max quality bonus, so no sortie is flown without need


def _effective_cpus() -> float:
    """CPUs this process may really use: a container's CPU quota (cgroup v2 or v1), else the core count."""
    def read(path: str) -> str:
        with open(path) as f:
            return f.read().strip()
    for quota_of in (lambda: read("/sys/fs/cgroup/cpu.max").split()[:2],
                     lambda: [read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"),
                              read("/sys/fs/cgroup/cpu/cpu.cfs_period_us")]):
        try:
            quota, period = quota_of()
            if quota not in ("max", "-1"):
                return max(0.05, int(quota) / int(period))
        except (OSError, ValueError):
            continue
    return float(os.cpu_count() or 1)


# On a small cloud instance (e.g. 0.1 CPU) one search worker given more wall time beats eight starving ones.
CPUS = _effective_cpus()
DEFAULT_WORKERS = int(os.environ.get("SARTHI_SOLVER_WORKERS") or (8 if CPUS >= 2 else max(1, int(CPUS))))
TIME_SCALE = float(os.environ.get("SARTHI_TIME_SCALE") or (1.0 if CPUS >= 1 else min(3.0, 0.25 / CPUS)))


def churn_weight(minutes_to_launch: int) -> int:
    """How disruptive it is to change a sortie this long before launch."""
    if minutes_to_launch > 360:
        return 1      # still a plan on paper
    if minutes_to_launch > 120:
        return 3      # crews briefed
    return 8          # aircraft armed, crews walking


def frozen_missions(world: World, baseline: Plan | None) -> set[str]:
    if baseline is None:
        return set()
    return {mid for mid, a in baseline.assignments.items()
            if mid in world.missions and min(s.launch for s in a.sorties) <= world.now}


def solve(world: World, cands: Candidates | None = None, baseline: Plan | None = None,
          hint: Plan | None = None, time_limit: float = 10.0, workers: int | None = None,
          churn: bool = True) -> Plan:
    """Plan, or (with `baseline`) retask. `churn=False` re-plans from scratch around frozen missions."""
    t0 = time.perf_counter()
    cands = cands or build(world)
    frozen = frozen_missions(world, baseline)
    model = cp_model.CpModel()
    obj = []

    u, tot = {}, {}
    x, y, k = {}, {}, {}
    ac_iv = defaultdict(list)      # tail -> intervals
    crew_iv = defaultdict(list)    # crew -> intervals
    crew_load = defaultdict(list)  # crew -> (y, flight minutes)
    stock_use = defaultdict(list)  # (base, weapon) -> terms
    reserve_iv = defaultdict(list)  # base -> fighter intervals

    # Frozen missions: fixed resource consumption, copied verbatim to the output.
    stocks = {(b.id, w): q for b in world.bases.values() for w, q in b.stocks.items()}
    crew_done = defaultdict(lambda: [0, 0])  # crew -> [sorties, flight minutes] already committed
    for mid in frozen:
        a = baseline.assignments[mid]
        m = world.missions[mid]
        for s in a.sorties:
            t = world.types[world.aircraft[s.tail].type] if s.tail in world.aircraft else None
            turn = t.turnaround_min if t else 90
            ac_iv[s.tail].append(model.new_fixed_size_interval_var(s.launch, s.recover - s.launch + turn, ""))
            if s.crew:
                crew_iv[s.crew].append(model.new_fixed_size_interval_var(
                    s.launch - BRIEF_MIN, s.recover - s.launch + BRIEF_MIN + DEBRIEF_MIN + CREW_REST_MIN, ""))
                crew_done[s.crew][0] += 1
                crew_done[s.crew][1] += s.recover - s.launch
            if m.weapon:
                stocks[(s.base, m.weapon)] = stocks.get((s.base, m.weapon), 0) - m.weapons_per_aircraft
            if t and t.fighter:
                reserve_iv[s.base].append((model.new_fixed_size_interval_var(
                    s.launch, s.recover - s.launch + turn, ""), 1))
        for ts in a.tanker_sorties:
            turn = world.types[world.aircraft[ts.tail].type].turnaround_min
            ac_iv[ts.tail].append(model.new_fixed_size_interval_var(ts.launch, ts.recover - ts.launch + turn, ""))

    live = [m for m in world.missions.values() if m.id not in frozen]
    for m in live:
        u[m.id] = model.new_bool_var(f"u[{m.id}]")
        tot[m.id] = model.new_int_var(m.tot_earliest, m.tot_latest, f"tot[{m.id}]")
        obj.append(VALUE_PER_PRIORITY * m.priority * u[m.id])
        pairs = cands.pairs.get(m.id, [])
        for p in pairs:
            v = model.new_bool_var(f"x[{m.id},{p.tail}]")
            x[m.id, p.tail] = v
            model.add_linear_expression_in_domain(tot[m.id], cp_model.Domain.from_intervals(
                [list(iv) for iv in p.domain])).only_enforce_if(v)
            iv = model.new_optional_fixed_size_interval_var(
                tot[m.id] - p.lead, p.lead + p.trail + p.turnaround, v, f"iv[{m.id},{p.tail}]")
            ac_iv[p.tail].append(iv)
            if world.types[p.type].fighter:
                reserve_iv[p.base].append((iv, 1))
            if m.weapon:
                stock_use[(p.base, m.weapon)].append(m.weapons_per_aircraft * v)
            intent = world.intent
            cost = SORTIE_COST + intent.sortie_cost
            cost += int(round(intent.loss_weight * VALUE_PER_PRIORITY * p.route.risk))  # expected loss
            if m.weapon:
                cost += intent.munitions_weight * m.weapons_per_aircraft
            obj.append((p.quality - cost) * v)

        xs = [x[m.id, p.tail] for p in pairs]
        if m.role == Role.AIRLIFT:
            model.add(sum(int(p.payload_t * 10) * x[m.id, p.tail] for p in pairs)
                      >= int(m.cargo_t * 10) * u[m.id])
            model.add(sum(xs) <= MAX_AIRLIFT_PACKAGE * u[m.id])
        else:
            model.add(sum(xs) == m.package * u[m.id])

        # Crews: each (base, type) group needs as many crews as aircraft.
        by_group = defaultdict(list)
        for p in pairs:
            by_group[(p.base, p.type)].append(p)
        crews_by_group = defaultdict(list)
        for c in cands.crews.get(m.id, []):
            crews_by_group[(c.base, c.type)].append(c)
        for g, gp in by_group.items():
            lead, trail = gp[0].lead, gp[0].trail
            ys = []
            for c in crews_by_group.get(g, []):
                cv = model.new_bool_var(f"y[{m.id},{c.crew}]")
                y[m.id, c.crew] = cv
                ys.append(cv)
                model.add_linear_expression_in_domain(tot[m.id], cp_model.Domain.from_intervals(
                    [list(iv) for iv in c.domain])).only_enforce_if(cv)
                crew_iv[c.crew].append(model.new_optional_fixed_size_interval_var(
                    tot[m.id] - lead - BRIEF_MIN, lead + trail + BRIEF_MIN + DEBRIEF_MIN + CREW_REST_MIN,
                    cv, ""))
                crew_load[c.crew].append((cv, lead + trail))
                obj.append(max(0, c.fitness - int(world.fatigue_threshold)) // 4 * cv)
            model.add(sum(x[m.id, p.tail] for p in gp) == sum(ys))

        # Tankers cover every receiver that needs fuel.
        aar = [x[m.id, p.tail] for p in pairs if p.needs_aar]
        if aar:
            ks = []
            for tc in cands.tankers.get(m.id, []):
                kv = model.new_bool_var(f"k[{m.id},{tc.tail}]")
                k[m.id, tc.tail] = kv
                ks.append((kv, tc.capacity))
                model.add(kv <= u[m.id])
                model.add_linear_expression_in_domain(tot[m.id], cp_model.Domain.from_intervals(
                    [list(iv) for iv in tc.domain])).only_enforce_if(kv)
                ac_iv[tc.tail].append(model.new_optional_fixed_size_interval_var(
                    tot[m.id] - tc.lead, tc.lead + tc.trail + tc.turnaround, kv, ""))
                obj.append(-20 * kv)  # tanker sorties are scarce; use only when needed
            model.add(sum(aar) <= sum(cap * kv for kv, cap in ks))

    # Mission dependencies (e.g. SEAD must precede the strike it enables).
    for m in live:
        if not m.depends_on or m.depends_on not in world.missions:
            continue
        d = m.depends_on
        if d in frozen:
            if d not in baseline.assignments:
                model.add(u[m.id] == 0)
                continue
            dtot = baseline.assignments[d].tot
            model.add(tot[m.id] - dtot >= m.dep_lag_min).only_enforce_if(u[m.id])
            model.add(tot[m.id] - dtot <= m.dep_lag_max).only_enforce_if(u[m.id])
        else:
            model.add_implication(u[m.id], u[d])
            model.add(tot[m.id] - tot[d] >= m.dep_lag_min).only_enforce_if(u[m.id])
            model.add(tot[m.id] - tot[d] <= m.dep_lag_max).only_enforce_if(u[m.id])

    for ivs in ac_iv.values():
        if len(ivs) > 1:
            model.add_no_overlap(ivs)
    for c, ivs in crew_iv.items():
        if len(ivs) > 1:
            model.add_no_overlap(ivs)
    for c, loads in crew_load.items():
        crew = world.crews[c]
        done_n, done_min = crew_done[c]
        model.add(sum(v for v, _ in loads) <= crew.max_sorties - done_n)
        model.add(sum(v * mins for v, mins in loads) <= crew.max_flight_min - done_min)
    for key, terms in stock_use.items():
        model.add(sum(terms) <= max(0, stocks.get(key, 0)))
    for bid, ivs in reserve_iv.items():
        b = world.bases[bid]
        n = sum(1 for a in world.aircraft.values()
                if a.base == bid and a.serviceable and world.types[a.type].fighter)
        model.add_cumulative([iv for iv, _ in ivs], [d for _, d in ivs], max(0, n - world.reserve(bid)))

    # Churn: reward keeping what was already planned, scaled by how close it is to launch.
    if baseline is not None and churn:
        for m in live:
            a0 = baseline.assignments.get(m.id)
            if a0 is None:
                continue
            w = churn_weight(min(s.launch for s in a0.sorties) - world.now)
            kept = {s.tail for s in a0.sorties}
            spare = {s.tail for s in a0.spares}  # already briefed and loaded: the cheapest substitute
            for p in cands.pairs.get(m.id, []):
                v = x[m.id, p.tail]
                bonus = KEEP_AIRCRAFT * w if p.tail in kept else -ADD_AIRCRAFT * w // (4 if p.tail in spare else 1)
                obj.append(bonus * v)
            for s in a0.sorties:
                if s.crew and (m.id, s.crew) in y:
                    obj.append(KEEP_CREW * w * y[m.id, s.crew])
            dev = model.new_int_var(0, 1440, f"dev[{m.id}]")
            model.add(dev >= tot[m.id] - a0.tot)
            model.add(dev >= a0.tot - tot[m.id])
            obj.append(-TOT_SHIFT_PER_MIN * w * dev)

    _add_hints(model, hint or (baseline if churn else None), u, tot, x, y, k)
    model.maximize(sum(obj))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = workers or DEFAULT_WORKERS
    solver.parameters.random_seed = 1
    status = solver.solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE) and baseline is None and hint is not None:
        # Not enough CPU time to find a solution: keep the valid greedy start rather than an empty plan.
        plan = hint.model_copy(deep=True)
        plan.solver, plan.status = "greedy (solver out of time)", solver.status_name(status)
        plan.solve_seconds = round(time.perf_counter() - t0, 3)
        return plan

    plan = Plan(solver="cp-sat", status=solver.status_name(status))
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        plan.objective = solver.objective_value
        for mid in frozen:
            plan.assignments[mid] = baseline.assignments[mid].model_copy(deep=True)
        for m in live:
            if not solver.value(u[m.id]):
                continue
            t = solver.value(tot[m.id])
            chosen = [p for p in cands.pairs[m.id] if solver.value(x[m.id, p.tail])]
            crews = defaultdict(list)
            for c in cands.crews.get(m.id, []):
                if solver.value(y[m.id, c.crew]):
                    crews[(c.base, c.type)].append(c.crew)
            sorties = []
            for p in chosen:
                pool = crews[(p.base, p.type)]
                sorties.append(Sortie(tail=p.tail, base=p.base, crew=pool.pop(0) if pool else None,
                                      launch=t - p.lead, recover=t + p.trail, route_km=round(p.route.km, 1),
                                      risk=round(p.route.risk, 4), needs_aar=p.needs_aar, route=p.route.path))
            tks = [tc for tc in cands.tankers.get(m.id, []) if solver.value(k[m.id, tc.tail])]
            plan.assignments[m.id] = Assignment(mission=m.id, tot=t, sorties=sorties,
                                                tankers=[tc.tail for tc in tks],
                                                tanker_sorties=[tanker_sortie(world, tc, t) for tc in tks])
    if baseline is None and hint is not None and _value(world, hint) > _value(world, plan):
        # Short of CPU time the solver can end below its own greedy start: keep the better plan.
        plan = hint.model_copy(deep=True)
        plan.solver, plan.status = "greedy (solver out of time)", solver.status_name(status)
        plan.solve_seconds = round(time.perf_counter() - t0, 3)
        return plan
    plan.unassigned = explain_unassigned(world, cands, plan)
    if world.spare_policy and plan.assignments:
        from .robust import add_spares
        plan = add_spares(world, plan, cands, prefer=baseline)
    plan.solve_seconds = round(time.perf_counter() - t0, 3)
    return plan


def _value(world: World, plan: Plan) -> int:
    """Priority planned: the numerator of priority-weighted fulfilment."""
    return sum(world.missions[m].priority for m in plan.assignments if m in world.missions)


def _add_hints(model, hint: Plan | None, u, tot, x, y, k) -> None:
    if hint is None:
        return
    for mid, uv in u.items():
        a = hint.assignments.get(mid)
        model.add_hint(uv, 1 if a else 0)
        if a:
            lo, hi = tot[mid].proto.domain[0], tot[mid].proto.domain[-1]
            model.add_hint(tot[mid], min(max(a.tot, lo), hi))
    for (mid, tail), v in x.items():
        a = hint.assignments.get(mid)
        model.add_hint(v, 1 if a and any(s.tail == tail for s in a.sorties) else 0)
    for (mid, crew), v in y.items():
        a = hint.assignments.get(mid)
        model.add_hint(v, 1 if a and any(s.crew == crew for s in a.sorties) else 0)
    for (mid, tail), v in k.items():
        a = hint.assignments.get(mid)
        model.add_hint(v, 1 if a and tail in a.tankers else 0)
