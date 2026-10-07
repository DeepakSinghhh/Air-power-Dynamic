"""Seeded synthetic scenario generator.

Base locations are public airfield coordinates. Fleet mix, stocks, crews,
threats, targets and missions are NOTIONAL and randomly generated; they do not
represent real deployments, capabilities or target data.
"""
from __future__ import annotations

import random

from .geo import haversine_km, in_india, ring_offsets
from .models import (Aircraft, AircraftType, Base, Crew, Mission, RestrictedZone, Role, Threat,
                     World)

R = Role

TYPES: dict[str, AircraftType] = {t.name: t for t in [
    AircraftType(name="RAFALE", roles=[R.DCA, R.STRIKE], speed_kmh=850, combat_radius_km=1000,
                 turnaround_min=90, weapons={"BVR": 6, "PGM": 4, "LGB": 4}, fighter=True),
    AircraftType(name="SU30", roles=[R.DCA, R.STRIKE, R.SEAD], speed_kmh=850, combat_radius_km=1200,
                 turnaround_min=120, weapons={"BVR": 6, "PGM": 4, "ARM": 4, "LGB": 6}, fighter=True),
    AircraftType(name="MIG29", roles=[R.DCA], speed_kmh=850, combat_radius_km=700,
                 turnaround_min=90, weapons={"BVR": 4}, fighter=True),
    AircraftType(name="TEJAS", roles=[R.DCA, R.CAS, R.STRIKE], speed_kmh=800, combat_radius_km=500,
                 turnaround_min=60, weapons={"BVR": 4, "LGB": 2, "PGM": 2}, fighter=True),
    AircraftType(name="JAGUAR", roles=[R.STRIKE, R.CAS], speed_kmh=750, combat_radius_km=600,
                 turnaround_min=90, weapons={"LGB": 4, "PGM": 2}),
    AircraftType(name="UAV-MALE", roles=[R.ISR], speed_kmh=200, combat_radius_km=1000,
                 prep_min=45, turnaround_min=120),
    AircraftType(name="TANKER", roles=[R.AAR], speed_kmh=750, combat_radius_km=1500,
                 prep_min=45, turnaround_min=120, aar_receivers=4),
    AircraftType(name="AEWC", roles=[R.AEW], speed_kmh=650, combat_radius_km=900,
                 prep_min=45, turnaround_min=120),
    AircraftType(name="HEAVYLIFT", roles=[R.AIRLIFT], speed_kmh=750, combat_radius_km=4000,
                 prep_min=60, turnaround_min=180, payload_t=60, min_runway_m=2000),
    AircraftType(name="MEDLIFT", roles=[R.AIRLIFT], speed_kmh=600, combat_radius_km=2000,
                 prep_min=45, turnaround_min=120, payload_t=18, min_runway_m=1100),
    # Helicopters (HADR): land or winch anywhere, short legs.
    AircraftType(name="HELO-M", roles=[R.AIRLIFT], speed_kmh=220, combat_radius_km=300,
                 prep_min=20, turnaround_min=45, payload_t=4),
    AircraftType(name="HELO-L", roles=[R.AIRLIFT], speed_kmh=240, combat_radius_km=220,
                 prep_min=15, turnaround_min=40, payload_t=1.2),
]}

# (id, name, lat, lon, {type: count}, {weapon: stock}, alert reserve)
BASES = [
    ("AMB", "Ambala", 30.368, 76.817, {"RAFALE": 10, "JAGUAR": 6}, {"BVR": 40, "PGM": 24, "LGB": 20}, 2),
    ("ADM", "Adampur", 31.433, 75.759, {"MIG29": 8, "SU30": 4}, {"BVR": 40, "ARM": 8}, 2),
    ("HLW", "Halwara", 30.749, 75.630, {"SU30": 10}, {"BVR": 30, "PGM": 16, "ARM": 12, "LGB": 12}, 2),
    ("PTK", "Pathankot", 32.234, 75.634, {"TEJAS": 8}, {"BVR": 24, "LGB": 12}, 2),
    ("SRN", "Srinagar", 33.987, 74.774, {"SU30": 4, "MIG29": 4}, {"BVR": 24, "PGM": 8}, 2),
    ("SRS", "Sirsa", 29.561, 75.006, {"SU30": 6, "JAGUAR": 4}, {"BVR": 20, "PGM": 12, "LGB": 12, "ARM": 4}, 1),
    ("JDH", "Jodhpur", 26.251, 73.049, {"SU30": 8, "TEJAS": 4}, {"BVR": 30, "PGM": 16, "LGB": 12, "ARM": 8}, 2),
    ("BTH", "Bathinda", 30.270, 74.756, {"UAV-MALE": 4, "AEWC": 2}, {}, 0),
    ("AGR", "Agra", 27.156, 77.961, {"TANKER": 4, "MEDLIFT": 3}, {}, 0),
    ("HDN", "Hindan", 28.708, 77.358, {"HEAVYLIFT": 3, "MEDLIFT": 4}, {}, 0),
    ("LEH", "Leh", 34.136, 77.546, {}, {}, 0),
]

