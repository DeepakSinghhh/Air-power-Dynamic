"""HTTP API over the engine, and the static frontend when it has been built.

    uvicorn sarthi.api:app --reload        # API at /api, UI at / (after `npm run build`)

Single in-memory session. Retasking is human-in-the-loop: /retask/propose returns
a proposal (diff + naive comparison) that only takes effect on /approve.
"""
from __future__ import annotations

import base64
import threading
import time
import uuid
from pathlib import Path

import numpy as np
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import candidates, coa, greedy, met, optimizer
from .events import Event
from .kpi import kpis, stress_test
from .models import Plan, World
from .presets import presets
from .retask import RetaskResult, retask
from .scenario import generate
from .threats import RiskField, effective_envelope

app = FastAPI(title="VAYU-SARTHI engine", version="0.2.0")
api = APIRouter(prefix="/api")
LOCK = threading.Lock()
STATE: dict = {"world": None, "plan": None, "baseline_kpis": None, "reference_label": "vs manual-style plan",
               "version": 0, "history": [],
               "proposal": None, "met_live": None, "coas": None}
LIVE_MET_TTL_S = 1800


class ScenarioReq(BaseModel):
    seed: int = 7


class ProposeReq(BaseModel):
    events: list[Event]
    time_limit: float = 10.0


def envelopes(world: World) -> dict:
    out = {}
    for t in world.threats.values():
        r_eff, _ = effective_envelope(t, world.now)
        out[t.id] = {"r_eff_km": round(r_eff, 1), "age_min": max(0, world.now - t.observed_at)}
    return out


def snapshot() -> dict:
    w, p = STATE["world"], STATE["plan"]
    return {"world": w, "plan": p, "version": STATE["version"],
            "kpis": kpis(w, p) if w and p else None, "baseline_kpis": STATE["baseline_kpis"],
            "reference_label": STATE["reference_label"],
            "envelopes": envelopes(w) if w else {}, "history": STATE["history"]}


def _ensure_world() -> World:
    if STATE["world"] is None:
        STATE["world"] = generate()
    return STATE["world"]


def _need_plan() -> tuple[World, Plan]:
    if STATE["plan"] is None:
        raise HTTPException(409, "No plan yet: POST /api/plan first")
    return STATE["world"], STATE["plan"]


@api.get("/state")
def get_state() -> dict:
    with LOCK:
        _ensure_world()
        return snapshot()


@api.post("/scenario")
def new_scenario(req: ScenarioReq) -> dict:
    with LOCK:
        STATE.update(world=generate(req.seed), plan=None, baseline_kpis=None, history=[], proposal=None,
                     version=STATE["version"] + 1)
        return snapshot()


@api.post("/plan")
def make_plan(time_limit: float = 10.0) -> dict:
    with LOCK:
        world = _ensure_world()
        cands = candidates.build(world)
        base = greedy.solve(world, cands)
        plan = optimizer.solve(world, cands, hint=base, time_limit=time_limit)
        STATE.update(plan=plan, baseline_kpis=kpis(world, base), reference_label="vs manual-style plan",
                     proposal=None, version=STATE["version"] + 1)
        return snapshot()


@api.get("/hazard")
def hazard(proposal: bool = False) -> dict:
    """Threat hazard surface (per-km kill rate), quantised to 0-255, rows south to north.

    With proposal=true, uses the pending proposal's world (e.g. showing a pop-up SAM before approval).
    """
    with LOCK:
        prop = STATE["proposal"]
        world = prop["result"].world if proposal and prop else _ensure_world()
        f = RiskField(world)
    h = f.hazard
    hmax = float(h.max()) or 1.0
    q = np.round(np.sqrt(h / hmax) * 255).astype(np.uint8)  # sqrt keeps faint envelope edges visible
    return {"lat0": f.lat0, "lon0": f.lon0, "res": f.res, "nlat": f.nlat, "nlon": f.nlon, "max": hmax,
            "scale": "sqrt", "data": base64.b64encode(q.tobytes()).decode()}


@api.get("/met")
def met_forecast(source: str = "snapshot", threshold: float = 0.5, at: int | None = None) -> dict:
    """Fog forecast per base (MOS on Open-Meteo NWP) and the closures it implies at `threshold`.

    source=snapshot uses the cached dense-fog night (works offline); source=live calls Open-Meteo.
    """
    with LOCK:
        world = _ensure_world().model_copy(deep=True)
    at = max(world.now if at is None else at, world.now)
    if source == "live":
        cached = STATE["met_live"]
        if cached and time.time() - cached[0] < LIVE_MET_TTL_S:
            fc = cached[1]
        else:
            try:
                fc = met.live_forecast(world)
            except RuntimeError as e:
                raise HTTPException(503, f"{e}. Use the cached snapshot (source=snapshot).")
            STATE["met_live"] = (time.time(), fc)
    else:
        fc = met.snapshot_forecast(world)
    windows = met.fog_windows(fc, threshold, after=at)
    return {"forecast": fc, "threshold": threshold, "at": at, "windows": windows,
            "events": met.closure_events(world, windows, at)}


