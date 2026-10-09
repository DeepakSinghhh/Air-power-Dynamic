# VAYU-SARTHI engine

Core decision engine: fused world state → threat-aware routing → CP-SAT allocation →
minimal-disruption retasking with explanations. All scenario data is notional.

```bash
cd engine
pip install -e ".[api,dev]"

python -m sarthi.demo                 # plan a 24 h day, then fog / pop-up SAM / MX alert / TST retasks
python -m sarthi.benchmark --seeds 20 # optimiser vs greedy manual-planner baseline
python -m sarthi.benchmark --coa      # the three courses of action, side by side
python -m sarthi.benchmark --hadr     # flood-relief scenario: optimiser vs greedy, incl. relief tonnage
python -m sarthi.benchmark --quake    # earthquake-relief scenario (Himalaya): the same comparison
python -m sarthi.copilot_eval         # copilot routing accuracy (add --url/--model/--api for a local model)
python -m pytest -q                   # 55 tests, incl. constraint validation, API flow, fog model, COAs, spares, HADR
uvicorn sarthi.api:app --reload       # REST API under /api (docs at /docs); serves the UI if built
```

| Module | Purpose |
|---|---|
| `models.py` | Typed domain model (times are minutes from 00:00 D-day), including commander's intent |
| `scenario.py` | Seeded notional scenario generator (western front) |
| `scenario_hadr.py` | Seeded notional flood-relief scenario (Assam and Bihar): NDRF lift, helicopter rescue and relief drops |
| `scenario_quake.py` | Seeded notional earthquake-relief scenario (Garhwal Himalaya): damaged runways, thin air, landing ceilings |
| `threats.py` | SAM hazard field, intel-age inflation, SEAD suppression, Dijkstra routing |
| `fatigue.py` | Two-process crew effectiveness model → fit TOT windows |
| `candidates.py` | Feasibility screening, TOT domains, reason codes |
| `optimizer.py` | CP-SAT model (plan and retask modes) |
| `greedy.py` | Manual-planner baseline under the same constraints |
| `events.py`, `retask.py` | Operational events, retask, plan diff |
| `explain.py` | "Why wasn't this mission planned?" |
| `whatif.py` | "What would it take?": single relaxations re-solved in parallel, each with its cost |
| `readiness.py` | Readiness board: per-base aircraft, crews fit per hour, weapons left, closures; data-feed freshness |
| `copilot.py` | Copilot: deterministic parser + optional local LLM router → engine tools → templated answers |
| `kpi.py` | KPIs, COA trade-off metrics |
| `robust.py` | Mission success model, Monte Carlo execution, single points of failure, ground spares |
| `coa.py` | Courses of action: the same situation planned under three commander's intents, in parallel |
| `validate.py` | Independent constraint checker |
| `presets.py` | Context-aware demo events built from the current plan |
| `met.py` | Fog forecasting: MOS logistic model on Open-Meteo NWP → P(vis < 1 km) per base-hour → closure events |
| `data/met/` | Trained fog model (with held-out skill scores) and a cached real dense-fog night |
| `api.py` | FastAPI: state, plan, hazard grid, fog forecast, presets, propose / approve / reject retask, COAs, robustness |
| `data/india.json` | India outline (Natural Earth, India point of view) for scenario geography |

Retasking is human-in-the-loop: a proposal only takes effect when approved.

```bash
curl -X POST localhost:8000/api/scenario -H 'content-type: application/json' -d '{"seed": 7}'
curl -X POST localhost:8000/api/scenario -H 'content-type: application/json' -d '{"seed": 7, "kind": "hadr"}'   # or "quake"
curl -X POST 'localhost:8000/api/plan?time_limit=8'
curl 'localhost:8000/api/presets?at=120'                      # ready-made events for "now"
curl -X POST localhost:8000/api/retask/propose -H 'content-type: application/json' -d \
  '{"events":[{"kind":"base_closure","at":120,"base":"HLW","start":300,"end":570,"reason":"fog"}]}'
curl -X POST localhost:8000/api/retask/<id>/approve           # or /reject
curl -X POST 'localhost:8000/api/coa?time_limit=6'             # three COAs for the current situation
curl -X POST localhost:8000/api/coa/risk/propose               # adopt one as a proposal, then approve it
curl -X POST localhost:8000/api/whatif/STK-06                  # what would get an unplanned mission planned?
curl 'localhost:8000/api/readiness?at=360'                     # readiness board at 06:00 + data-feed freshness
curl -X POST localhost:8000/api/copilot -H 'content-type: application/json' -d '{"text":"why is STK-06 not planned?"}'
curl localhost:8000/api/copilot/log                            # audit trail of every copilot question
curl 'localhost:8000/api/robustness?runs=2000'                 # Monte Carlo: current plan vs with ground spares
curl -X POST localhost:8000/api/robustness/propose             # hold ground spares (a proposal, then approve)
```

