"""Greedy baseline that mimics a manual planner.

Highest priority first, earliest feasible time-on-target, nearest base first.
It respects exactly the same constraints as the optimiser (checked by
`validate`), so benchmark differences come from decision quality alone.
"""
from __future__ import annotations

import time
from collections import defaultdict

from .candidates import BRIEF_MIN, CREW_REST_MIN, DEBRIEF_MIN, Candidates, build, tanker_sortie
from .explain import explain_unassigned
from .geo import contains
from .models import Assignment, Plan, Role, Sortie, World
from .optimizer import MAX_AIRLIFT_PACKAGE

STEP = 5


def _free(busy: list[tuple[int, int]], s: int, e: int) -> bool:
    return all(e <= bs or s >= be for bs, be in busy)


def solve(world: World, cands: Candidates | None = None) -> Plan:
    t0 = time.perf_counter()
    cands = cands or build(world)
    ac_busy = defaultdict(list)
    crew_busy = defaultdict(list)
    crew_sorties = defaultdict(int)
    crew_mins = defaultdict(int)
    stock = {(b.id, w): q for b in world.bases.values() for w, q in b.stocks.items()}
    fighters = defaultdict(int)
    for a in world.aircraft.values():
        if a.serviceable and world.types[a.type].fighter:
            fighters[a.base] += 1
    fighter_iv = defaultdict(list)  # base -> busy fighter intervals

    def reserve_ok(base: str, s: int, e: int, extra: int) -> bool:
        cap = fighters[base] - world.reserve(base)
        overlapping = [(bs, be) for bs, be in fighter_iv[base] if bs < e and s < be]
        points = sorted({s} | {bs for bs, _ in overlapping if bs > s})
        return all(extra + sum(1 for bs, be in overlapping if bs <= p < be) <= cap for p in points)

    plan = Plan(solver="greedy", status="FEASIBLE")
    order = sorted(world.missions.values(), key=lambda m: (-m.priority, m.role != Role.SEAD, m.tot_earliest))
    for m in order:
        pairs = sorted(cands.pairs.get(m.id, []), key=lambda p: p.route.km)
        if not pairs:
            continue
        lo, hi = m.tot_earliest, m.tot_latest
        if m.depends_on:
            dep = plan.assignments.get(m.depends_on)
            if dep is None:
                continue
            lo, hi = max(lo, dep.tot + m.dep_lag_min), min(hi, dep.tot + m.dep_lag_max)
        for t in range(lo, hi + 1, STEP):
            pick = _try_pack(world, cands, m, pairs, t, ac_busy, crew_busy, crew_sorties, crew_mins,
                             stock, fighter_iv, reserve_ok)
            if pick is None:
                continue
            sorties, tankers = pick
            for s, p in sorties:
                ac_busy[p.tail].append((s.launch, s.recover + p.turnaround))
                crew_busy[s.crew].append((s.launch - BRIEF_MIN, s.recover + DEBRIEF_MIN + CREW_REST_MIN))
                crew_sorties[s.crew] += 1
                crew_mins[s.crew] += s.recover - s.launch
                if m.weapon:
                    stock[(p.base, m.weapon)] -= m.weapons_per_aircraft
                if world.types[p.type].fighter:
                    fighter_iv[p.base].append((s.launch, s.recover + p.turnaround))
            for tc in tankers:
                ac_busy[tc.tail].append((t - tc.lead, t + tc.trail + tc.turnaround))
            plan.assignments[m.id] = Assignment(mission=m.id, tot=t, sorties=[s for s, _ in sorties],
                                                tankers=[tc.tail for tc in tankers],
                                                tanker_sorties=[tanker_sortie(world, tc, t) for tc in tankers])
            break
    plan.solve_seconds = round(time.perf_counter() - t0, 3)
    plan.unassigned = explain_unassigned(world, cands, plan)
    return plan


def _try_pack(world, cands, m, pairs, t, ac_busy, crew_busy, crew_sorties, crew_mins, stock,
              fighter_iv, reserve_ok):
    crews = defaultdict(list)
    for c in cands.crews.get(m.id, []):
        if contains(c.domain, t):
            crews[(c.base, c.type)].append(c)
    chosen, used_crews, payload = [], set(), 0.0
    stock_left = dict(stock)
    extra_fighters = defaultdict(int)
    for p in pairs:
        if not contains(p.domain, t):
            continue
        s, e = t - p.lead, t + p.trail
        if not _free(ac_busy[p.tail], s, e + p.turnaround):
            continue
        if m.weapon and stock_left.get((p.base, m.weapon), 0) < m.weapons_per_aircraft:
            continue
        if world.types[p.type].fighter and not reserve_ok(p.base, s, e + p.turnaround, extra_fighters[p.base] + 1):
            continue
        crew = next((c for c in crews[(p.base, p.type)] if c.crew not in used_crews
                     and _free(crew_busy[c.crew], s - BRIEF_MIN, e + DEBRIEF_MIN + CREW_REST_MIN)
                     and crew_sorties[c.crew] < world.crews[c.crew].max_sorties
                     and crew_mins[c.crew] + (e - s) <= world.crews[c.crew].max_flight_min), None)
        if crew is None:
            continue
        used_crews.add(crew.crew)
        if m.weapon:
            stock_left[(p.base, m.weapon)] -= m.weapons_per_aircraft
        if world.types[p.type].fighter:
            extra_fighters[p.base] += 1
        chosen.append((Sortie(tail=p.tail, base=p.base, crew=crew.crew, launch=s, recover=e,
                              route_km=round(p.route.km, 1), risk=round(p.route.risk, 4),
                              needs_aar=p.needs_aar, route=p.route.path), p))
        payload += p.payload_t
        if m.role == Role.AIRLIFT:
            if payload >= m.cargo_t:
                break
            if len(chosen) == MAX_AIRLIFT_PACKAGE:
                return None
        elif len(chosen) == m.package:
            break
    if m.role == Role.AIRLIFT:
        if payload < m.cargo_t:
            return None
    elif len(chosen) < m.package:
        return None
    receivers = sum(1 for _, p in chosen if p.needs_aar)
    tankers, cap = [], 0
    for tc in cands.tankers.get(m.id, []):
        if cap >= receivers:
            break
        if contains(tc.domain, t) and _free(ac_busy[tc.tail], t - tc.lead, t + tc.trail + tc.turnaround):
            tankers.append(tc)
            cap += tc.capacity
    if cap < receivers:
        return None
    return chosen, tankers