# Sampling boxes (lat0, lat1, lon0, lon1); points are then filtered against India's boundary.
RED_BOX = (27.6, 32.6, 69.5, 74.6)        # notional adversary area: >= RED_STANDOFF_KM outside India
BLUE_FWD_BOX = (29.0, 33.0, 74.6, 76.2)   # own-side forward area for CAP stations (inside India)
CAS_BOX = (29.5, 32.8, 73.4, 75.2)        # troops in contact: inside India, near the boundary
RED_STANDOFF_KM = 30.0

THREAT_KINDS = {  # kind: (radius_km, pk, relocation km/h)
    "SAM-LR": (110.0, 0.55, 0.0),
    "SAM-MR": (45.0, 0.45, 6.0),
    "SAM-SR": (15.0, 0.35, 10.0),
}


def _uniform(rng: random.Random, box) -> tuple[float, float]:
    return round(rng.uniform(box[0], box[1]), 3), round(rng.uniform(box[2], box[3]), 3)


def red_point(rng: random.Random) -> tuple[float, float]:
    """Notional adversary location, at least RED_STANDOFF_KM outside India."""
    while True:
        lat, lon = _uniform(rng, RED_BOX)
        if not in_india(lat, lon) and not in_india(*ring_offsets(lat, lon, RED_STANDOFF_KM)).any():
            return lat, lon


def blue_point(rng: random.Random, box=BLUE_FWD_BOX, margin_km: float = 25.0) -> tuple[float, float]:
    """Own-side location inside India, at least margin_km from the boundary."""
    while True:
        lat, lon = _uniform(rng, box)
        if in_india(lat, lon) and in_india(*ring_offsets(lat, lon, margin_km)).all():
            return lat, lon


def border_point(rng: random.Random, max_km: float = 35.0) -> tuple[float, float]:
    """Inside India and within max_km of the boundary (close air support)."""
    while True:
        lat, lon = _uniform(rng, CAS_BOX)
        if in_india(lat, lon) and in_india(*ring_offsets(lat, lon, 8.0)).all() \
                and not in_india(*ring_offsets(lat, lon, max_km)).all():
            return lat, lon


