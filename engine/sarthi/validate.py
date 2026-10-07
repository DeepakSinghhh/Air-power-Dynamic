"""Independent checker: every plan (optimiser or greedy) must pass this."""
from __future__ import annotations

from collections import defaultdict

from .candidates import BRIEF_MIN, CREW_REST_MIN, DEBRIEF_MIN, Candidates
from .fatigue import effectiveness, is_night
from .models import Plan, Role, World


def validate(world: World, plan: Plan, cands: Candidates, frozen: set[str] = frozenset()) -> list[str]:
    errs: list[str] = []
    ac_iv, crew_iv = defaultdict(list), defaultdict(list)
    crew_n, crew_min = defaultdict(int), defaultdict(int)
    use = defaultdict(int)
    fighter_iv = defaultdict(list)

    for mid, a in plan.assignments.items():
        m = world.missions[mid]
        live = mid not in frozen
        if live and not (m.tot_earliest <= a.tot <= m.tot_latest):
            errs.append(f"{mid}: TOT outside window")
        if m.role == Role.AIRLIFT:
            lift = sum(world.types[world.aircraft[s.tail].type].payload_t for s in a.sorties)
            if lift < m.cargo_t:
                errs.append(f"{mid}: lift {lift}t < cargo {m.cargo_t}t")
        elif len(a.sorties) != m.package:
            errs.append(f"{mid}: {len(a.sorties)} aircraft, package needs {m.package}")
        if m.depends_on:
            d = plan.assignments.get(m.depends_on)
            if d is None:
                errs.append(f"{mid}: dependency {m.depends_on} not planned")
            elif live and not (m.dep_lag_min <= a.tot - d.tot <= m.dep_lag_max):
                errs.append(f"{mid}: lag to {m.depends_on} is {a.tot - d.tot} min")
        receivers = 0
        for s in a.sorties:
            ac = world.aircraft[s.tail]
            t = world.types[ac.type]
            if live and m.role not in t.roles:
                errs.append(f"{mid}: {s.tail} lacks role {m.role.value}")
            if live and not ac.serviceable:
                errs.append(f"{mid}: {s.tail} unserviceable")
            if live:
                for c in world.bases[s.base].closures:
                    if c.start <= s.launch <= c.end or c.start <= s.recover <= c.end:
                        errs.append(f"{mid}: {s.tail} launches/recovers during closure at {s.base}")
            ac_iv[s.tail].append((s.launch, s.recover + t.turnaround_min, mid))
            if t.fighter:
                fighter_iv[s.base].append((s.launch, s.recover + t.turnaround_min))
            if m.weapon:
                use[(s.base, m.weapon)] += m.weapons_per_aircraft
            receivers += s.needs_aar
            if s.crew is None:
                errs.append(f"{mid}: {s.tail} has no crew")
                continue
            crew = world.crews[s.crew]
            if crew.qualified != ac.type or crew.base != s.base:
                errs.append(f"{mid}: crew {s.crew} not qualified on {s.tail}")
            if live:
                for tt in (a.tot, s.recover):
                    if effectiveness(crew, tt) < world.fatigue_threshold:
                        errs.append(f"{mid}: crew {s.crew} effectiveness {effectiveness(crew, tt):.0f}% at {tt}")
                    if not crew.night_qualified and bool(is_night(tt)):
                        errs.append(f"{mid}: crew {s.crew} not night-qualified")
            crew_iv[s.crew].append((s.launch - BRIEF_MIN, s.recover + DEBRIEF_MIN + CREW_REST_MIN, mid))
            crew_n[s.crew] += 1
            crew_min[s.crew] += s.recover - s.launch
        cap = 0
        for ts in a.tanker_sorties:
            t = world.types[world.aircraft[ts.tail].type]
            cap += t.aar_receivers
            ac_iv[ts.tail].append((ts.launch, ts.recover + t.turnaround_min, mid))
        if live and receivers > cap:
            errs.append(f"{mid}: {receivers} receivers need AAR, tanker capacity {cap}")

    for key, ivs in list(ac_iv.items()) + list(crew_iv.items()):
        ivs.sort()
        for (s1, e1, m1), (s2, e2, m2) in zip(ivs, ivs[1:]):
            if s2 < e1:
                errs.append(f"{key}: double-booked on {m1} and {m2}")
    for c, n in crew_n.items():
        if n > world.crews[c].max_sorties or crew_min[c] > world.crews[c].max_flight_min:
            errs.append(f"crew {c}: over sortie/flight-time limit")
    for (b, w), q in use.items():
        if q > world.bases[b].stocks.get(w, 0):
            errs.append(f"{b}: {w} use {q} exceeds stock {world.bases[b].stocks.get(w, 0)}")
    for b, ivs in fighter_iv.items():
        cap = sum(1 for a in world.aircraft.values()
                  if a.base == b and a.serviceable and world.types[a.type].fighter) - world.bases[b].fighter_reserve
        for p in {s for s, _ in ivs}:
            if sum(1 for s, e in ivs if s <= p < e) > cap:
                errs.append(f"{b}: alert reserve breached at {p}")
                break
    return errs
