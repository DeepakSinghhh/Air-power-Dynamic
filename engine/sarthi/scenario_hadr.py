"""Seeded notional HADR scenario: monsoon flood relief in Assam and Bihar.

Same engine, different mission set. Fixed-wing transports lift NDRF teams and relief stores from
the plains to forward airfields; helicopters fly rescue, medical and relief-drop sorties to
marooned clusters that have no runway; UAVs map the flood extent. Flights stay inside Indian
airspace (Siliguri corridor, no overflight of neighbouring countries) and route around
thunderstorm cells. Airfield and district-headquarters coordinates are public; fleets, crews,
cargo, people counts and weather are NOTIONAL and randomly generated.
"""
from __future__ import annotations

import random

from .geo import haversine_km, in_india, ring_offsets
from .models import Aircraft, Base, Crew, Mission, RestrictedZone, Role, Window, World
from .scenario import TYPES

# (id, name, lat, lon, {type: count})
BASES = [
    ("HDN", "Hindan", 28.708, 77.358, {"HEAVYLIFT": 3, "MEDLIFT": 4}),
    ("AGR", "Agra", 27.156, 77.961, {"MEDLIFT": 3}),
    ("GKP", "Gorakhpur", 26.739, 83.449, {"HELO-M": 2}),
    ("BHT", "Bihta", 25.591, 84.880, {"HELO-M": 3, "HELO-L": 2, "UAV-MALE": 1}),
    ("BGD", "Bagdogra", 26.681, 88.328, {"HELO-M": 3}),
    ("BKP", "Barrackpore", 22.782, 88.359, {"HELO-M": 2}),
    ("GAU", "Guwahati", 26.106, 91.586, {"HELO-M": 3, "HELO-L": 2, "MEDLIFT": 2}),
    ("TEZ", "Tezpur", 26.709, 92.784, {"HELO-L": 3}),
    ("JRH", "Jorhat", 26.731, 94.175, {"HELO-M": 3, "MEDLIFT": 2, "UAV-MALE": 2}),
]

# Forward airfields for fixed-wing relief (notional runway lengths).
AIRFIELDS = [
    ("GAU", "Guwahati", 26.106, 91.586, 3100, "NDRF teams and boats"),
    ("BHT", "Bihta", 25.591, 84.880, 2700, "NDRF teams and boats"),
    ("JRH", "Jorhat", 26.731, 94.175, 2700, "relief stores"),
    ("BGD", "Bagdogra", 26.681, 88.328, 2750, "relief stores"),
    ("LIL", "Lilabari", 27.295, 94.098, 1400, "relief stores"),
]

# Flood-hit districts (district HQ); marooned clusters are placed near them.
DISTRICTS = [
    ("Dhemaji", 27.48, 94.58), ("Lakhimpur", 27.24, 94.10), ("Majuli", 26.95, 94.17),
    ("Morigaon", 26.25, 92.34), ("Barpeta", 26.32, 91.00), ("Dhubri", 26.02, 89.98),
    ("Goalpara", 26.17, 90.62), ("Nalbari", 26.44, 91.44), ("Darrang", 26.45, 92.03),
    ("Supaul", 26.12, 86.60), ("Saharsa", 25.88, 86.60), ("Darbhanga", 26.15, 85.90),
    ("Madhubani", 26.35, 86.07), ("Sitamarhi", 26.60, 85.48), ("Khagaria", 25.50, 86.47),
]

AREA = (22.0, 29.5, 76.5, 96.0)


def _near(rng: random.Random, lat: float, lon: float, km: float = 15.0) -> tuple[float, float]:
    """A point within km of (lat, lon), inside India with a 5 km margin."""
    for _ in range(200):
        dlat, dlon = rng.uniform(-km, km) / 111.32, rng.uniform(-km, km) / 100.0
        p = (round(lat + dlat, 3), round(lon + dlon, 3))
        if in_india(*p) and in_india(*ring_offsets(*p, 5.0)).all():
            return p
    return lat, lon


