# VAYU-SARTHI engine

Core decision engine: fused world state → threat-aware routing → CP-SAT allocation →
minimal-disruption retasking with explanations. All scenario data is notional.

```bash
cd engine
pip install -e ".[api,dev]"

python -m sarthi.demo                 # plan a 24 h day, then fog / pop-up SAM / MX alert / TST retasks
python -m sarthi.benchmark --seeds 20 # optimiser vs greedy manual-planner baseline
python -m pytest -q                   # 11 tests, incl. independent constraint validation
uvicorn sarthi.api:app --reload       # REST API, docs at http://127.0.0.1:8000/docs
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
| `api.py` | FastAPI wrapper |

Example API retask call:

```bash
curl -X POST localhost:8000/scenario -H 'content-type: application/json' -d '{"seed": 7}'
curl -X POST 'localhost:8000/plan?time_limit=8'
curl -X POST localhost:8000/retask -H 'content-type: application/json' -d \
  '{"events":[{"kind":"base_closure","at":120,"base":"HLW","start":300,"end":570,"reason":"fog"}]}'
```

See `../docs/PLAN.md` for the full design and roadmap.
