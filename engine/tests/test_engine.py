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


def test_frozen_tanker_is_not_double_booked():
    """A tanker committed to a launched package must stay unavailable to later retasks."""
    from sarthi.events import PriorityChange
    from sarthi.models import Aircraft, Base, Mission, Role, World
    from sarthi.scenario import TYPES

    bases = {"B": Base(id="B", name="Fighter base", lat=28.0, lon=77.0, stocks={"PGM": 20}),
             "T": Base(id="T", name="Tanker base", lat=28.0, lon=77.5)}
    aircraft = {f"F{i}": Aircraft(tail=f"F{i}", type="TEJAS", base="B") for i in range(4)}
    aircraft["K1"] = Aircraft(tail="K1", type="TANKER", base="T")
    crews = {f"C{i}": Crew(id=f"C{i}", base="B", qualified="TEJAS", wake_time=300, sleep_hours=8) for i in range(6)}
    # ~650 km north: beyond a TEJAS's 500 km radius, inside its AAR-extended reach.
    strike = dict(role=Role.STRIKE, lat=33.85, lon=77.0, package=2, weapon="PGM", weapons_per_aircraft=2)
    missions = {"A": Mission(id="A", priority=6, tot_earliest=600, tot_latest=600, **strike),
                "B2": Mission(id="B2", priority=5, tot_earliest=700, tot_latest=760, **strike)}
    world = World(bases=bases, types=dict(TYPES), aircraft=aircraft, crews=crews, missions=missions)

    plan = optimizer.solve(world, time_limit=5)
    assert "A" in plan.assignments and plan.assignments["A"].tankers == ["K1"]
    assert "B2" not in plan.assignments  # one tanker, overlapping tanker sorties
    launch_a = min(s.launch for s in plan.assignments["A"].sorties)
    res = retask(world, plan, [PriorityChange(at=launch_a + 5, mission="B2", priority=10)], 5)
    assert res.plan.assignments["A"] == plan.assignments["A"]  # frozen
    assert "B2" not in res.plan.assignments  # cannot steal the committed tanker
    assert validate(res.world, res.plan, candidates.build(res.world),
                    optimizer.frozen_missions(res.world, res.plan)) == []


def test_coas_trade_off_and_respect_their_intents(planned):
    from sarthi import coa
    world, _, _, plan = planned
    res = {c.id: (w, c) for w, c in coa.compare(world, plan, time_limit=5)}
    for w, c in res.values():
        assert validate(w, c.plan, candidates.build(w), optimizer.frozen_missions(w, c.plan)) == [], c.name
    eff, risk, defend = res["effect"][1], res["risk"][1], res["defend"][1]
    assert risk.metrics["expected_losses"] <= eff.metrics["expected_losses"]
    assert risk.kpis["max_sortie_risk"] <= 0.6 * max(m.max_risk for m in world.missions.values()) + 1e-6
    w_def = res["defend"][0]
    assert not any(w_def.deferred(w_def.missions[m]) for m in defend.plan.assignments)
    assert defend.metrics["munitions_total"] <= eff.metrics["munitions_total"]


def test_success_model_and_ground_spares(planned):
    from sarthi import robust
    world, cands, _, plan = planned
    sim = robust.simulate(world, plan, runs=4000)
    assert abs(sim["mean"] - kpis(world, plan)["expected_value"]) < 0.01  # analytic == Monte Carlo
    w = world.model_copy(deep=True)
    w.spare_policy = True
    hard = robust.add_spares(w, plan, cands)
    assert validate(w, hard, cands) == []
    from sarthi.retask import diff_plans
    d = diff_plans(w, plan, hard)
    assert d.aircraft_changes == 0 and d.crew_changes == 0 and d.tot_shifts == 0 and d.spare_changes > 0
    for a in hard.assignments.values():
        elements = {(s.base, w.aircraft[s.tail].type) for s in a.sorties}
        assert all((s.base, w.aircraft[s.tail].type) in elements for s in a.spares)
    assert robust.expected_value(w, hard) > robust.expected_value(world, plan)
    assert robust.simulate(w, hard)["p05"] >= sim["p05"]
    # Losing a SEAD aircraft also loses the strike that depends on it.
    for sp in sim["single_points"]:
        lost = {m.split(" ")[0] for m in sp["missions"]}
        for mid in lost:
            for dep in (m for m in world.missions.values() if m.depends_on == mid and m.id in plan.assignments):
                assert dep.id in lost


def test_ground_spare_steps_in_for_unserviceable_primary(planned):
    from sarthi import robust
    world, cands, _, plan = planned
    w = world.model_copy(deep=True)
    w.spare_policy = True
    hard = robust.add_spares(w, plan, cands)
    mid, a = next((m, a) for m, a in sorted(hard.assignments.items(), key=lambda kv: kv[1].tot)
                  if a.spares and min(s.launch for s in a.sorties) > 200)
    spare = a.spares[0]
    primary = next(s for s in a.sorties if s.base == spare.base
                   and w.aircraft[s.tail].type == w.aircraft[spare.tail].type)
    res = retask(w, hard, [AircraftDown(at=min(s.launch for s in a.sorties) - 60, tails=[primary.tail])], 8)
    new = res.plan.assignments[mid]
    assert spare.tail in {s.tail for s in new.sorties}
    assert validate(res.world, res.plan, candidates.build(res.world),
                    optimizer.frozen_missions(res.world, res.plan)) == []