def generate_hadr(seed: int = 7) -> World:
    rng = random.Random(seed)
    bases, aircraft, crews = {}, {}, {}
    for bid, name, lat, lon, fleet in BASES:
        bases[bid] = Base(id=bid, name=name, lat=lat, lon=lon)
        for tname, count in fleet.items():
            for k in range(count):
                tail = f"{bid}-{tname}-{k + 1:02d}"
                aircraft[tail] = Aircraft(tail=tail, type=tname, base=bid,
                                          p_serviceable=round(rng.uniform(0.80, 0.97), 2))
            helo = tname.startswith("HELO")
            for k in range(int(count * 1.5) + 1):
                cid = f"{bid}-{tname}-C{k + 1:02d}"
                crews[cid] = Crew(id=cid, base=bid, qualified=tname,
                                  night_qualified=rng.random() < (0.4 if helo else 0.85),  # NVG for helicopters
                                  max_flight_min=900 if tname == "UAV-MALE" else 480,
                                  wake_time=rng.choice([-120, 240, 300, 360, 420, 480, 600]),
                                  sleep_hours=round(rng.uniform(5.5, 8.5), 1))

    missions: dict[str, Mission] = {}

    def add(m: Mission) -> None:
        missions[m.id] = m

    # Strategic lift: plains -> forward airfields (fixed wing; runway length matters).
    for k, (aid, name, lat, lon, runway, what) in enumerate(AIRFIELDS):
        start = rng.randrange(120, 600, 30)
        cargo = {"GAU": 60, "BHT": 40}.get(aid, rng.choice([18, 30, 36]))
        add(Mission(id=f"LIFT-{k + 1:02d}", role=Role.AIRLIFT, priority=8 if "NDRF" in what else rng.randint(6, 7),
                    lat=lat, lon=lon, tot_earliest=start, tot_latest=start + 240, on_station_min=60, package=0,
                    cargo_t=float(cargo), runway_m=runway, max_risk=0.05,
                    label=f"{what[0].upper()}{what[1:]} to {name} ({runway:,} m)"))

    picks = rng.sample(DISTRICTS, len(DISTRICTS))
    # Rescue: marooned people, helicopters only; two at night (NVG crews), the rest by day.
    for k, (name, lat, lon) in enumerate(picks[:8]):
        people = rng.choice([20, 30, 40, 60, 80])
        start = rng.randrange(60, 240, 30) if k < 2 else rng.randrange(360, 1000, 30)
        p = _near(rng, lat, lon)
        add(Mission(id=f"RSC-{k + 1:02d}", role=Role.AIRLIFT, priority=rng.randint(9, 10) if k < 3 else 8,
                    lat=p[0], lon=p[1], tot_earliest=start, tot_latest=start + 120, on_station_min=30, package=0,
                    cargo_t=people / 10, runway_m=0, max_risk=0.05,
                    label=f"Rescue: ~{people} people marooned near {name}"))
    # Medical teams and supplies.
    for k, (name, lat, lon) in enumerate(picks[8:10]):
        start = rng.randrange(420, 900, 30)
        p = _near(rng, lat, lon)
        add(Mission(id=f"MED-{k + 1:02d}", role=Role.AIRLIFT, priority=8, lat=p[0], lon=p[1],
                    tot_earliest=start, tot_latest=start + 120, on_station_min=30, package=0, cargo_t=2.0,
                    runway_m=0, max_risk=0.05, label=f"Medical team and supplies, {name}"))
    # Relief drops by day.
    for k, (name, lat, lon) in enumerate(picks[2:15]):
        start = rng.randrange(360, 900, 30)
        p = _near(rng, lat, lon, km=25.0)
        add(Mission(id=f"DRP-{k + 1:02d}", role=Role.AIRLIFT, priority=rng.randint(4, 7), lat=p[0], lon=p[1],
                    tot_earliest=start, tot_latest=start + 150, on_station_min=20, package=0,
                    cargo_t=float(rng.choice([6, 8, 10, 12, 16])), runway_m=0, max_risk=0.05,
                    label=f"Relief drop: food and water, {name}"))
    # Flood-extent mapping.
    for k, (name, lat, lon) in enumerate([("Brahmaputra valley", 26.55, 92.6), ("Kosi basin", 26.05, 86.45)]):
        start = rng.randrange(0, 240, 30)
        add(Mission(id=f"MAP-{k + 1:02d}", role=Role.ISR, priority=5, lat=lat, lon=lon, tot_earliest=start,
                    tot_latest=start + 120, on_station_min=300, package=1, max_risk=0.3,
                    label=f"Flood-extent mapping, {name}"))

    # Monsoon weather: afternoon heavy rain at two airfields, and thunderstorm cells to route around.
    bases["GAU"].closures.append(Window(start=780, end=900, reason="heavy rain, below minima"))
    other = rng.choice(["JRH", "BGD", "BHT"])
    s = rng.randrange(840, 960, 30)
    bases[other].closures.append(Window(start=s, end=s + 90, reason="heavy rain, below minima"))
    keep_clear = [(b.lat, b.lon) for b in bases.values()] + [(m.lat, m.lon) for m in missions.values()]
    zones = {}
    for k in range(rng.randint(2, 3)):
        for _ in range(500):
            name, lat, lon = rng.choice(DISTRICTS)
            lat, lon = lat + rng.uniform(-0.6, 0.6), lon + rng.uniform(-0.8, 0.8)
            r = rng.uniform(20, 30)
            if in_india(lat, lon) and all(haversine_km(lat, lon, a, b) > r + 25 for a, b in keep_clear):
                zid = f"CB-{k + 1}"
                zones[zid] = RestrictedZone(id=zid, lat=round(lat, 3), lon=round(lon, 3), radius_km=round(r),
                                            reason="Thunderstorm cell (CB): avoid")
                keep_clear.append((lat, lon))
                break

    return World(bases=bases, types=dict(TYPES), aircraft=aircraft, crews=crews, threats={}, zones=zones,
                 missions=missions, area=AREA, scenario="hadr", domestic_only=True)
