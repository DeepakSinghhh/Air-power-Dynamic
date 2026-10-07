# VAYU-SARTHI engine

Core decision engine: fused world state → threat-aware routing → CP-SAT allocation →
minimal-disruption retasking with explanations. All scenario data is notional.

```bash
cd engine
pip install -e ".[api,dev]"

python -m sarthi.demo                 # plan a 24 h day, then fog / pop-up SAM / MX alert / TST retasks
python -m sarthi.benchmark --seeds 20 # optimiser vs greedy manual-planner baseline
python -m pytest -q                   # 15 tests, incl. independent constraint validation + API flow
uvicorn sarthi.api:app --reload       # REST API under /api (docs at /docs); serves the UI if built
```

| Module | Purpose |
|---|---|
| `models.py` | Typed domain model (times are minutes from 00:00 D-day) |
| `scenario.py` | Seeded notional scenario generator |
| `threats.py` | SAM hazard field, intel-age inflation, SEAD suppression, Dijkstra routing |
| `fatigue.py` | Two-process crew effectiveness model → fit TOT windows |
| `candidates.py` | Feasibility screening, TOT domains, reason codes |
| `optimizer.py` | CP-SAT model (plan and retask modes) |
| `greedy.py` | Manual-planner baseline under the same constraints |
| `events.py`, `retask.py` | Operational events, retask, plan diff |
| `explain.py` | "Why wasn't this mission planned?" |
| `kpi.py` | KPIs and Monte Carlo stress test |
| `validate.py` | Independent constraint checker |
| `presets.py` | Context-aware demo events built from the current plan |
| `api.py` | FastAPI: state, plan, hazard grid, presets, propose / approve / reject retask |
| `data/india.json` | India outline (Natural Earth, India point of view) for scenario geography |

Retasking is human-in-the-loop: a proposal only takes effect when approved.

```bash
curl -X POST localhost:8000/api/scenario -H 'content-type: application/json' -d '{"seed": 7}'
curl -X POST 'localhost:8000/api/plan?time_limit=8'
curl 'localhost:8000/api/presets?at=120'                      # ready-made events for "now"
curl -X POST localhost:8000/api/retask/propose -H 'content-type: application/json' -d \
  '{"events":[{"kind":"base_closure","at":120,"base":"HLW","start":300,"end":570,"reason":"fog"}]}'
curl -X POST localhost:8000/api/retask/<id>/approve           # or /reject
```

Scenario geography is boundary-consistent: notional adversary sites are always at least 30 km outside
India's boundary, CAP stations are inside it, and CAS points are on the Indian side near the border.
Routes are any-angle: Dijkstra on the threat grid, then string pulling that keeps a straight leg only
where it costs no more (distance + threat exposure) than the grid path and avoids restricted airspace.

See `../docs/PLAN.md` for the full design and roadmap.
