"""Readiness board: the fused picture per base, and how fresh each data feed is.

For a time `at`: aircraft serviceable and tasked by type, ground spares, the alert reserve, crews fit
to fly now and hour by hour over the next 24 h (fatigue model and night currency), weapons left after
the plan, and closures. Feed timestamps live in World.feeds and are moved by the events that update them.
"""
from __future__ import annotations

import numpy as np

from .fatigue import effectiveness, is_night
from .models import Plan, World

FEED_LABEL = {
    "maintenance": "Maintenance status",
    "crews": "Crew roster",
    "armament": "Armament state",
    "intel": "Threat picture",
    "airfields": "Airfield state",
    "airspace": "Airspace (ACO)",
    "tasking": "Tasking",
}


def readiness(world: World, plan: Plan | None, at: int) -> dict:
    at = max(at, world.now)
    hours = [(at // 60) * 60 + 60 * k for k in range(24)]
    hrs = np.array(hours)
    sorties = [(mid, s) for mid, a in (plan.assignments.items() if plan else []) for s in a.sorties]
    spares = [s for a in (plan.assignments.values() if plan else []) for s in a.spares]
    out = []
    for b in world.bases.values():
        acs = [a for a in world.aircraft.values() if a.base == b.id]
        crews = [c for c in world.crews.values() if c.base == b.id]
        if not acs and not crews:
            continue
        types = {}
        for a in acs:
            t = types.setdefault(a.type, {"type": a.type, "total": 0, "serviceable": 0, "p_sum": 0.0, "tasked": 0})
            t["total"] += 1
            t["serviceable"] += a.serviceable
            t["p_sum"] += a.p_serviceable if a.serviceable else 0.0
        ahead = {s.tail for _, s in sorties if s.base == b.id and s.recover > at}
        for tail in ahead:
            types[world.aircraft[tail].type]["tasked"] += 1
        for t in types.values():
            t["p_mean"] = round(t.pop("p_sum") / t["serviceable"], 3) if t["serviceable"] else 0.0
        avail = [c for c in crews if c.available]
        fit = np.zeros(len(hours), dtype=int)
        night = is_night(hrs)
        for c in avail:
            ok = effectiveness(c, hrs) >= world.fatigue_threshold
            if not c.night_qualified:
                ok &= ~night
            fit += ok
        used = {}
        for mid, s in sorties:
            m = world.missions.get(mid)
            if m and m.weapon and s.base == b.id:
                used[m.weapon] = used.get(m.weapon, 0) + m.weapons_per_aircraft
        weapons = [{"weapon": w, "stock": q, "planned": used.get(w, 0), "left": q - used.get(w, 0)}
                   for w, q in sorted(b.stocks.items())]
        fighters = sum(1 for a in acs if a.serviceable and world.types[a.type].fighter)
        out.append({
            "base": b.id, "name": b.name,
            "types": sorted(types.values(), key=lambda t: t["type"]),
            "spares": sum(1 for s in spares if s.base == b.id),
            "fighters": fighters, "reserve": world.reserve(b.id) if fighters else 0,
            "crews_available": len(avail), "crews_total": len(crews),
            "crews_night": sum(1 for c in avail if c.night_qualified),
            "crews_fit_now": int(fit[0]) if len(fit) else 0,
            "crews_fit_hourly": fit.tolist(),
            "weapons": weapons,
            "closures": [c.model_dump() for c in b.closures if c.end >= at and c.start <= at + 1440],
        })
    feeds = dict(world.feeds)
    if world.threats:
        feeds["intel"] = max(feeds.get("intel", -10**6), max(t.observed_at for t in world.threats.values()))
    return {"at": at, "hours": hours, "threshold": world.fatigue_threshold, "bases": out,
            "feeds": [{"id": k, "label": FEED_LABEL.get(k, k), "as_of": v, "age_min": at - v}
                      for k, v in sorted(feeds.items(), key=lambda kv: kv[1])]}
