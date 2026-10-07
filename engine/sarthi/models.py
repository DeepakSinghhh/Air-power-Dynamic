"""Domain model for the air-operations planning engine.

All times are integer minutes from 00:00 local on the planning day (D-day).
Values above 1440 spill into the next day. All data is notional.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Role(str, Enum):
    DCA = "DCA"            # defensive counter-air / combat air patrol
    STRIKE = "STRIKE"      # offensive counter-air / interdiction
    SEAD = "SEAD"          # suppression of enemy air defences
    CAS = "CAS"            # close air support
    ISR = "ISR"            # intelligence, surveillance, reconnaissance
    AEW = "AEW"            # airborne early warning
    AAR = "AAR"            # air-to-air refuelling (tanker)
    AIRLIFT = "AIRLIFT"    # transport / logistics / HADR


class Window(BaseModel):
    start: int
    end: int
    reason: str = ""


class Base(BaseModel):
    id: str
    name: str
    lat: float
    lon: float
    closures: list[Window] = Field(default_factory=list)
    stocks: dict[str, int] = Field(default_factory=dict)  # weapon category -> rounds
    fighter_reserve: int = 0  # DCA-capable aircraft held back on alert at all times


class AircraftType(BaseModel):
    name: str
    roles: list[Role]
    speed_kmh: float
    combat_radius_km: float
    prep_min: int = 30          # start-up, taxi, take-off
    turnaround_min: int = 90    # re-arm, refuel, quick servicing after landing
    weapons: dict[str, int] = Field(default_factory=dict)  # category -> max per sortie
    payload_t: float = 0.0      # airlift payload (tonnes)
    aar_receivers: int = 0      # receivers one tanker sortie can support
    fighter: bool = False       # counts toward the base alert reserve


class Aircraft(BaseModel):
    tail: str
    type: str
    base: str
    serviceable: bool = True
    p_serviceable: float = 0.95  # predicted probability it is mission-capable at launch
    available_from: int = 0


class Crew(BaseModel):
    id: str
    base: str
    qualified: str               # aircraft type the crew is current on
    night_qualified: bool = True
    wake_time: int = 360         # minute of D-day the crew woke up
    sleep_hours: float = 7.5     # sleep obtained before wake_time
    max_sorties: int = 2
    max_flight_min: int = 480
    available: bool = True


class Threat(BaseModel):
    id: str
    kind: str
    lat: float
    lon: float
    radius_km: float
    pk: float                    # probability of kill for a full crossing of the envelope
    observed_at: int = 0         # minute the position was last confirmed
    mobile_kmh: float = 0.0      # relocation speed; drives positional uncertainty growth


class RestrictedZone(BaseModel):
    id: str
    lat: float
    lon: float
    radius_km: float
    reason: str = "restricted airspace"


class Mission(BaseModel):
    id: str
    role: Role
    priority: int                # 1 (low) .. 10 (critical)
    lat: float
    lon: float
    tot_earliest: int            # time-on-target window
    tot_latest: int
    on_station_min: int = 10
    package: int = 2             # aircraft required (non-airlift)
    weapon: str | None = None    # weapon category required
    weapons_per_aircraft: int = 0
    cargo_t: float = 0.0         # airlift only
    max_risk: float = 0.35       # max acceptable route risk (two-way)
    depends_on: str | None = None
    dep_lag_min: int = 5         # this TOT - dependency TOT must lie in [min, max]
    dep_lag_max: int = 30
    suppresses: list[str] = Field(default_factory=list)  # SEAD: threats it suppresses
    label: str = ""


class World(BaseModel):
    """Single fused state of everything the planner needs."""

    now: int = 0
    horizon: int = 1440
    area: tuple[float, float, float, float] = (22.0, 37.0, 68.0, 82.0)  # lat0, lat1, lon0, lon1
    bases: dict[str, Base]
    types: dict[str, AircraftType]
    aircraft: dict[str, Aircraft]
    crews: dict[str, Crew]
    threats: dict[str, Threat] = Field(default_factory=dict)
    zones: dict[str, RestrictedZone] = Field(default_factory=dict)
    missions: dict[str, Mission]
    aar_extension: float = 1.5   # tanker support extends reach to radius * this
    fatigue_threshold: float = 77.0


class Sortie(BaseModel):
    tail: str
    base: str
    crew: str | None = None
    launch: int
    recover: int
    route_km: float
    risk: float
    needs_aar: bool = False
    route: list[tuple[float, float]] = Field(default_factory=list)


class Assignment(BaseModel):
    mission: str
    tot: int
    sorties: list[Sortie]
    tankers: list[str] = Field(default_factory=list)
    tanker_sorties: list[Sortie] = Field(default_factory=list)  # timing + track for each tanker


class Plan(BaseModel):
    assignments: dict[str, Assignment] = Field(default_factory=dict)
    unassigned: dict[str, list[str]] = Field(default_factory=dict)  # mission -> explanation lines
    solver: str = ""
    status: str = ""
    solve_seconds: float = 0.0
    objective: float = 0.0
