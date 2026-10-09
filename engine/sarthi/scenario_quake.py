"""Seeded notional HADR scenario: a Himalayan earthquake in the Garhwal hills (Uttarakhand).

The same engine as the flood, with the constraints that matter in the mountains. A notional M6.8
earthquake at 02:40 has cut the roads; relief comes by air. Fixed-wing transports lift NDRF teams,
a field hospital and stores from the plains into the airfields that still work, and the shock has
cracked the runway at Jolly Grant (Dehradun), so only the medium transport can land there and an
aftershock can close it to fixed wing altogether. The two advanced landing grounds in the valleys
take the medium transport only. Everything else goes by helicopter: rescues, casualty evacuation,
medical teams and relief loads to villages and pilgrim halts between 1,100 m and 4,800 m, where
thin air cuts what a helicopter can lift (the light type keeps more of its payload and can land
higher). Flying is by day, and low cloud on the ridges has to be flown around. Flights stay inside
Indian airspace. Airfield and town coordinates and elevations are public and approximate; runway
lengths, fleets, crews, cargo, people counts and weather are NOTIONAL and randomly generated.
"""
from __future__ import annotations

import random

from .geo import haversine_km, in_india
from .models import Aircraft, Base, Crew, Mission, RestrictedZone, Role, Window, World
from .scenario import NOTIONAL_FEEDS, TYPES
from .scenario_hadr import _near

# (id, name, lat, lon, {type: count})
BASES = [
    ("HDN", "Hindan", 28.708, 77.358, {"HEAVYLIFT": 2, "MEDLIFT": 3}),
    ("AGR", "Agra", 27.156, 77.961, {"MEDLIFT": 2}),
    ("IXC", "Chandigarh", 30.673, 76.789, {"MEDLIFT": 2, "HELO-M": 2}),
    ("SSW", "Sarsawa", 29.993, 77.425, {"HELO-M": 3, "HELO-L": 1}),
    ("BEK", "Bareilly", 28.422, 79.452, {"HELO-M": 2, "HELO-L": 1, "UAV-MALE": 1}),
    ("DED", "Jolly Grant", 30.190, 78.180, {"HELO-L": 2}),
    ("PGH", "Pantnagar", 29.033, 79.473, {"HELO-M": 2}),
]
DAMAGED = {"DED": (1500, "earthquake damage: cracks across the eastern half")}

# Fixed-wing destinations: (id, name, lat, lon, usable runway m, elevation m, what, cargo t, priority).
AIRFIELDS = [
    ("DED", "Jolly Grant", 30.190, 78.180, 1500, 560, "NDRF teams and search dogs", 36, 9),
    ("BEK", "Bareilly", 28.422, 79.452, 2800, 170, "field hospital", 60, 8),
    ("CYS", "Chinyalisaur ALG", 30.573, 78.326, 1150, 860, "relief stores", 18, 7),
    ("GCR", "Gauchar ALG", 30.283, 79.153, 1150, 800, "medical teams and supplies", 12, 8),
    ("PGH", "Pantnagar", 29.033, 79.473, 1400, 240, "tents and blankets", 30, 6),
]

# Villages, towns and pilgrim halts cut off by landslides: (name, lat, lon, elevation m).
SITES = [
    ("Uttarkashi", 30.73, 78.44, 1150), ("Harsil", 31.04, 78.74, 2620), ("Gangotri", 30.99, 78.94, 3100),
    ("Gopeshwar", 30.41, 79.32, 1550), ("Joshimath", 30.56, 79.56, 1890), ("Badrinath", 30.74, 79.49, 3300),
    ("Kedarnath", 30.73, 79.07, 3580), ("Guptkashi", 30.52, 78.98, 1320), ("New Tehri", 30.38, 78.43, 1750),
    ("Barkot", 30.81, 78.21, 1220), ("Purola", 30.88, 78.08, 1520), ("Mori", 31.02, 78.04, 1150),
    ("Dharali", 31.04, 78.79, 2700), ("Malari", 30.69, 79.90, 3050), ("Ghuttu", 30.53, 78.74, 1520),
    ("Ukhimath", 30.52, 79.09, 1310),
]
# Above the medium helicopter's landing ceiling: light helicopters only.
HIGH_SITE = ("Kedartal", 30.91, 78.96, 4750)

AREA = (26.8, 32.0, 76.2, 81.2)
DAY = (360, 1020)  # mountain flying by day only: 06:00-17:00


def _day_start(rng: random.Random, lo: int, hi: int, span: int) -> int:
    return rng.randrange(max(lo, DAY[0]), min(hi, DAY[1] - span), 30)


