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
from typing import Literal

import numpy as np
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import candidates, coa, copilot, greedy, met, optimizer, robust, whatif
from .readiness import readiness
from .events import Event
from .kpi import kpis, stress_test
from .models import Plan, World
from .presets import presets
from .retask import RetaskResult, diff_plans, retask
from .scenario import generate
from .scenario_hadr import generate_hadr
from .scenario_quake import generate_quake
from .threats import RiskField, effective_envelope

app = FastAPI(title="VAYU-SARTHI engine", version="0.2.0")
api = APIRouter(prefix="/api")
LOCK = threading.Lock()
STATE: dict = {"world": None, "plan": None, "baseline_kpis": None, "reference_label": "vs manual-style plan",
               "version": 0, "history": [],
               "proposal": None, "met_live": None, "coas": None, "hardened": None,
               "copilot_log": [], "copilot_ctx": {}}
LLM = copilot.LLMRouter.from_env()  # optional local model (SARTHI_LLM_URL); the copilot works without it
LIVE_MET_TTL_S = 1800


class ScenarioReq(BaseModel):
    seed: int = 7
    kind: Literal["conflict", "hadr", "quake"] = "conflict"  # western front | flood relief | earthquake relief


class ProposeReq(BaseModel):
    events: list[Event]
    time_limit: float = 10.0


