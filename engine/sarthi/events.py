"""Operational events that trigger dynamic retasking."""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from .geo import fmt_time
from .models import Mission, Threat, Window, World


class _Event(BaseModel):
    at: int  # minute the information arrives


class BaseClosure(_Event):
    kind: Literal["base_closure"] = "base_closure"
    base: str
    start: int
    end: int
    reason: str = "weather below minima"

    def apply(self, w: World) -> str:
        w.bases[self.base].closures.append(Window(start=self.start, end=self.end, reason=self.reason))
        return f"{w.bases[self.base].name} closed {fmt_time(self.start)}-{fmt_time(self.end)} ({self.reason})"


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


Event = Annotated[Union[BaseClosure, AircraftDown, CrewDown, NewThreat, NewMission, CancelMission,
                        PriorityChange, StockLoss],
                  Field(discriminator="kind")]


def apply_events(world: World, events: list) -> tuple[World, list[str]]:
    w = world.model_copy(deep=True)
    notes = []
    for e in sorted(events, key=lambda e: e.at):
        w.now = max(w.now, e.at)
        notes.append(e.apply(w))
    return w, notes
