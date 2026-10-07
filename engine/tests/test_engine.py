import pytest

from sarthi import candidates, greedy, optimizer
from sarthi.events import AircraftDown, BaseClosure, NewThreat
from sarthi.fatigue import effectiveness, fit_tot_domain
from sarthi.geo import haversine_km, subtract
from sarthi.kpi import kpis
from sarthi.models import Crew, Threat
from sarthi.retask import retask
from sarthi.scenario import generate
from sarthi.threats import RiskField
from sarthi.validate import validate


@pytest.fixture(scope="module")
def planned():
    world = generate(11)
    cands = candidates.build(world)
    g = greedy.solve(world, cands)
    plan = optimizer.solve(world, cands, hint=g, time_limit=8)
    return world, cands, g, plan


def test_haversine_known_distance():
    # Delhi (IGI) to Mumbai (CSMIA) is ~1,140 km great-circle.
    assert 1100 < haversine_km(28.556, 77.100, 19.089, 72.866) < 1170


def test_interval_subtract():
    assert subtract([(0, 100)], 20, 30) == [(0, 19), (31, 100)]
    assert subtract([(0, 10)], 20, 30) == [(0, 10)]


def test_fatigue_rested_vs_sleep_deprived():
    crew = Crew(id="c", base="b", qualified="t", wake_time=420, sleep_hours=8)
    assert effectiveness(crew, 600) > 90           # 10:00 after a full night
    assert effectiveness(crew, 420 + 21 * 60) < 77  # 04:00 next day after 21 h awake


def test_non_night_crew_gets_no_night_tots():
    crew = Crew(id="c", base="b", qualified="t", night_qualified=False, wake_time=420, sleep_hours=8)
    dom = fit_tot_domain(crew, 60, 17 * 60, 23 * 60, 70)
    assert dom and all(hi + 60 < 19 * 60 for _, hi in dom)


def test_routes_avoid_threats():
    world = generate(11)
    world.threats = {}
    clear = RiskField(world).routes_to(31.0, 73.0, {"X": (30.0, 76.0)})["X"]
    world.threats["T"] = Threat(id="T", kind="SAM-LR", lat=30.5, lon=74.5, radius_km=60, pk=0.6)
    threatened = RiskField(world).routes_to(31.0, 73.0, {"X": (30.0, 76.0)})["X"]
    assert clear.risk == 0
    assert threatened.km > clear.km          # detours around the envelope
    assert threatened.risk < 0.6             # rather than flying straight through


def test_both_planners_satisfy_all_constraints(planned):
    world, cands, g, plan = planned
    assert validate(world, g, cands) == []
    assert validate(world, plan, cands) == []


def test_optimiser_beats_or_matches_greedy(planned):
    world, _, g, plan = planned
    assert kpis(world, plan)["priority_weighted_fulfilment"] >= kpis(world, g)["priority_weighted_fulfilment"]


def test_every_unplanned_mission_is_explained(planned):
    world, _, _, plan = planned
    for mid in set(world.missions) - set(plan.assignments):
        assert plan.unassigned.get(mid), mid


def test_base_closure_retask_is_valid_and_minimal(planned):
    world, _, _, plan = planned
    busiest = max(world.bases, key=lambda b: sum(s.base == b for a in plan.assignments.values() for s in a.sorties))
    res = retask(world, plan, [BaseClosure(at=120, base=busiest, start=300, end=570)], 8, compare_naive=True)
    new_cands = candidates.build(res.world)
    assert validate(res.world, res.plan, new_cands, optimizer.frozen_missions(res.world, res.plan)) == []
    for a in res.plan.assignments.values():
        for s in a.sorties:
            if s.base == busiest and s.launch > 120:
                assert not (300 <= s.launch <= 570 or 300 <= s.recover <= 570)
    assert res.diff.aircraft_changes <= res.naive_diff.aircraft_changes


def test_launched_missions_are_frozen(planned):
    world, _, _, plan = planned
    now = 400
    launched = {m for m, a in plan.assignments.items() if min(s.launch for s in a.sorties) <= now}
    tails = sorted({s.tail for m in launched for s in plan.assignments[m].sorties})
    res = retask(world, plan, [AircraftDown(at=now, tails=tails[:2])], 8)
    for m in launched:
        assert res.plan.assignments[m] == plan.assignments[m]


def test_popup_threat_raises_or_reroutes(planned):
    world, _, _, plan = planned
    res = retask(world, plan, [NewThreat(at=60, threat=Threat(id="P", kind="SAM-MR", lat=31.0, lon=73.5,
                                                              radius_km=45, pk=0.5))], 8)
    assert res.plan.status in ("OPTIMAL", "FEASIBLE")
    assert validate(res.world, res.plan, candidates.build(res.world),
                    optimizer.frozen_missions(res.world, res.plan)) == []
