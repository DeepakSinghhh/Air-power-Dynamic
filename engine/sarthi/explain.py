"""Plain-language answers to "why wasn't this mission planned?"."""
from __future__ import annotations

from collections import defaultdict

from .candidates import Candidates
from .models import Plan, Role, World


def explain_unassigned(world: World, cands: Candidates, plan: Plan) -> dict[str, list[str]]:
    used_by = defaultdict(list)  # tail -> missions using it
    for mid, a in plan.assignments.items():
        for s in a.sorties:
            used_by[s.tail].append(mid)
        for t in a.tankers:
            used_by[t].append(mid)
    remaining = _remaining_stock(world, plan)

    out: dict[str, list[str]] = {}
    for m in sorted(world.missions.values(), key=lambda m: -m.priority):
        if m.id in plan.assignments:
            continue
        why: list[str] = []
        if m.depends_on and m.depends_on not in plan.assignments:
            why.append(f"Depends on {m.depends_on} ({world.missions[m.depends_on].role.value}), "
                       f"which could not be scheduled.")
        pairs = cands.pairs.get(m.id, [])
        rej = cands.rejects.get(m.id, {})
        top = ", ".join(f"{n}x {r}" for r, n in sorted(rej.items(), key=lambda kv: -kv[1])[:3])
        need = m.package if m.role != Role.AIRLIFT else None
        if not pairs:
            why.append(f"No feasible aircraft. Screened out: {top or 'no aircraft with this role'}.")
            risky = [r for (mid, _), r in cands.routes.items() if mid == m.id]
            if any("risk" in r for r in rej) and risky:
                best = min(r.risk for r in risky)
                why.append(f"Least-risk route is {best:.0%} vs acceptable {m.max_risk:.0%}; "
                           f"a SEAD package or higher risk acceptance would open options.")
        else:
            tails = {p.tail for p in pairs}
            if need and len(tails) < need:
                why.append(f"Only {len(tails)} feasible aircraft; package needs {need}. Screened out: {top}.")
            if not cands.crews.get(m.id):
                why.append("No crew fit to fly in the window (fatigue / night currency).")
            busy = {mid for t in tails for mid in used_by.get(t, [])}
            if busy:
                lst = ", ".join(f"{b} (P{world.missions[b].priority})" for b in
                                sorted(busy, key=lambda b: -world.missions[b].priority)[:4])
                why.append(f"{sum(1 for t in tails if t in used_by)} of {len(tails)} feasible aircraft "
                           f"are committed to: {lst}.")
            if m.weapon:
                bases = sorted({p.base for p in pairs})
                dry = [b for b in bases if remaining.get((b, m.weapon), 0) < m.weapons_per_aircraft]
                if dry:
                    why.append(f"{m.weapon} stock exhausted at {', '.join(dry)}.")
            if all(p.needs_aar for p in pairs):
                if not cands.tankers.get(m.id):
                    why.append("Every feasible aircraft needs air-to-air refuelling and no tanker can make the track.")
                else:
                    why.append("Every feasible aircraft needs air-to-air refuelling; tanker sorties are the bottleneck.")
            if len(why) == (1 if m.depends_on and m.depends_on not in plan.assignments else 0):
                why.append("Feasible, but scarce aircraft/crew time produced more value on higher-priority missions.")
        out[m.id] = why
    return out


def _remaining_stock(world: World, plan: Plan) -> dict[tuple[str, str], int]:
    rem = {(b.id, w): q for b in world.bases.values() for w, q in b.stocks.items()}
    for mid, a in plan.assignments.items():
        m = world.missions.get(mid)
        if m and m.weapon:
            for s in a.sorties:
                rem[(s.base, m.weapon)] = rem.get((s.base, m.weapon), 0) - m.weapons_per_aircraft
    return rem