def generate_quake(seed: int = 7) -> World:
    rng = random.Random(seed)
    bases, aircraft, crews = {}, {}, {}
    for bid, name, lat, lon, fleet in BASES:
        runway, note = DAMAGED.get(bid, (None, ""))
        bases[bid] = Base(id=bid, name=name, lat=lat, lon=lon, runway_m=runway, runway_note=note)
        for tname, count in fleet.items():
            for k in range(count):
                tail = f"{bid}-{tname}-{k + 1:02d}"
                aircraft[tail] = Aircraft(tail=tail, type=tname, base=bid,
                                          p_serviceable=round(rng.uniform(0.80, 0.97), 2))
            for k in range(int(count * 1.5) + 1):
                cid = f"{bid}-{tname}-C{k + 1:02d}"
                crews[cid] = Crew(id=cid, base=bid, qualified=tname,
                                  night_qualified=rng.random() < (0.3 if tname.startswith("HELO") else 0.85),
                                  max_flight_min=900 if tname == "UAV-MALE" else 480,
                                  wake_time=rng.choice([-120, 180, 240, 300, 330, 360, 420]),
                                  sleep_hours=round(rng.uniform(5.5, 8.5), 1))

    missions: dict[str, Mission] = {}

    def add(m: Mission) -> None:
        missions[m.id] = m

    # Fixed-wing lift into the airfields that still work (usable runway decides which transport).
    for k, (aid, name, lat, lon, runway, elev, what, cargo, prio) in enumerate(AIRFIELDS):
        alg = name.endswith("ALG")
        start = _day_start(rng, 420, 840, 240) if alg else rng.randrange(180, 600, 30)
        add(Mission(id=f"LIFT-{k + 1:02d}", role=Role.AIRLIFT, priority=prio, lat=lat, lon=lon,
                    tot_earliest=start, tot_latest=start + 240, on_station_min=60, package=0,
                    cargo_t=float(cargo), runway_m=runway, elevation_m=elev, airfield=aid, max_risk=0.05,
                    label=f"{what[0].upper()}{what[1:]} to {name} ({runway:,} m usable)"))

    picks = rng.sample(SITES, len(SITES))
    # Rescue and casualty evacuation: helicopters only; the first light is the priority.
    n_rescue = 7
    for k, (name, lat, lon, elev) in enumerate(picks[:n_rescue]):
        people = rng.choice([20, 30, 40, 50, 60])
        start = _day_start(rng, 360, 480, 120) if k < 3 else _day_start(rng, 480, 840, 120)
        p = _near(rng, lat, lon, km=4.0)
        add(Mission(id=f"RSC-{k + 1:02d}", role=Role.AIRLIFT, priority=rng.randint(9, 10) if k < 3 else 8,
                    lat=p[0], lon=p[1], tot_earliest=start, tot_latest=start + 120, on_station_min=20, package=0,
                    cargo_t=people / 10, runway_m=0, elevation_m=elev, max_risk=0.05,
                    label=f"Rescue and casualty evacuation: ~{people} people, {name} ({elev:,} m)"))
    # Trekkers stranded above the medium helicopter's ceiling.
    name, lat, lon, elev = HIGH_SITE
    start = _day_start(rng, 420, 720, 120)
    add(Mission(id=f"RSC-{n_rescue + 1:02d}", role=Role.AIRLIFT, priority=9, lat=lat, lon=lon,
                tot_earliest=start, tot_latest=start + 120, on_station_min=20, package=0, cargo_t=0.6,
                runway_m=0, elevation_m=elev, max_risk=0.05,
                label=f"Rescue: 6 trekkers stranded at {name} ({elev:,} m)"))
    # Medical teams.
    for k, (name, lat, lon, elev) in enumerate(picks[7:10]):
        start = _day_start(rng, 420, 900, 120)
        p = _near(rng, lat, lon, km=4.0)
        add(Mission(id=f"MED-{k + 1:02d}", role=Role.AIRLIFT, priority=8, lat=p[0], lon=p[1],
                    tot_earliest=start, tot_latest=start + 120, on_station_min=20, package=0, cargo_t=1.5,
                    runway_m=0, elevation_m=elev, max_risk=0.05,
                    label=f"Medical team and supplies, {name} ({elev:,} m)"))
    # Relief loads: food, tents, blankets.
    for k, (name, lat, lon, elev) in enumerate(picks[3:14]):
        start = _day_start(rng, 480, 900, 150)
        p = _near(rng, lat, lon, km=6.0)
        add(Mission(id=f"DRP-{k + 1:02d}", role=Role.AIRLIFT, priority=rng.randint(4, 7), lat=p[0], lon=p[1],
                    tot_earliest=start, tot_latest=start + 150, on_station_min=20, package=0,
                    cargo_t=float(rng.choice([4, 5, 6, 8, 10])), runway_m=0, elevation_m=elev, max_risk=0.05,
                    label=f"Relief load: food, tents and blankets, {name} ({elev:,} m)"))
    # Damage assessment and landslide-dam watch.
    for k, (name, lat, lon) in enumerate([("Bhagirathi valley", 30.85, 78.62), ("Alaknanda valley", 30.48, 79.30)]):
        start = rng.randrange(360, 600, 30)
        add(Mission(id=f"MAP-{k + 1:02d}", role=Role.ISR, priority=6 - k, lat=lat, lon=lon, tot_earliest=start,
                    tot_latest=start + 120, on_station_min=300, package=1, max_risk=0.3,
                    label=f"Damage assessment and landslide-dam watch, {name}"))

    # Mountain weather: morning low cloud in the Doon valley, and cloud on the ridges to fly around.
    s = rng.choice([360, 390, 420])
    bases["DED"].closures.append(Window(start=s, end=s + 90, reason="low cloud, below minima"))
    keep_clear = [(b.lat, b.lon) for b in bases.values()] + [(m.lat, m.lon) for m in missions.values()]
    zones = {}
    for k in range(rng.randint(2, 3)):
        for _ in range(500):
            name, lat, lon, _e = rng.choice(SITES)
            lat, lon = lat + rng.uniform(-0.3, 0.3), lon + rng.uniform(-0.35, 0.35)
            r = rng.uniform(8, 14)
            if in_india(lat, lon) and all(haversine_km(lat, lon, a, b) > r + 8 for a, b in keep_clear):
                zid = f"CLD-{k + 1:02d}"
                zones[zid] = RestrictedZone(id=zid, lat=round(lat, 3), lon=round(lon, 3), radius_km=round(r),
                                            reason="Low cloud on the ridge: no VFR crossing")
                keep_clear.append((lat, lon))
                break

    return World(bases=bases, types=dict(TYPES), aircraft=aircraft, crews=crews, threats={}, zones=zones,
                 missions=missions, area=AREA, scenario="hadr", disaster="earthquake", domestic_only=True,
                 feeds=dict(NOTIONAL_FEEDS))
