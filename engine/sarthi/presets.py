"""Context-aware demo events built from the current world and plan.

Each preset targets something that matters *right now*: the busiest base, the
highest-priority strike still to launch, aircraft tasked later in the day.
"""
from __future__ import annotations

import random

import numpy as np
from pydantic import BaseModel

from . import met
from .events import AircraftDown, BaseClosure, Event, NewMission, NewThreat, NewZone, RunwayDamage, StockLoss
from .geo import fmt_time, haversine_km
from .models import Mission, Plan, RestrictedZone, Role, Threat, World
from .scenario import red_point
from .threats import RiskField


class Preset(BaseModel):
    id: str
    label: str
    detail: str
    events: list[Event]


def busiest_bases(world: World, plan: Plan, after: int = 0) -> list[str]:
    load = {b: 0 for b in world.bases}
    for a in plan.assignments.values():
        for s in a.sorties:
            if s.launch > after:
                load[s.base] += 1
    return sorted(load, key=lambda b: -load[b])


def survivable_point(world: World, samples: int = 200, seed: int = 0) -> tuple[float, float]:
    """Lowest-hazard notional adversary location (so a time-sensitive target is actually reachable)."""
    rng = random.Random(seed)
    field = RiskField(world)
    pts = [red_point(rng) for _ in range(samples)]
    haz = [field.hazard[divmod(field.node(lat, lon), field.nlon)] for lat, lon in pts]
    return pts[int(np.argmin(haz))]


def _unique(prefix: str, existing) -> str:
    n = 1
    while f"{prefix}-{n:02d}" in existing:
        n += 1
    return f"{prefix}-{n:02d}"


def fog_preset(world: World, fallback_base: str, at: int, threshold: float = 0.5) -> Preset:
    """Fog closures from the MOS forecast (cached dense-fog night); synthetic closure if none apply."""
    try:
        fc = met.snapshot_forecast(world)
        events = met.closure_events(world, met.fog_windows(fc, threshold, after=at), at)
    except (OSError, ValueError, KeyError):
        fc, events = None, []
    if fc and events:
        names = sorted({world.bases[e.base].name for e in events})
        detail = "; ".join(f"{world.bases[e.base].name} P {e.probability:.0%} {fmt_time(e.start)}-{fmt_time(e.end)}"
                           for e in events[:4])
        return Preset(id="fog", label=f"Met forecast: fog at {len(names)} base{'s' if len(names) > 1 else ''}",
                      detail=f"{detail}{' ...' if len(events) > 4 else ''} · MOS on Open-Meteo, {fc.label}",
                      events=events)
    b = world.bases[fallback_base]
    s, e = at + 180, at + 450
    return Preset(id="fog", label=f"Fog forecast: {b.name} (synthetic)",
                  detail=f"RVR below minima at {b.name} {fmt_time(s)}-{fmt_time(e)}",
                  events=[BaseClosure(at=at, base=b.id, start=s, end=e, reason="fog forecast, RVR below minima")])