def generate(seed: int = 7, n_strike: int = 10, n_dca: int = 5, n_cas: int = 4, n_isr: int = 3,
             n_airlift: int = 3) -> World:
    rng = random.Random(seed)
    bases, aircraft, crews = {}, {}, {}
    for bid, name, lat, lon, fleet, stocks, reserve in BASES:
        bases[bid] = Base(id=bid, name=name, lat=lat, lon=lon, stocks=dict(stocks), fighter_reserve=reserve)
        for tname, count in fleet.items():
            for k in range(count):
                tail = f"{bid}-{tname}-{k + 1:02d}"
                aircraft[tail] = Aircraft(tail=tail, type=tname, base=bid,
                                          p_serviceable=round(rng.uniform(0.82, 0.99), 2))
            for k in range(int(count * 1.5) + 1):
                cid = f"{bid}-{tname}-C{k + 1:02d}"
                crews[cid] = Crew(id=cid, base=bid, qualified=tname,
                                  night_qualified=rng.random() > 0.15,
                                  max_flight_min=900 if tname == "UAV-MALE" else 480,
                                  wake_time=rng.choice([-180, -60, 240, 300, 360, 420, 480, 600, 720]),
                                  sleep_hours=round(rng.uniform(5.0, 8.5), 1))

    threats = {}
    for k in range(rng.randint(2, 3)):
        threats[f"SAM-LR-{k + 1}"] = _threat(rng, f"SAM-LR-{k + 1}", "SAM-LR")
    for k in range(rng.randint(4, 6)):
        threats[f"SAM-MR-{k + 1}"] = _threat(rng, f"SAM-MR-{k + 1}", "SAM-MR")
    for k in range(rng.randint(3, 5)):
        threats[f"SAM-SR-{k + 1}"] = _threat(rng, f"SAM-SR-{k + 1}", "SAM-SR")

    missions: dict[str, Mission] = {}

    def add(m: Mission) -> None:
        missions[m.id] = m

    for k in range(n_dca):
        lat, lon = blue_point(rng)
        start = rng.randrange(0, 1200, 30)
        add(Mission(id=f"DCA-{k + 1:02d}", role=R.DCA, priority=rng.randint(6, 9), lat=lat, lon=lon,
                    tot_earliest=start, tot_latest=start + 60, on_station_min=120, package=2,
                    weapon="BVR", weapons_per_aircraft=4, max_risk=0.25, label="CAP station"))
    for k in range(2):
        lat, lon = blue_point(rng, margin_km=60.0)
        start = 0 if k == 0 else 720
        add(Mission(id=f"AEW-{k + 1:02d}", role=R.AEW, priority=8, lat=lat, lon=lon,
                    tot_earliest=start, tot_latest=start + 90, on_station_min=300, package=1,
                    max_risk=0.10, label="AEW&C orbit"))

    sead_n = 0
    for k in range(n_strike):
        lat, lon = red_point(rng)
        start = rng.randrange(120, 1200, 15)
        mid = f"STK-{k + 1:02d}"
        strike = Mission(id=mid, role=R.STRIKE, priority=rng.randint(4, 10), lat=lat, lon=lon,
                         tot_earliest=start, tot_latest=start + rng.choice([60, 90, 120]),
                         package=rng.choice([2, 2, 4]), weapon=rng.choice(["PGM", "PGM", "LGB"]),
                         weapons_per_aircraft=2, max_risk=0.30, label="Notional target")
        # Pair a SEAD mission with strikes deep inside a medium/long-range SAM envelope.
        near = [t for t in threats.values() if t.kind != "SAM-SR"
                and _dist(t, lat, lon) < t.radius_km * 0.8]
        if near:
            th = min(near, key=lambda t: _dist(t, lat, lon))
            sead_n += 1
            sid = f"SEAD-{sead_n:02d}"
            add(Mission(id=sid, role=R.SEAD, priority=strike.priority, lat=th.lat, lon=th.lon,
                        tot_earliest=max(0, start - 40), tot_latest=strike.tot_latest,
                        package=2, weapon="ARM", weapons_per_aircraft=2, max_risk=0.60,
                        suppresses=[th.id], label=f"Suppress {th.id}"))
            strike.depends_on = sid
        add(strike)

    for k in range(n_cas):
        lat, lon = border_point(rng)
        start = rng.randrange(300, 1200, 15)
        add(Mission(id=f"CAS-{k + 1:02d}", role=R.CAS, priority=rng.randint(5, 8), lat=lat, lon=lon,
                    tot_earliest=start, tot_latest=start + 45, on_station_min=30, package=2,
                    weapon="LGB", weapons_per_aircraft=2, max_risk=0.30, label="Troops in contact"))
    for k in range(n_isr):
        lat, lon = red_point(rng)
        start = rng.randrange(0, 900, 30)
        add(Mission(id=f"ISR-{k + 1:02d}", role=R.ISR, priority=rng.randint(3, 6), lat=lat, lon=lon,
                    tot_earliest=start, tot_latest=start + 120, on_station_min=240, package=1,
                    max_risk=0.60, label="Persistent ISR"))
    dests = [("LEH", 34.136, 77.546), ("SRN", 33.987, 74.774), ("PTK", 32.234, 75.634)]
    for k in range(n_airlift):
        dname, lat, lon = dests[k % len(dests)]
        start = rng.randrange(180, 1000, 30)
        add(Mission(id=f"LIFT-{k + 1:02d}", role=R.AIRLIFT, priority=rng.randint(3, 7), lat=lat, lon=lon,
                    tot_earliest=start, tot_latest=start + 240, on_station_min=60, package=0,
                    cargo_t=float(rng.choice([20, 40, 60, 90])), max_risk=0.05,
                    label=f"Sustainment lift to {dname}"))

    zones = {"CIV-1": RestrictedZone(id="CIV-1", lat=28.56, lon=77.10, radius_km=25,
                                     reason="Civil terminal area (FUA)")}
    return World(bases=bases, types=dict(TYPES), aircraft=aircraft, crews=crews,
                 threats=threats, zones=zones, missions=missions)


def _threat(rng: random.Random, tid: str, kind: str) -> Threat:
    radius, pk, mobile = THREAT_KINDS[kind]
    lat, lon = red_point(rng)
    return Threat(id=tid, kind=kind, lat=lat, lon=lon, radius_km=radius, pk=pk, mobile_kmh=mobile)


def _dist(t: Threat, lat: float, lon: float) -> float:
    return haversine_km(t.lat, t.lon, lat, lon)
