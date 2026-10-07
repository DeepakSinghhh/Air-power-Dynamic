"""Feasibility screening with reason codes.

For every (mission, asset) pair this computes timing offsets, the route, risk,
and the *allowed time-on-target domain* (availability, weather/attack closures
at launch and recovery, crew fitness). The optimiser only ever chooses within
these domains, and the reason codes are what make "why not?" answerable.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .fatigue import effectiveness, fit_tot_domain
from .geo import Intervals, haversine_km, intersect, subtract
from .models import Role, World
from .threats import RiskField, Route

BRIEF_MIN = 60
DEBRIEF_MIN = 30
CREW_REST_MIN = 90
TANKER_LEAD_MIN = 90   # tanker on its track this long before the package TOT
TANKER_TRAIL_MIN = 45


@dataclass
class PairCand:
    mission: str
    tail: str
    base: str
    type: str
    lead: int            # TOT - launch
    trail: int           # recover - TOT
    turnaround: int
    route: Route
    needs_aar: bool
    domain: Intervals
    quality: int         # objective bonus for preferring this asset
    payload_t: float = 0.0


@dataclass
class CrewCand:
    mission: str
    crew: str
    base: str
    type: str
    domain: Intervals
    fitness: int


@dataclass
class TankerCand:
    mission: str
    tail: str
    lead: int
    trail: int
    turnaround: int
    domain: Intervals
    capacity: int


@dataclass
class Candidates:
    pairs: dict[str, list[PairCand]] = field(default_factory=dict)
    crews: dict[str, list[CrewCand]] = field(default_factory=dict)
    tankers: dict[str, list[TankerCand]] = field(default_factory=dict)
    rejects: dict[str, Counter] = field(default_factory=dict)  # mission -> reason histogram
    routes: dict[tuple[str, str], Route] = field(default_factory=dict)  # (mission, base) -> route


def closure_free(world: World, base: str, window: Intervals, lead: int, trail: int) -> Intervals:
    """Remove TOTs whose launch or recovery would fall inside a base closure."""
    dom = window
    for c in world.bases[base].closures:
        dom = subtract(dom, c.start + lead, c.end + lead)    # launch inside closure
        dom = subtract(dom, c.start - trail, c.end - trail)  # recovery inside closure
    return dom


def build(world: World, fields: dict[frozenset, RiskField] | None = None) -> Candidates:
    cands = Candidates()
    fields = {} if fields is None else fields
    base_pts = {b.id: (b.lat, b.lon) for b in world.bases.values()}

    for m in world.missions.values():
        rej: Counter = Counter()
        cands.rejects[m.id] = rej
        suppress = _suppression(world, m.id)
        key = frozenset(suppress.items())
        if key not in fields:
            fields[key] = RiskField(world, suppress)
        routes = fields[key].routes_to(m.lat, m.lon, base_pts)
        for bid, r in routes.items():
            cands.routes[(m.id, bid)] = r

        window = [(m.tot_earliest, m.tot_latest)]
        pairs = []
        for a in world.aircraft.values():
            t = world.types[a.type]
            if m.role not in t.roles:
                continue  # not a candidate at all; keep the histogram meaningful
            if not a.serviceable:
                rej["aircraft unserviceable"] += 1
                continue
            if m.weapon and t.weapons.get(m.weapon, 0) < m.weapons_per_aircraft:
                rej[f"cannot carry {m.weapons_per_aircraft}x {m.weapon}"] += 1
                continue
            if m.weapon and world.bases[a.base].stocks.get(m.weapon, 0) < m.weapons_per_aircraft:
                rej[f"no {m.weapon} stock at base"] += 1
                continue
            route = routes[a.base]
            reach = t.combat_radius_km
            needs_aar = route.km > reach
            if route.km > reach * world.aar_extension or (needs_aar and m.role in (Role.AAR, Role.AIRLIFT)):
                rej["out of range (even with AAR)"] += 1
                continue
            if route.risk > m.max_risk:
                rej[f"route risk above {m.max_risk:.0%}"] += 1
                continue
            transit = int(round(route.km / t.speed_kmh * 60))
            lead = t.prep_min + transit
            trail = m.on_station_min + transit
            dom = intersect(window, [(max(world.now, a.available_from) + lead, 10**6)])
            if not dom:
                rej["cannot launch in time"] += 1
                continue
            dom = closure_free(world, a.base, dom, lead, trail)
            if not dom:
                rej["base closed at launch/recovery"] += 1
                continue
            quality = int(round(100 * a.p_serviceable * (1 - route.risk) - route.km / 25))
            pairs.append(PairCand(m.id, a.tail, a.base, a.type, lead, trail, t.turnaround_min, route,
                                  needs_aar, dom, quality, t.payload_t))
        cands.pairs[m.id] = pairs

        # Crews for every (base, type) group that has a candidate aircraft.
        groups = {(p.base, p.type): p for p in pairs}
        crew_list = []
        for c in world.crews.values():
            p = groups.get((c.base, c.qualified))
            if p is None or not c.available:
                continue
            if p.lead + p.trail > c.max_flight_min:
                rej["sortie exceeds crew flight-time limit"] += 1
                continue
            dom = fit_tot_domain(c, p.trail, m.tot_earliest, m.tot_latest, world.fatigue_threshold)
            if not dom:
                rej["crew fatigued / not night-qualified"] += 1
                continue
            fitness = int(effectiveness(c, (m.tot_earliest + m.tot_latest) // 2))
            crew_list.append(CrewCand(m.id, c.id, c.base, c.qualified, dom, fitness))
        cands.crews[m.id] = crew_list

        # Tankers, only if some candidate needs refuelling.
        tk = []
        if any(p.needs_aar for p in pairs):
            for a in world.aircraft.values():
                t = world.types[a.type]
                if Role.AAR not in t.roles or not a.serviceable:
                    continue
                b = world.bases[a.base]
                transit = int(round(0.5 * haversine_km(b.lat, b.lon, m.lat, m.lon) / t.speed_kmh * 60))
                lead = t.prep_min + transit + TANKER_LEAD_MIN
                trail = TANKER_TRAIL_MIN + transit
                dom = intersect(window, [(max(world.now, a.available_from) + lead, 10**6)])
                dom = closure_free(world, a.base, dom, lead, trail)
                if dom:
                    tk.append(TankerCand(m.id, a.tail, lead, trail, t.turnaround_min, dom, t.aar_receivers))
        cands.tankers[m.id] = tk
    return cands


def tanker_sortie(world: World, tc: TankerCand, tot: int):
    """Concrete tanker sortie: launch, recovery and a track halfway between its base and the target."""
    from .models import Sortie
    a = world.aircraft[tc.tail]
    b, m = world.bases[a.base], world.missions[tc.mission]
    mid = ((b.lat + m.lat) / 2, (b.lon + m.lon) / 2)
    return Sortie(tail=tc.tail, base=a.base, launch=tot - tc.lead, recover=tot + tc.trail,
                  route_km=round(haversine_km(b.lat, b.lon, *mid), 1), risk=0.0,
                  route=[(b.lat, b.lon), (round(mid[0], 3), round(mid[1], 3))])


def _suppression(world: World, mission_id: str) -> dict[str, float]:
    """Threat Pk multipliers that apply to a mission's route.

    SEAD crews are equipped to engage the SAM they target; a strike that depends
    on a SEAD mission flies after that SAM has been suppressed.
    """
    m = world.missions[mission_id]
    out = {t: 0.5 for t in m.suppresses}
    if m.depends_on and m.depends_on in world.missions:
        for t in world.missions[m.depends_on].suppresses:
            out[t] = 0.25
    return out
