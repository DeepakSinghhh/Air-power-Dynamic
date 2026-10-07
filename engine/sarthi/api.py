"""Thin HTTP API over the engine for the frontend (single in-memory session).

    uvicorn sarthi.api:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import candidates, greedy, optimizer
from .events import Event
from .kpi import kpis, stress_test
from .models import Plan, World
from .retask import retask
from .scenario import generate

app = FastAPI(title="VAYU-SARTHI engine", version="0.1.0")
STATE: dict = {"world": None, "plan": None, "history": []}


class ScenarioReq(BaseModel):
    seed: int = 7


class RetaskReq(BaseModel):
    events: list[Event]
    time_limit: float = 10.0


def _need_plan() -> tuple[World, Plan]:
    if STATE["plan"] is None:
        raise HTTPException(409, "No plan yet: POST /scenario then POST /plan")
    return STATE["world"], STATE["plan"]


@app.post("/scenario")
def new_scenario(req: ScenarioReq) -> World:
    STATE.update(world=generate(req.seed), plan=None, history=[])
    return STATE["world"]


@app.get("/world")
def get_world() -> World:
    if STATE["world"] is None:
        raise HTTPException(409, "No scenario: POST /scenario first")
    return STATE["world"]


@app.post("/plan")
def make_plan(time_limit: float = 10.0) -> dict:
    world = STATE["world"] or generate()
    cands = candidates.build(world)
    base = greedy.solve(world, cands)
    plan = optimizer.solve(world, cands, hint=base, time_limit=time_limit)
    STATE.update(world=world, plan=plan)
    return {"plan": plan, "kpis": kpis(world, plan), "baseline_kpis": kpis(world, base)}


@app.get("/plan")
def get_plan() -> dict:
    world, plan = _need_plan()
    return {"plan": plan, "kpis": kpis(world, plan)}


@app.post("/retask")
def do_retask(req: RetaskReq) -> dict:
    world, plan = _need_plan()
    res = retask(world, plan, req.events, req.time_limit, compare_naive=True)
    STATE["history"].append({"notes": res.notes, "diff": res.diff})
    STATE.update(world=res.world, plan=res.plan)
    return {"notes": res.notes, "diff": res.diff, "naive_diff": res.naive_diff,
            "plan": res.plan, "kpis": kpis(res.world, res.plan)}


@app.get("/stress")
def stress(runs: int = 2000) -> dict:
    world, plan = _need_plan()
    return stress_test(world, plan, runs)


@app.get("/history")
def history() -> list:
    return STATE["history"]
