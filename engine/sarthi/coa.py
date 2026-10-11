"""Courses of action: the same situation planned under different commander's intents.

Each COA is the least-disruptive realisation of its intent from the current state (launched
missions stay frozen, churn is penalised), so "changes vs current plan" is part of its cost.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from . import optimizer
from .candidates import build
from .kpi import kpis, plan_metrics, stress_test
from .models import Intent, Plan, World
from .retask import PlanDiff, diff_plans

INTENTS: dict[str, tuple[Intent, str]] = {
    "effect": (Intent(name="Max effect"),
               "Fly every mission the tasked risk ceilings allow; maximise priority-weighted effect."),
    "risk": (Intent(name="Min risk", risk_scale=0.6, loss_weight=4.0),
             "Tighten every risk ceiling to 60% and price each expected aircraft loss at 4 priority points."),
    "defend": (Intent(name="Defensive posture", offensive_floor=7, reserve_fraction=0.6, munitions_weight=150),
               "Defer strikes and SEAD below P7, hold 60% of every base's fighters on the ground for air defence, "
               "conserve guided weapons."),
}


class Coa(BaseModel):
    id: str
    name: str
    description: str
    intent: Intent
    plan: Plan
    kpis: dict
    metrics: dict
    robustness_p05: float
    diff: PlanDiff


def solve_coa(world: World, plan: Plan, coa_id: str, time_limit: float, workers: int) -> tuple[World, Coa]:
    intent, text = INTENTS[coa_id]
    w = world.model_copy(deep=True)
    w.intent = intent.model_copy()
    p = optimizer.solve(w, build(w), baseline=plan, time_limit=time_limit, workers=workers)
    st = stress_test(w, p, runs=1000)
    return w, Coa(id=coa_id, name=intent.name, description=text, intent=w.intent, plan=p, kpis=kpis(w, p),
                  metrics=plan_metrics(w, p), robustness_p05=st["p05"], diff=diff_plans(w, plan, p))


def compare(world: World, plan: Plan, time_limit: float = 6.0) -> list[tuple[World, Coa]]:
    """Solve every COA in parallel threads (CP-SAT releases the GIL); workers split across CPUs."""
    workers = min(max(2, (os.cpu_count() or 4) // len(INTENTS) + 1), optimizer.DEFAULT_WORKERS)
    # One at a time on a fractional CPU, so each solve gets the whole allowance.
    with ThreadPoolExecutor(max_workers=len(INTENTS) if optimizer.CPUS >= 2 else 1) as pool:
        futs = [pool.submit(solve_coa, world, plan, cid, time_limit, workers) for cid in INTENTS]
        return [f.result() for f in futs]
