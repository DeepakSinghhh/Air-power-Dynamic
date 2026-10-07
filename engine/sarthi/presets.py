"""Context-aware demo events built from the current world and plan.

Each preset targets something that matters *right now*: the busiest base, the
highest-priority strike still to launch, aircraft tasked later in the day.
"""
from __future__ import annotations

import random

import numpy as np
from pydantic import BaseModel

from . import met
from .events import AircraftDown, BaseClosure, Event, NewMission, NewThreat, StockLoss
from .geo import fmt_time
from .models import Mission, Plan, Role, Threat, World
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


def presets(world: World, plan: Plan, at: int) -> list[Preset]:
    at = max(at, world.now)
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
