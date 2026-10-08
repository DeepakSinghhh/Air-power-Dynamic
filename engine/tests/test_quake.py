import pytest

from sarthi import candidates, greedy, optimizer
from sarthi.events import RunwayDamage, apply_events
from sarthi.geo import in_india
from sarthi.kpi import kpis
from sarthi.presets import presets
from sarthi.retask import retask
from sarthi.scenario_quake import HIGH_SITE, generate_quake
from sarthi.validate import validate


@pytest.fixture(scope="module")
def quake():
    world = generate_quake(7)
    cands = candidates.build(world)
    g = greedy.solve(world, cands)
    plan = optimizer.solve(world, cands, hint=g, time_limit=6)
    return world, cands, g, plan


def test_payload_falls_with_elevation():
    world = generate_quake(7)
    m, l = world.types["HELO-M"], world.types["HELO-L"]
    assert m.payload_at(0) == m.payload_t and m.payload_at(3580) < 0.6 * m.payload_t
    assert l.payload_at(3580) / l.payload_t > m.payload_at(3580) / m.payload_t  # light type is better hot and high
    assert world.types["MEDLIFT"].payload_at(3000) == world.types["MEDLIFT"].payload_t  # fixed wing: not derated


def test_quake_plan_respects_runways_ceilings_and_thin_air(quake):
    world, cands, g, plan = quake
    assert world.disaster == "earthquake" and world.scenario == "hadr"
    assert validate(world, g, cands) == [] and validate(world, plan, cands) == []
    assert kpis(world, plan)["priority_weighted_fulfilment"] >= kpis(world, g)["priority_weighted_fulfilment"]
    assert all(in_india(m.lat, m.lon) for m in world.missions.values())
    for mid, a in plan.assignments.items():
        m = world.missions[mid]
        types = [world.types[world.aircraft[s.tail].type] for s in a.sorties]
        assert sum(t.payload_at(m.elevation_m) for t in types) >= m.cargo_t - 1e-6, mid
        for s, t in zip(a.sorties, types):
            if m.runway_m is not None:
                assert t.min_runway_m <= m.runway_m, (mid, s.tail)
            if t.max_landing_m is not None:
                assert m.elevation_m <= t.max_landing_m, (mid, s.tail)
            assert all(in_india(lat, lon) for lat, lon in s.route), (mid, s.tail)
    # The cracked runway at Jolly Grant takes the medium transport, not the heavy one.
    ded = [m for m in world.missions.values() if m.airfield == "DED"]
    assert ded and all(world.aircraft[s.tail].type == "MEDLIFT"
                       for m in ded if m.id in plan.assignments for s in plan.assignments[m.id].sorties)
    # Above the medium helicopter's ceiling only the light one can go.
    high = next(m for m in world.missions.values() if m.elevation_m == HIGH_SITE[3])
    assert {p.type for p in cands.pairs[high.id]} == {"HELO-L"}
    assert any("too high" in r for r in cands.rejects[high.id])


def test_runway_damage_stops_landings_and_take_offs():
    world = generate_quake(7)
    world, _ = apply_events(world, [RunwayDamage(at=0, airfield="DED", usable_m=900),
                                         RunwayDamage(at=0, airfield="HDN", usable_m=1500)])
    cands = candidates.build(world)
    ded = next(m for m in world.missions.values() if m.airfield == "DED")
    assert ded.runway_m == 900 and "900 m usable" in ded.label
    assert all(p.type.startswith("HELO") for p in cands.pairs[ded.id])  # helicopters can still land there
    assert any("too short" in r for r in cands.rejects[ded.id])
    bek = next(m for m in world.missions.values() if m.airfield == "BEK")
    assert not any(p.base == "HDN" and p.type == "HEAVYLIFT" for p in cands.pairs[bek.id])
    assert any("Hindan too short to take off" in r for r in cands.rejects[bek.id])


def test_quake_events_retask_validly(quake):
    world, _, _, plan = quake
    for at in (0, 360):
        ps = presets(world, plan, at)
        assert {"aftershock", "landslide", "hospital"} <= {p.id for p in ps}
        for p in ps:
            res = retask(world, plan, p.events, 5)
            errs = validate(res.world, res.plan, candidates.build(res.world),
                            optimizer.frozen_missions(res.world, res.plan))
            assert errs == [], (at, p.id, errs[:3])
            if p.id == "aftershock":
                why = " ".join(res.plan.unassigned.get("LIFT-01", []))
                assert "LIFT-01" in res.plan.unassigned and "Not enough lift" in why and "runway too short" in why, why
                for m in res.world.missions.values():
                    if m.airfield == "DED" and m.id in res.plan.assignments \
                            and m.id not in optimizer.frozen_missions(res.world, res.plan):
                        pytest.fail(f"{m.id} still lands on a 900 m runway")
