import pytest

from sarthi import candidates, greedy, optimizer
from sarthi.geo import in_india
from sarthi.kpi import kpis
from sarthi.presets import presets
from sarthi.retask import retask
from sarthi.scenario_hadr import generate_hadr
from sarthi.validate import validate


@pytest.fixture(scope="module")
def hadr():
    world = generate_hadr(7)
    cands = candidates.build(world)
    g = greedy.solve(world, cands)
    plan = optimizer.solve(world, cands, hint=g, time_limit=6)
    return world, cands, g, plan


def test_hadr_plan_is_valid_domestic_and_uses_the_right_aircraft(hadr):
    world, cands, g, plan = hadr
    assert validate(world, g, cands) == [] and validate(world, plan, cands) == []
    assert kpis(world, plan)["priority_weighted_fulfilment"] >= kpis(world, g)["priority_weighted_fulfilment"]
    assert all(in_india(b.lat, b.lon) for b in world.bases.values())
    assert all(in_india(m.lat, m.lon) for m in world.missions.values())
    for mid, a in plan.assignments.items():
        m = world.missions[mid]
        for s in a.sorties:
            t = world.types[world.aircraft[s.tail].type]
            if m.runway_m is not None:
                assert t.min_runway_m <= m.runway_m, (mid, s.tail)  # helicopters only where there is no runway
            assert all(in_india(lat, lon) for lat, lon in s.route), (mid, s.tail)  # no foreign overflight


def test_hadr_events_retask_validly_after_launches(hadr):
    world, _, _, plan = hadr
    for at in (0, 360):
        for p in presets(world, plan, at):
            res = retask(world, plan, p.events, 5)
            errs = validate(res.world, res.plan, candidates.build(res.world),
                            optimizer.frozen_missions(res.world, res.plan))
            assert errs == [], (at, p.id, errs[:3])