class AskReq(BaseModel):
    text: str


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
        world = {"hadr": generate_hadr, "quake": generate_quake}.get(req.kind, generate)(req.seed)
        STATE.update(world=world, plan=None, baseline_kpis=None, history=[], proposal=None,
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
        _warm_llm(world, plan)
        return snapshot()


def _warm_llm(world: World, plan: Plan) -> None:
    """Prime a local model's prompt cache in the background, so the first real question is not the slow one."""
    if LLM is not None:
        threading.Thread(target=LLM.route, args=("status", world, plan), daemon=True).start()


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


def _coas(time_limit: float) -> dict:
    world, plan = _need_plan()
    t0 = time.perf_counter()
    results = coa.compare(world, plan, time_limit)
    STATE["coas"] = {"version": STATE["version"], "items": {c.id: (w, c) for w, c in results}}
    return {"version": STATE["version"], "now": world.now, "current_intent": world.intent,
            "seconds": round(time.perf_counter() - t0, 1),
            "coas": [c.model_dump(exclude={"plan"}) for _, c in results]}


@api.post("/coa")
def compare_coas(time_limit: float = 6.0) -> dict:
    """Plan the current situation under each commander's intent (solved in parallel)."""
    with LOCK:
        return _coas(time_limit)


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


@api.post("/whatif/{mission}")
def what_would_it_take(mission: str, time_limit: float = 3.0) -> dict:
    """Counterfactuals for an unplanned mission: single relaxations re-solved in parallel."""
    with LOCK:
        world, plan = _need_plan()
        if mission not in world.missions:
            raise HTTPException(404, f"Unknown mission {mission}")
        if mission in plan.assignments:
            raise HTTPException(409, f"{mission} is already planned")
        t0 = time.perf_counter()
        out = whatif.what_would_it_take(world, plan, mission, time_limit)
        return {"mission": mission, "version": STATE["version"], "seconds": round(time.perf_counter() - t0, 1),
                "outcomes": out}


@api.get("/readiness")
def get_readiness(at: int | None = None, proposal: bool = False) -> dict:
    """Per-base readiness at `at` (default now) and data-feed freshness; proposal=true uses the pending proposal."""
    with LOCK:
        prop = STATE["proposal"]
        if proposal and prop:
            world, plan = prop["result"].world, prop["result"].plan
        else:
            world, plan = _ensure_world(), STATE["plan"]
        return readiness(world, plan, world.now if at is None else at)


@api.get("/presets")
def get_presets(at: int = 0) -> list:
    with LOCK:
        world, plan = _need_plan()
        return presets(world, plan, at)


def _propose(events: list, time_limit: float = 10.0) -> dict:
    world, plan = _need_plan()
    res: RetaskResult = retask(world, plan, events, time_limit, compare_naive=True)
    pid = uuid.uuid4().hex[:8]
    STATE["proposal"] = {"id": pid, "version": STATE["version"], "result": res}
    return {"id": pid, "notes": res.notes, "diff": res.diff, "naive_diff": res.naive_diff,
            "world": res.world, "plan": res.plan, "kpis": kpis(res.world, res.plan),
            "envelopes": envelopes(res.world)}


@api.post("/retask/propose")
def propose(req: ProposeReq) -> dict:
    with LOCK:
        return _propose(req.events, req.time_limit)


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


def _robustness(runs: int) -> dict:
    world, plan = _need_plan()
    out = {"version": STATE["version"], "now": world.now, "spare_policy": world.spare_policy,
           "current": stress_test(world, plan, runs), "hardened": None}
    if not world.spare_policy:
        w = world.model_copy(deep=True)
        w.spare_policy = True
        hp = robust.add_spares(w, plan, candidates.build(w))
        STATE["hardened"] = {"version": STATE["version"], "world": w, "plan": hp}
        out["hardened"] = stress_test(w, hp, runs)
    return out


@api.get("/robustness")
def robustness(runs: int = 2000) -> dict:
    """Monte Carlo execution of the committed plan and, if spares are not yet held, of the same plan
    with ground spares (nothing else changed)."""
    with LOCK:
        return _robustness(runs)


def _propose_spares(compute: bool = False) -> dict:
    world, plan = _need_plan()
    h = STATE["hardened"]
    if compute and (not h or h["version"] != STATE["version"]):
        _robustness(500)
        h = STATE["hardened"]
    if not h:
        raise HTTPException(404, "Run the robustness check first")
    if h["version"] != STATE["version"]:
        raise HTTPException(409, "Plan changed since the robustness check; run it again")
    w, hp = h["world"], h["plan"]
    n = sum(len(a.spares) for a in hp.assignments.values())
    k = sum(1 for a in hp.assignments.values() if a.spares)
    notes = [f"Hold {n} idle aircraft as ground spares for {k} missions. No mission, flying aircraft, crew "
             f"or TOT changes. Spares are loaded and booked for the sortie, and stay assigned through later "
             f"retasks."]
    res = RetaskResult(world=w, plan=hp, diff=diff_plans(w, plan, hp), notes=notes)
    pid = uuid.uuid4().hex[:8]
    STATE["proposal"] = {"id": pid, "version": STATE["version"], "result": res}
    return {"id": pid, "notes": notes, "diff": res.diff, "naive_diff": None, "world": w, "plan": hp,
            "kpis": kpis(w, hp), "envelopes": envelopes(w)}


@api.post("/robustness/propose")
def propose_spares() -> dict:
    """Hold idle aircraft as ground spares: a proposal like any retask, approved by a human."""
    with LOCK:
        return _propose_spares()


# ---------- copilot ----------

class _Services:
    """What the copilot may do: read, simulate, and create proposals (a human approves or rejects them)."""
    def propose(self, events: list, label: str) -> dict:
        return _propose(events)

    def propose_spares(self) -> dict:
        return _propose_spares(compute=True)

    def robustness(self) -> dict:
        return _robustness(2000)

    def coas(self) -> dict:
        return _coas(6.0)

    def pending(self) -> bool:
        return STATE["proposal"] is not None


@api.get("/copilot/status")
def copilot_status() -> dict:
    """Which router answers: the deterministic parser always; a local model only if one is configured."""
    info = {"llm": LLM.describe() if LLM else None, "reachable": None, "tools": copilot.TOOLS}
    if LLM:
        try:
            info["reachable"] = LLM.ping()
        except OSError:
            info["reachable"] = False
    return info


@api.post("/copilot")
def ask_copilot(req: AskReq) -> dict:
    with LOCK:
        world, plan = _need_plan()
        t0 = time.perf_counter()
        r = copilot.answer(req.text[:500], world, plan, _Services(), STATE["copilot_ctx"], LLM)
        STATE["copilot_log"].append({"wall": time.strftime("%Y-%m-%d %H:%M:%S"), "now": world.now,
                                     "text": req.text[:500], "router": r.router, "intent": r.intent,
                                     "tools": r.tools, "proposal": r.proposal["id"] if r.proposal else None,
                                     "seconds": round(time.perf_counter() - t0, 2)})
        out = r.model_dump()
        out["seconds"] = round(time.perf_counter() - t0, 2)
        return out


@api.get("/copilot/log")
def copilot_log() -> list:
    """Audit trail: every question, how it was routed, which engine tools ran, any proposal it created."""
    return STATE["copilot_log"]


app.include_router(api)

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")