def _on_route(world: World, plan: Plan, at: int, min_km: float) -> tuple[str, tuple[float, float]] | None:
    """Midpoint of the highest-priority helicopter route still to fly, if it is clear of both ends."""
    later = [a for a in plan.assignments.values() if min(s.launch for s in a.sorties) > at + 60
             and world.missions.get(a.mission) and world.missions[a.mission].runway_m == 0 and a.sorties[0].route]
    for a in sorted(later, key=lambda a: -world.missions[a.mission].priority):
        path = a.sorties[0].route
        c = path[len(path) // 2] if len(path) > 2 else ((path[0][0] + path[-1][0]) / 2, (path[0][1] + path[-1][1]) / 2)
        m = world.missions[a.mission]
        base = world.bases[a.sorties[0].base]
        if min(haversine_km(*c, m.lat, m.lon), haversine_km(*c, base.lat, base.lon)) >= min_km:
            return a.mission, c
    return None


def _helos_down(world: World, plan: Plan, at: int, rng: random.Random) -> Preset | None:
    helos = sorted({s.tail for a in plan.assignments.values() for s in a.sorties
                    if s.launch > at + 60 and world.aircraft[s.tail].type.startswith("HELO")})
    if not helos:
        return None
    tails = sorted(rng.sample(helos, min(2, len(helos))))
    return Preset(id="mx", label=f"Helicopters unserviceable: {len(tails)}",
                  detail="Chip warnings after the morning sorties: " + ", ".join(tails),
                  events=[AircraftDown(at=at, tails=tails, reason="unserviceable (chip warning)")])


def hadr_presets(world: World, plan: Plan, at: int) -> list[Preset]:
    """Flood-relief events: a new breach, rain at a busy airfield, a building thunderstorm, helicopters U/S."""
    from .scenario_hadr import DISTRICTS, _near
    rng = random.Random(at)
    out: list[Preset] = []
    name, lat, lon = rng.choice(DISTRICTS)
    p = _near(rng, lat, lon)
    mid = _unique("RSC", world.missions)
    out.append(Preset(id="breach", label=f"Embankment breach near {name}: rescue {mid} (P10)",
                      detail=f"~60 people marooned at {p[0]:.2f}N {p[1]:.2f}E, pick up {fmt_time(at + 60)}-{fmt_time(at + 180)}",
                      events=[NewMission(at=at, mission=Mission(
                          id=mid, role=Role.AIRLIFT, priority=10, lat=p[0], lon=p[1], tot_earliest=at + 60,
                          tot_latest=at + 180, on_station_min=30, package=0, cargo_t=6.0, runway_m=0, max_risk=0.05,
                          label=f"Rescue: ~60 people marooned near {name}"))]))

    busy = [b for b in busiest_bases(world, plan, after=at + 60) if world.bases[b].id not in ("HDN", "AGR")]
    if busy:
        b = world.bases[busy[0]]
        # Put the 2.5 h of rain over the base's busiest stretch of launches and recoveries.
        moves = [t for a in plan.assignments.values() for s in a.sorties if s.base == b.id
                 for t in (s.launch, s.recover) if t > at + 30]
        start = max(moves, key=lambda s0: sum(s0 <= t <= s0 + 150 for t in moves)) if moves else at + 30
        out.append(Preset(id="rain", label=f"Heavy rain closes {b.name} for 2.5 h",
                          detail=f"Below landing minima {fmt_time(start)}-{fmt_time(start + 150)}",
                          events=[BaseClosure(at=at, base=b.id, start=start, end=start + 150,
                                              reason="heavy rain, below minima")]))

    hit = _on_route(world, plan, at, min_km=40)
    if hit:
        mid, c = hit
        zid = _unique("CB", world.zones)
        out.append(Preset(id="cb", label=f"Thunderstorm cell on {mid}'s route",
                          detail=f"CB building at {c[0]:.2f}N {c[1]:.2f}E, avoid by 20 km",
                          events=[NewZone(at=at, zone=RestrictedZone(id=zid, lat=round(c[0], 3), lon=round(c[1], 3),
                                                                     radius_km=20, reason="Thunderstorm cell (CB): avoid"))]))

    mx = _helos_down(world, plan, at, rng)
    if mx:
        out.append(mx)

    name, lat, lon = rng.choice(DISTRICTS)
    p = _near(rng, lat, lon, km=20.0)
    did = _unique("DRP", world.missions)
    s0 = max(at + 90, 360)
    out.append(Preset(id="convoy", label=f"Road convoy to {name} cancelled: airlift 16 t ({did}, P8)",
                      detail=f"Bridge washed away; food and water by air {fmt_time(s0)}-{fmt_time(s0 + 180)}",
                      events=[NewMission(at=at, mission=Mission(
                          id=did, role=Role.AIRLIFT, priority=8, lat=p[0], lon=p[1], tot_earliest=s0,
                          tot_latest=s0 + 180, on_station_min=20, package=0, cargo_t=16.0, runway_m=0, max_risk=0.05,
                          label=f"Relief drop: food and water, {name} (convoy cancelled)"))]))
    return out


def quake_presets(world: World, plan: Plan, at: int) -> list[Preset]:
    """Earthquake events: an aftershock cracks the damaged runway further, a landslide, cloud on a ridge,
    helicopters unserviceable, a field hospital for a valley landing ground."""
    from .scenario_quake import AIRFIELDS, DAY, SITES
    from .scenario_hadr import _near
    rng = random.Random(at)
    out: list[Preset] = []

    hit = [m for m in world.missions.values() if m.airfield == "DED" and m.runway_m is not None
           and (m.id not in plan.assignments or min(s.launch for s in plan.assignments[m.id].sorties) > at)]
    b = world.bases.get("DED")
    if b is not None and (b.runway_m is None or b.runway_m > 900):
        out.append(Preset(id="aftershock", label=f"Aftershock: {b.name} runway down to 900 m",
                          detail="New cracks; too short for the transports"
                                 + (f": {', '.join(m.id for m in hit)} must re-plan" if hit else ""),
                          events=[RunwayDamage(at=at, airfield=b.id, usable_m=900,
                                               reason="aftershock: new cracks")]))

    name, lat, lon, elev = rng.choice([s for s in SITES if s[3] >= 1800])
    p = _near(rng, lat, lon, km=3.0)
    mid = _unique("RSC", world.missions)
    s0 = max(at + 60, DAY[0])
    out.append(Preset(id="landslide", label=f"Landslide hits a bus near {name}: rescue {mid} (P10)",
                      detail=f"~25 people, {elev:,} m, pick up {fmt_time(s0)}-{fmt_time(s0 + 120)}",
                      events=[NewMission(at=at, mission=Mission(
                          id=mid, role=Role.AIRLIFT, priority=10, lat=p[0], lon=p[1], tot_earliest=s0,
                          tot_latest=s0 + 120, on_station_min=20, package=0, cargo_t=2.5, runway_m=0,
                          elevation_m=elev, max_risk=0.05,
                          label=f"Rescue: ~25 people from a bus hit by a landslide near {name} ({elev:,} m)"))]))

    hit_route = _on_route(world, plan, at, min_km=15)
    if hit_route:
        rid, c = hit_route
        zid = _unique("CLD", world.zones)
        out.append(Preset(id="cloud", label=f"Low cloud on {rid}'s route",
                          detail=f"Ridge in cloud at {c[0]:.2f}N {c[1]:.2f}E, no VFR crossing within 10 km",
                          events=[NewZone(at=at, zone=RestrictedZone(
                              id=zid, lat=round(c[0], 3), lon=round(c[1], 3), radius_km=10,
                              reason="Low cloud on the ridge: no VFR crossing"))]))

    mx = _helos_down(world, plan, at, rng)
    if mx:
        out.append(mx)

    aid, name, lat, lon, runway, elev, *_ = next(a for a in AIRFIELDS if a[0] == "GCR")
    hid = _unique("LIFT", world.missions)
    s0 = max(at + 90, 420)
    out.append(Preset(id="hospital", label=f"Field hospital to {name}: 16 t ({hid}, P9)",
                      detail=f"Army field hospital for the Alaknanda valley, land {fmt_time(s0)}-{fmt_time(s0 + 240)}",
                      events=[NewMission(at=at, mission=Mission(
                          id=hid, role=Role.AIRLIFT, priority=9, lat=lat, lon=lon, tot_earliest=s0,
                          tot_latest=s0 + 240, on_station_min=60, package=0, cargo_t=16.0, runway_m=runway,
                          elevation_m=elev, airfield=aid, max_risk=0.05,
                          label=f"Field hospital to {name} ({runway:,} m usable)"))]))
    return out


def presets(world: World, plan: Plan, at: int) -> list[Preset]:
    at = max(at, world.now)
    if world.scenario == "hadr":
        return quake_presets(world, plan, at) if world.disaster == "earthquake" else hadr_presets(world, plan, at)
    out: list[Preset] = []
    busy = busiest_bases(world, plan, after=at + 60)

    out.append(fog_preset(world, busy[0], at))

    strikes = [a for a in plan.assignments.values()
               if world.missions.get(a.mission) and world.missions[a.mission].role == Role.STRIKE
               and min(x.launch for x in a.sorties) > at + 60 and a.sorties[0].route]
    if strikes:
        tgt = max(strikes, key=lambda a: world.missions[a.mission].priority)
        path = tgt.sorties[0].route
        lat, lon = path[len(path) * 2 // 3]
        tid = _unique("POPUP", world.threats)
        out.append(Preset(id="popup", label=f"Pop-up SAM on {tgt.mission} ingress",
                          detail=f"ELINT detects a medium-range SAM at {lat:.2f}N {lon:.2f}E",
                          events=[NewThreat(at=at, threat=Threat(id=tid, kind="SAM-MR", lat=lat, lon=lon,
                                                                 radius_km=45, pk=0.5, mobile_kmh=6))]))

    later = sorted({x.tail for a in plan.assignments.values() for x in a.sorties if x.launch > at + 60})
    if later:
        rng = random.Random(at)
        tails = sorted(rng.sample(later, min(3, len(later))))
        out.append(Preset(id="mx", label=f"Maintenance alert: {len(tails)} aircraft",
                          detail="Predictive maintenance flags " + ", ".join(tails),
                          events=[AircraftDown(at=at, tails=tails,
                                               reason="predicted unserviceable (maintenance analytics)")]))

    lat, lon = survivable_point(world)
    mid = _unique("TST", world.missions)
    out.append(Preset(id="tst", label=f"Time-sensitive target {mid} (P10)",
                      detail=f"High-value mobile target at {lat:.2f}N {lon:.2f}E, TOT {fmt_time(at + 30)}-{fmt_time(at + 90)}",
                      events=[NewMission(at=at, mission=Mission(
                          id=mid, role=Role.STRIKE, priority=10, lat=lat, lon=lon, tot_earliest=at + 30,
                          tot_latest=at + 90, package=2, weapon="PGM", weapons_per_aircraft=2, max_risk=0.35,
                          label="Time-sensitive target"))]))

    if len(busy) > 1:
        b2 = world.bases[busy[1]]
        out.append(Preset(id="runway", label=f"Runway cratered: {b2.name}",
                          detail=f"Rapid runway repair estimate 3 h ({fmt_time(at)}-{fmt_time(at + 180)})",
                          events=[BaseClosure(at=at, base=b2.id, start=at, end=at + 180,
                                              reason="runway cratered, repair in progress")]))

    pgm = max(world.bases.values(), key=lambda x: x.stocks.get("PGM", 0))
    if pgm.stocks.get("PGM", 0) > 0:
        q = pgm.stocks["PGM"] // 2
        out.append(Preset(id="stock", label=f"Ammunition point hit: {pgm.name}",
                          detail=f"{q} PGM lost at {pgm.name}",
                          events=[StockLoss(at=at, base=pgm.id, weapon="PGM", qty=q)]))
    return out
