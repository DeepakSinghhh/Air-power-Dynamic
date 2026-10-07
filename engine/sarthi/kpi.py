"""Plan quality metrics and Monte Carlo robustness."""
from __future__ import annotations

import random
from collections import Counter

import numpy as np

from .models import Plan, World


def kpis(world: World, plan: Plan) -> dict:
    total_p = sum(m.priority for m in world.missions.values()) or 1
    done_p = sum(world.missions[m].priority for m in plan.assignments if m in world.missions)
    exp_v, risks = 0.0, []
    for mid, a in plan.assignments.items():
        if mid not in world.missions:
            continue
        p = 1.0
        for s in a.sorties:
            p *= world.aircraft[s.tail].p_serviceable * (1 - s.risk)
            risks.append(s.risk)
        exp_v += world.missions[mid].priority * p
    return {
        "missions_planned": len(plan.assignments),
        "missions_total": len(world.missions),
        "priority_weighted_fulfilment": round(done_p / total_p, 3),
        "expected_value": round(exp_v / total_p, 3),
        "sorties": sum(len(a.sorties) for a in plan.assignments.values()),
        "tanker_sorties": sum(len(a.tankers) for a in plan.assignments.values()),
        "mean_sortie_risk": round(float(np.mean(risks)), 4) if risks else 0.0,
        "max_sortie_risk": round(float(np.max(risks)), 4) if risks else 0.0,
        "solve_seconds": plan.solve_seconds,
    }


def stress_test(world: World, plan: Plan, runs: int = 2000, seed: int = 0) -> dict:
    """Execute the plan many times under random unserviceability and attrition.

    Reports the spread of priority-weighted mission success *without* replanning
    and the missions and assets the plan leans on hardest (single points of failure).
    """
    rng = random.Random(seed)
    total_p = sum(m.priority for m in world.missions.values()) or 1
    fails = Counter()
    scores = []
    for _ in range(runs):
        score = 0.0
        for mid, a in plan.assignments.items():
            ok = all(rng.random() < world.aircraft[s.tail].p_serviceable and rng.random() >= s.risk
                     for s in a.sorties)
            if ok:
                score += world.missions[mid].priority
            else:
                fails[mid] += 1
        scores.append(score / total_p)
    reliance = Counter()
    for a in plan.assignments.values():
        for t in a.tankers:
            reliance[t] += 1
        for s in a.sorties:
            reliance[s.tail] += 1
    return {
        "p05": round(float(np.percentile(scores, 5)), 3),
        "p50": round(float(np.percentile(scores, 50)), 3),
        "p95": round(float(np.percentile(scores, 95)), 3),
        "most_fragile": [(m, round(n / runs, 3)) for m, n in fails.most_common(5)],
        "most_relied_on_assets": reliance.most_common(5),
    }