@api.post("/coa")
def compare_coas(time_limit: float = 6.0) -> dict:
    """Plan the current situation under each commander's intent (solved in parallel)."""
    with LOCK:
        world, plan = _need_plan()
        t0 = time.perf_counter()
        results = coa.compare(world, plan, time_limit)
        STATE["coas"] = {"version": STATE["version"], "items": {c.id: (w, c) for w, c in results}}
        return {"version": STATE["version"], "now": world.now, "current_intent": world.intent,
                "seconds": round(time.perf_counter() - t0, 1),
                "coas": [c.model_dump(exclude={"plan"}) for _, c in results]}


@api.post("/coa/{coa_id}/propose")
def propose_coa(coa_id: str) -> dict:
    """Turn a computed COA into a pending proposal (reviewed and approved like any retask)."""
    with LOCK:
        world, plan = _need_plan()
        store = STATE["coas"]
        if not store or coa_id not in store["items"]:
            raise HTTPException(404, "Compute courses of action first")
        if store["version"] != STATE["version"]:
            raise HTTPException(409, "Plan changed since the COAs were computed; compute them again")
        w, c = store["items"][coa_id]
        notes = [f"Commander's intent: {c.name}. {c.description}"]
        res = RetaskResult(world=w, plan=c.plan, diff=c.diff, notes=notes)
        pid = uuid.uuid4().hex[:8]
        STATE["proposal"] = {"id": pid, "version": STATE["version"], "result": res}
        return {"id": pid, "notes": notes, "diff": c.diff, "naive_diff": None, "world": w, "plan": c.plan,
                "kpis": c.kpis, "envelopes": envelopes(w)}


@api.get("/presets")
def get_presets(at: int = 0) -> list:
    with LOCK:
        world, plan = _need_plan()
        return presets(world, plan, at)


@api.post("/retask/propose")
def propose(req: ProposeReq) -> dict:
    with LOCK:
        world, plan = _need_plan()
        res: RetaskResult = retask(world, plan, req.events, req.time_limit, compare_naive=True)
        pid = uuid.uuid4().hex[:8]
        STATE["proposal"] = {"id": pid, "version": STATE["version"], "result": res}
        return {"id": pid, "notes": res.notes, "diff": res.diff, "naive_diff": res.naive_diff,
                "world": res.world, "plan": res.plan, "kpis": kpis(res.world, res.plan),
                "envelopes": envelopes(res.world)}


@api.post("/retask/{pid}/approve")
def approve(pid: str) -> dict:
    with LOCK:
        prop = STATE["proposal"]
        if not prop or prop["id"] != pid:
            raise HTTPException(404, "No such pending proposal")
        if prop["version"] != STATE["version"]:
            raise HTTPException(409, "Plan changed since this proposal was made; propose again")
        res: RetaskResult = prop["result"]
        STATE["history"].append({"id": pid, "notes": res.notes, "now": res.world.now,
                                 "aircraft_changes": res.diff.aircraft_changes,
                                 "naive_aircraft_changes": res.naive_diff.aircraft_changes if res.naive_diff else None,
                                 "decision": "approved"})
        # The manual-style baseline never faced this event; compare with the plan before the change instead.
        before = kpis(STATE["world"], STATE["plan"])
        STATE.update(world=res.world, plan=res.plan, proposal=None, version=STATE["version"] + 1,
                     baseline_kpis=before, reference_label="vs before last retask")
        return snapshot()


@api.post("/retask/{pid}/reject")
def reject(pid: str) -> dict:
    with LOCK:
        prop = STATE["proposal"]
        if not prop or prop["id"] != pid:
            raise HTTPException(404, "No such pending proposal")
        STATE["history"].append({"id": pid, "notes": prop["result"].notes, "now": prop["result"].world.now,
                                 "decision": "rejected"})
        STATE["proposal"] = None
        return snapshot()


@api.get("/stress")
def stress(runs: int = 2000) -> dict:
    with LOCK:
        world, plan = _need_plan()
        return stress_test(world, plan, runs)


app.include_router(api)

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")
