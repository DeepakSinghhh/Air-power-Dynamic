"""Plan robustness: mission success probabilities, Monte Carlo execution, single points of failure
and ground spares.

A planned mission succeeds only if
  * every package slot launches with a mission-capable aircraft. A ground spare of the same type at the
    same base replaces a primary that is unserviceable at start-up, and the primary's crew walks to it;
  * every aircraft reaches the target (the ingress half of its two-way route risk);
  * enough tankers are serviceable to refuel the receivers that need it;
  * the mission it depends on (SEAD before a strike) succeeded.
The same model gives the analytic expected value (a KPI) and the Monte Carlo spread (stress test), so
the two always agree.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import product

import numpy as np

from .candidates import Candidates, build
from .models import Assignment, Plan, Sortie, World

SPARE_HOLD_MIN = 15      # a ground spare stays ready this long after its package is airborne
MIN_SPARE_GAIN = 0.05    # expected priority points a spare must add to be worth holding
CAUSES = ("serviceability", "tanker", "dependency", "attrition")


# ---------- the success model ----------

def groups(world: World, a: Assignment) -> dict[tuple[str, str], tuple[list[Sortie], list[Sortie]]]:
    """Package elements: (base, type) -> (primary sorties, spares). Same base and type means same route
    and timing, so any spare in the element can replace any primary in it."""
    out: dict[tuple[str, str], tuple[list[Sortie], list[Sortie]]] = {}
    for s in a.sorties:
        out.setdefault((s.base, world.aircraft[s.tail].type), ([], []))[0].append(s)
    for s in a.spares:
        key = (s.base, world.aircraft[s.tail].type)
        if key in out:
            out[key][1].append(s)
    return out


def spare_standby(world: World, s: Sortie) -> tuple[int, int]:
    """When a spare is started up alongside its package and held: off the alert reserve meanwhile."""
    return s.launch, s.launch + world.types[world.aircraft[s.tail].type].prep_min + SPARE_HOLD_MIN


def p_serv(world: World, s: Sortie) -> float:
    """Probability the aircraft is mission-capable at start-up (already launched = 1)."""
    return 1.0 if s.launch <= world.now else world.aircraft[s.tail].p_serviceable


def p_reach(world: World, s: Sortie, tot: int) -> float:
    """P(the aircraft reaches the target). Sortie risk is two-way over the same path, so under the
    exponential hazard model the ingress half survives with sqrt(1 - risk). Egress losses cost the
    aircraft (see expected losses) but not the effect."""
    return 1.0 if tot <= world.now else (1.0 - s.risk) ** 0.5


def _count_dist(ps: list[float]) -> np.ndarray:
    """dist[k] = P(exactly k of independent events with probabilities ps happen)."""
    d = np.array([1.0])
    for p in ps:
        d = np.convolve(d, [1.0 - p, p])
    return d


def p_fill(world: World, primaries: list[Sortie], spares: list[Sortie]) -> float:
    """P(every slot in the element launches): unserviceable primaries <= serviceable spares."""
    down = _count_dist([1.0 - p_serv(world, s) for s in primaries])
    ready = _count_dist([p_serv(world, s) for s in spares])
    at_least = np.cumsum(ready[::-1])[::-1]  # at_least[k] = P(ready >= k)
    return float(sum(down[k] * at_least[k] for k in range(min(len(down), len(at_least)))))


def p_tanker(world: World, a: Assignment) -> float:
    need = sum(s.needs_aar for s in a.sorties)
    if not need:
        return 1.0
    tk = [(world.types[world.aircraft[t.tail].type].aar_receivers, p_serv(world, t)) for t in a.tanker_sorties]
    total = 0.0
    for up in product((0, 1), repeat=len(tk)):
        if sum(c for (c, _), u in zip(tk, up) if u) >= need:
            p = 1.0
            for (_, q), u in zip(tk, up):
                p *= q if u else 1.0 - q
            total += p
    return total


def own_p(world: World, a: Assignment) -> float:
    p = p_tanker(world, a)
    for primaries, spares in groups(world, a).values():
        p *= p_fill(world, primaries, spares)
    for s in a.sorties:
        p *= p_reach(world, s, a.tot)
    return p


def success_p(world: World, plan: Plan) -> dict[str, float]:
    own = {mid: own_p(world, a) for mid, a in plan.assignments.items() if mid in world.missions}
    out: dict[str, float] = {}

    def resolve(mid: str) -> float:
        if mid not in out:
            dep = world.missions[mid].depends_on
            out[mid] = own[mid] * (resolve(dep) if dep in own else 1.0)
        return out[mid]

    for mid in own:
        resolve(mid)
    return out


def expected_value(world: World, plan: Plan) -> float:
    total = sum(m.priority for m in world.missions.values()) or 1
    return sum(world.missions[m].priority * p for m, p in success_p(world, plan).items()) / total


# ---------- Monte Carlo execution ----------

def simulate(world: World, plan: Plan, runs: int = 2000, seed: int = 0) -> dict:
    """Execute the plan `runs` times without replanning; report the spread and what fails, and why."""
    rng = np.random.default_rng(seed)
    total = sum(m.priority for m in world.missions.values()) or 1
    mids = [m for m in plan.assignments if m in world.missions]
    # Dependencies first (SEAD before the strike that needs it).
    mids.sort(key=lambda m: world.missions[m].depends_on in plan.assignments)
    ok: dict[str, np.ndarray] = {}
    causes: dict[str, dict[str, int]] = {}
    score = np.zeros(runs)
    for mid in mids:
        a, m = plan.assignments[mid], world.missions[mid]
        serv = np.ones(runs, dtype=bool)
        for primaries, spares in groups(world, a).values():
            down = (rng.random((runs, len(primaries))) >= [p_serv(world, s) for s in primaries]).sum(1)
            ready = (rng.random((runs, len(spares))) < [p_serv(world, s) for s in spares]).sum(1) if spares else 0
            serv &= down <= ready
        need = sum(s.needs_aar for s in a.sorties)
        tank = np.ones(runs, dtype=bool)
        if need:
            cap = np.zeros(runs)
            for t in a.tanker_sorties:
                cap += (rng.random(runs) < p_serv(world, t)) * world.types[world.aircraft[t.tail].type].aar_receivers
            tank = cap >= need
        alive = (rng.random((runs, len(a.sorties))) < [p_reach(world, s, a.tot) for s in a.sorties]).all(1)
        dep = ok.get(m.depends_on, np.ones(runs, dtype=bool))
        ok[mid] = serv & tank & dep & alive
        # Attribute each failure to the first thing that went wrong, in the order it would be known.
        c = {"serviceability": ~serv, "tanker": serv & ~tank, "dependency": serv & tank & ~dep,
             "attrition": serv & tank & dep & ~alive}
        causes[mid] = {k: int(v.sum()) for k, v in c.items()}
        score += m.priority * ok[mid]
    score /= total
    edges = np.linspace(0, 1, 51)
    hist, _ = np.histogram(score, bins=edges)
    fragile = sorted(mids, key=lambda m: -world.missions[m].priority * (1 - ok[m].mean()))
    return {
        "runs": runs,
        "mean": round(float(score.mean()), 4),
        "p05": round(float(np.percentile(score, 5)), 3),
        "p50": round(float(np.percentile(score, 50)), 3),
        "p95": round(float(np.percentile(score, 95)), 3),
        "hist": hist.tolist(),
        "bin_width": 0.02,
        "fragile": [{"mission": m, "priority": world.missions[m].priority,
                     "p_fail": round(1 - float(ok[m].mean()), 3),
                     "causes": {k: round(v / runs, 3) for k, v in causes[m].items()}}
                    for m in fragile[:6] if ok[m].mean() < 1],
        "single_points": single_points(world, plan),
        "spares": sum(len(a.spares) for a in plan.assignments.values()),
        "missions_with_spares": sum(1 for a in plan.assignments.values() if a.spares),
    }


def single_points(world: World, plan: Plan, top: int = 5) -> list[dict]:
    """Assets whose loss (unserviceable from now on) would fail the most mission value, cascades included."""
    total = sum(m.priority for m in world.missions.values()) or 1
    live = {mid: a for mid, a in plan.assignments.items()
            if mid in world.missions and min(s.launch for s in a.sorties) > world.now}
    assets = {s.tail for a in live.values() for s in a.sorties} | {t.tail for a in live.values() for t in a.tanker_sorties}
    out = []
    for x in assets:
        failed = set()
        for mid, a in live.items():
            for primaries, spares in groups(world, a).values():
                if any(s.tail == x for s in primaries) and not any(s.tail != x for s in spares):
                    failed.add(mid)
            need = sum(s.needs_aar for s in a.sorties)
            if need and any(t.tail == x for t in a.tanker_sorties):
                cap = sum(world.types[world.aircraft[t.tail].type].aar_receivers
                          for t in a.tanker_sorties if t.tail != x)
                if cap < need:
                    failed.add(mid)
        changed = True
        while changed:  # SEAD lost -> its strike is lost too
            changed = False
            for mid in live:
                if mid not in failed and world.missions[mid].depends_on in failed:
                    failed.add(mid)
                    changed = True
        if failed:
            ac = world.aircraft[x]
            out.append({"asset": x, "type": ac.type, "base": ac.base,
                        "tanker": any(t.tail == x for a in live.values() for t in a.tanker_sorties),
                        "missions": [f"{m} (P{world.missions[m].priority})"
                                     for m in sorted(failed, key=lambda m: -world.missions[m].priority)],
                        "value": round(sum(world.missions[m].priority for m in failed) / total, 4)})
    out.sort(key=lambda d: (-d["value"], d["asset"]))
    return out[:top]


# ---------- ground spares ----------

def add_spares(world: World, plan: Plan, cands: Candidates | None = None, prefer: Plan | None = None) -> Plan:
    """Hold idle aircraft as ground spares where they add the most expected value.

    Purely additive: no mission, flying aircraft, crew or TOT changes. A spare must be able to fly its
    package's sortie (same base and type, a feasible pair for the mission at the planned TOT), is booked for
    the whole sortie so the plan stays feasible if it launches, is loaded (weapons come out of stock) and
    is not counted towards the alert reserve while it stands by. One spare per package element.
    """
    cands = cands or build(world)
    out = plan.model_copy(deep=True)
    for a in out.assignments.values():
        a.spares = []
    turn = {t: world.types[ac.type].turnaround_min for t, ac in world.aircraft.items()}
    busy: dict[str, list[tuple[int, int]]] = defaultdict(list)
    fighter_iv: dict[str, list[tuple[int, int]]] = defaultdict(list)
    stock = {(b.id, w): q for b in world.bases.values() for w, q in b.stocks.items()}
    for mid, a in out.assignments.items():
        m = world.missions.get(mid)
        for s in a.sorties + a.tanker_sorties:
            busy[s.tail].append((s.launch, s.recover + turn[s.tail]))
        for s in a.sorties:
            if world.types[world.aircraft[s.tail].type].fighter:
                fighter_iv[s.base].append((s.launch, s.recover + turn[s.tail]))
            if m and m.weapon:
                stock[(s.base, m.weapon)] = stock.get((s.base, m.weapon), 0) - m.weapons_per_aircraft
    fighters = defaultdict(int)
    for ac in world.aircraft.values():
        if ac.serviceable and world.types[ac.type].fighter:
            fighters[ac.base] += 1

    def reserve_ok(base: str, s: int, e: int) -> bool:
        cap = fighters[base] - world.reserve(base)
        over = [(bs, be) for bs, be in fighter_iv[base] if bs < e and s < be]
        return all(1 + sum(1 for bs, be in over if bs <= p < be) <= cap for p in {s} | {bs for bs, _ in over if bs > s})

    succ = success_p(world, out)
    dependents = defaultdict(list)
    for mid in out.assignments:
        d = world.missions[mid].depends_on if mid in world.missions else None
        if d in out.assignments:
            dependents[d].append(mid)
    own = {mid: own_p(world, a) for mid, a in out.assignments.items() if mid in world.missions}

    def weight(mid: str) -> float:
        """d(expected priority points) / d(own success probability of mid)."""
        m = world.missions[mid]
        dep_p = succ.get(m.depends_on, 1.0) if m.depends_on in out.assignments else 1.0
        return dep_p * (m.priority + sum(world.missions[d].priority * own[d] for d in dependents[mid]))

    preferred = {(mid, s.tail) for mid, a in (prefer.assignments.items() if prefer else []) for s in a.spares}
    options = []  # (mission, element, tail)
    for mid, a in out.assignments.items():
        if mid not in world.missions or min(s.launch for s in a.sorties) <= world.now:
            continue
        flying = {s.tail for s in a.sorties}
        for (bid, tname), (primaries, _) in groups(world, a).items():
            for p in cands.pairs.get(mid, []):
                if p.base == bid and p.type == tname and p.tail not in flying and world.aircraft[p.tail].serviceable \
                        and any(lo <= a.tot <= hi for lo, hi in p.domain):
                    options.append((mid, (bid, tname), p.tail))

    def gain(mid: str, key, tail: str) -> float:
        a = out.assignments[mid]
        primaries, spares = groups(world, a)[key]
        ref = primaries[0]
        trial = a.model_copy(update={"spares": a.spares + [ref.model_copy(update={"tail": tail, "crew": None})]})
        return (own_p(world, trial) - own[mid]) * weight(mid)

    filled: set[tuple[str, tuple[str, str]]] = set()
    while True:
        best, best_score = None, MIN_SPARE_GAIN
        for mid, key, tail in options:
            if (mid, key) in filled:
                continue
            a = out.assignments[mid]
            ref = groups(world, a)[key][0][0]
            if any(s < ref.recover + turn[tail] and ref.launch < e for s, e in busy[tail]):
                continue
            m = world.missions[mid]
            if m.weapon and stock.get((ref.base, m.weapon), 0) < m.weapons_per_aircraft:
                continue
            if world.types[world.aircraft[tail].type].fighter and \
                    not reserve_ok(ref.base, *spare_standby(world, ref.model_copy(update={"tail": tail}))):
                continue
            g = gain(mid, key, tail) + (0.01 if (mid, tail) in preferred else 0.0)
            if g > best_score:
                best, best_score = (mid, key, tail), g
        if best is None:
            break
        mid, key, tail = best
        a = out.assignments[mid]
        ref = groups(world, a)[key][0][0]
        a.spares.append(ref.model_copy(update={"tail": tail, "crew": None}))
        filled.add((mid, key))
        busy[tail].append((ref.launch, ref.recover + turn[tail]))
        m = world.missions[mid]
        if m.weapon:
            stock[(ref.base, m.weapon)] -= m.weapons_per_aircraft
        if world.types[world.aircraft[tail].type].fighter:
            fighter_iv[ref.base].append(spare_standby(world, a.spares[-1]))
        own[mid] = own_p(world, a)
        succ = success_p(world, out)
    return out
