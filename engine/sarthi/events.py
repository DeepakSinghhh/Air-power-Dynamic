"""Operational events that trigger dynamic retasking."""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from .geo import fmt_time
from .models import Mission, RestrictedZone, Threat, Window, World


class _Event(BaseModel):
    at: int  # minute the information arrives


class BaseClosure(_Event):
    kind: Literal["base_closure"] = "base_closure"
    base: str
    start: int
    end: int
    reason: str = "weather below minima"
    probability: float | None = None

    def apply(self, w: World) -> str:
        w.bases[self.base].closures.append(Window(start=self.start, end=self.end, reason=self.reason,
                                                  probability=self.probability))
        p = f", P {self.probability:.0%}" if self.probability is not None else ""
        return f"{w.bases[self.base].name} closed {fmt_time(self.start)}-{fmt_time(self.end)} ({self.reason}{p})"


class AircraftDown(_Event):
    kind: Literal["aircraft_down"] = "aircraft_down"
    tails: list[str]
    reason: str = "unserviceable"

    def apply(self, w: World) -> str:
        for t in self.tails:
            w.aircraft[t].serviceable = False
        return f"{len(self.tails)} aircraft {self.reason}: {', '.join(self.tails)}"


class CrewDown(_Event):
    kind: Literal["crew_down"] = "crew_down"
    crews: list[str]

    def apply(self, w: World) -> str:
        for c in self.crews:
            w.crews[c].available = False
        return f"{len(self.crews)} crews unavailable"


class NewThreat(_Event):
    kind: Literal["new_threat"] = "new_threat"
    threat: Threat

    def apply(self, w: World) -> str:
        self.threat.observed_at = self.at
        w.threats[self.threat.id] = self.threat
        return f"Pop-up {self.threat.kind} {self.threat.id} at {self.threat.lat:.2f}, {self.threat.lon:.2f}"


class NewMission(_Event):
    kind: Literal["new_mission"] = "new_mission"
    mission: Mission

    def apply(self, w: World) -> str:
        w.missions[self.mission.id] = self.mission
        return f"New {self.mission.role.value} tasking {self.mission.id} (P{self.mission.priority})"


class CancelMission(_Event):
    kind: Literal["cancel_mission"] = "cancel_mission"
    mission: str

    def apply(self, w: World) -> str:
        w.missions.pop(self.mission, None)
        return f"{self.mission} cancelled"


class PriorityChange(_Event):
    kind: Literal["priority_change"] = "priority_change"
    mission: str
    priority: int

    def apply(self, w: World) -> str:
        m = w.missions[self.mission]
        old, m.priority = m.priority, max(1, min(10, self.priority))
        return f"{self.mission} priority P{old} -> P{m.priority} (commander's intent)"


class StockLoss(_Event):
    kind: Literal["stock_loss"] = "stock_loss"
    base: str
    weapon: str
    qty: int

    def apply(self, w: World) -> str:
        s = w.bases[self.base].stocks
        s[self.weapon] = max(0, s.get(self.weapon, 0) - self.qty)
        return f"{self.qty}x {self.weapon} lost at {w.bases[self.base].name}"


class RiskAcceptance(_Event):
    """Commander accepts more (or less) route risk for one mission."""
    kind: Literal["risk_acceptance"] = "risk_acceptance"
    mission: str
    max_risk: float

    def apply(self, w: World) -> str:
        m = w.missions[self.mission]
        old, m.max_risk = m.max_risk, max(0.0, min(0.95, self.max_risk))
        return f"{self.mission} acceptable route risk {old:.0%} -> {m.max_risk:.0%}"


class WindowChange(_Event):
    """The supported commander widens or moves a mission's time-on-target window."""
    kind: Literal["window_change"] = "window_change"
    mission: str
    tot_earliest: int
    tot_latest: int

    def apply(self, w: World) -> str:
        m = w.missions[self.mission]
        m.tot_earliest, m.tot_latest = max(w.now, self.tot_earliest), max(w.now, self.tot_latest)
        return f"{self.mission} TOT window {fmt_time(m.tot_earliest)}-{fmt_time(m.tot_latest)}"


class Resupply(_Event):
    kind: Literal["resupply"] = "resupply"
    base: str
    weapon: str
    qty: int

    def apply(self, w: World) -> str:
        s = w.bases[self.base].stocks
        s[self.weapon] = s.get(self.weapon, 0) + self.qty
        return f"{self.qty}x {self.weapon} delivered to {w.bases[self.base].name}"


class NewZone(_Event):
    """Airspace to avoid from now on: a thunderstorm cell, a temporary restriction."""
    kind: Literal["new_zone"] = "new_zone"
    zone: RestrictedZone

    def apply(self, w: World) -> str:
        w.zones[self.zone.id] = self.zone
        return f"{self.zone.reason}: {self.zone.id}, {self.zone.radius_km:.0f} km at {self.zone.lat:.2f}, {self.zone.lon:.2f}"


Event = Annotated[Union[BaseClosure, AircraftDown, CrewDown, NewThreat, NewMission, CancelMission,
                        PriorityChange, StockLoss, NewZone, RiskAcceptance, WindowChange, Resupply],
                  Field(discriminator="kind")]


FEED_OF = {"base_closure": "airfields", "aircraft_down": "maintenance", "crew_down": "crews", "new_threat": "intel",
           "new_mission": "tasking", "cancel_mission": "tasking", "priority_change": "tasking",
           "risk_acceptance": "tasking", "window_change": "tasking", "stock_loss": "armament",
           "resupply": "armament", "new_zone": "airspace"}


def apply_events(world: World, events: list) -> tuple[World, list[str]]:
    w = world.model_copy(deep=True)
    notes = []
    for e in sorted(events, key=lambda e: e.at):
        w.now = max(w.now, e.at)
        notes.append(e.apply(w))
        w.feeds[FEED_OF[e.kind]] = max(w.feeds.get(FEED_OF[e.kind], -10**6), e.at)
    return w, notes