Scenario geography is boundary-consistent: notional adversary sites are always at least 30 km outside
India's boundary, CAP stations are inside it, and CAS points are on the Indian side near the border.
Routes are any-angle: Dijkstra on the threat grid, then string pulling that keeps a straight leg only
where it costs no more (distance + threat exposure) than the grid path and avoids restricted airspace.

Courses of action: commander's intent (`World.intent`) is part of the world state, so constraints, the
validator and explanations all apply it. *Max effect* uses the tasked risk ceilings. *Min risk* scales every
ceiling by 0.6 and prices each expected aircraft loss. *Defensive posture* defers strike/SEAD below P7, holds
60% of each base's fighters for air defence and prices guided weapons. Each COA is solved as a churn-penalised
retask from the current plan (launched missions frozen). Once a COA is adopted and approved, its intent persists.

Robustness: one success model gives both the *Expected value* KPI and the Monte Carlo spread. A mission succeeds
if every slot launches with a serviceable aircraft (a ground spare can replace a U/S primary), every aircraft reaches
the target (the ingress half of the two-way route risk), enough tankers turn up, and, for a strike, its SEAD
succeeded. Ground spares are a greedy, purely additive post-pass over idle aircraft: same base and type as a package
element, booked for the sortie, loaded from stock, and off the alert reserve while standing by. Once approved, they are
re-chosen after every retask, and a spare is the cheapest substitute when a primary goes unserviceable.

HADR (flood relief): the same engine with a different mission set. Fixed-wing transports lift NDRF teams and stores
to forward airfields; the runway length decides which types can land (`Mission.runway_m`, `AircraftType.min_runway_m`;
0 means helicopters only). Helicopters fly rescue, medical and relief-drop sorties to marooned clusters, and UAVs map the
flood. `World.domestic_only` masks the routing grid to India's boundary, so flights use the Siliguri corridor and never
overfly neighbouring countries. Thunderstorm cells are restricted zones, and a `new_zone` event adds one. HADR presets:
embankment breach (new P10 rescue), heavy rain at the busiest airfield, a thunderstorm cell on a route, helicopters
unserviceable, a road convoy cancelled.

HADR (earthquake, Garhwal Himalaya): `World.disaster = "earthquake"`. `Base.runway_m` is the usable runway after damage
and limits take-offs as well as landings; a `runway_damage` event shortens it (and the landings of missions whose
`Mission.airfield` it is). Helicopter payload falls with landing-site elevation (`AircraftType.altitude_derate`,
`payload_at(elevation_m)`), and `max_landing_m` is the highest site a type can use. The candidate screen, the optimiser
(through the per-pair payload) and the validator all apply them. Presets: an aftershock that cuts Jolly Grant to 900 m,
a landslide rescue at altitude, low cloud on a route, helicopters unserviceable, a field hospital for a valley landing
ground. "Why not?" says when a load cannot be lifted at all ("Not enough lift: … Screened out: runway too short") and
how thin air cuts each helicopter's lift.

Copilot: `POST /api/copilot {"text": ...}` answers from the engine only. A deterministic parser routes most questions
with no model. An optional local open-weight model routes the rest to ONE of 18 tools, under a JSON schema whose enums
are the scenario's real IDs. The parser's IDs and times override the model's. The answer is always a template filled
by the engine. Proposals go through approve/reject, and every question is logged. To add a local model (optional):

```bash
# Ollama (easiest on Windows/macOS/Linux):  ollama pull qwen2.5:3b
SARTHI_LLM_URL=http://127.0.0.1:11434 SARTHI_LLM_MODEL=qwen2.5:3b uvicorn sarthi.api:app
# or any OpenAI-compatible server, e.g. llama.cpp:  llama-server -m qwen2.5-1.5b-instruct-q4_k_m.gguf --port 8080
SARTHI_LLM_URL=http://127.0.0.1:8080/v1 SARTHI_LLM_API=openai SARTHI_LLM_MODEL=local uvicorn sarthi.api:app
```

Tested with Qwen2.5-1.5B-Instruct (Q4_K_M, llama.cpp, 4 CPU cores). The model alone routes 18/20 questions at about 5 s
each; parser first, then model, routes 20/20. The Ollama protocol is unit-tested but has not been run against a live
Ollama here.

Fog forecasting: `GET /api/met?source=snapshot|live&threshold=0.5` returns per-base hourly P(fog) and
the closures it implies. Retrain with `python -I tools/build_fog_model.py <cache-dir>`. It labels with IEM METAR
archives and uses Open-Meteo historical forecasts as features. It fits on two winters, recalibrates on a third and
tests on the latest. Held-out skill: Brier skill +44% vs climatology, AUC 0.91. Downloads are cached and retried,
because Open-Meteo's free tier is rate-limited per IP.

See `../docs/PLAN.md` for the full design and roadmap.
