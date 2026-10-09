"""Routing accuracy of the copilot on paraphrased questions.

    python -m sarthi.copilot_eval                                   # parser only (no model)
    python -m sarthi.copilot_eval --url http://127.0.0.1:11434 --model qwen2.5:3b              # Ollama
    python -m sarthi.copilot_eval --url http://127.0.0.1:8080/v1 --model local --api openai    # llama.cpp

Each case is a question a planner might type and the tool and arguments it should reach. Reports the parser
alone, the model alone (if configured) and the full pipeline (parser first, model for what it cannot place).
"""
from __future__ import annotations

import argparse
import time

from . import candidates, greedy, optimizer
from .copilot import LLMRouter, route, rules
from .scenario import generate

CASES = [
    ("STK-06 still isn't on the schedule, what's blocking it?", {"intent": "why_not", "mission": "STK-06"}),
    ("can we squeeze ISR-01 in somehow?", {"intent": "what_would_it_take", "mission": "ISR-01"}),
    ("give me the big picture", {"intent": "status"}),
    ("walk me through the STK-01 package", {"intent": "brief", "mission": "STK-01"}),
    ("Halwara gets socked in by fog between 0500 and 0930, how does the plan cope?",
     {"intent": "close_base", "base": "HLW", "start": 300, "end": 570}),
    ("Ambala runway was just bombed", {"intent": "close_base", "base": "AMB"}),
    ("HLW-SU30-02 has a hydraulic leak and is grounded", {"intent": "aircraft_down", "tail": "HLW-SU30-02"}),
    ("what's on HLW-SU30-09's schedule today?", {"intent": "aircraft", "tail": "HLW-SU30-09"}),
    ("which of our routes go near SAM-LR-1?", {"intent": "threat", "threat": "SAM-LR-1"}),
    ("are we running low on munitions anywhere?", {"intent": "readiness"}),
    ("what's the state of Jodhpur?", {"intent": "readiness", "base": "JDH"}),
    ("is the fog going to be a problem tonight?", {"intent": "fog"}),
    ("what happens to the plan on a bad day?", {"intent": "robustness"}),
    ("show me safer alternatives to this plan", {"intent": "coas"}),
    ("make STK-06 top priority", {"intent": "raise_priority", "mission": "STK-06"}),
    ("scrub the STK-10 mission", {"intent": "cancel", "mission": "STK-10"}),
    ("shut the fogged bases", {"intent": "fog_closures"}),
    ("put some reserve aircraft on standby for the packages", {"intent": "spares"}),
    ("which missions didn't make the cut?", {"intent": "unplanned"}),
    ("what's the capital of France?", {"intent": "help"}),
]


def _ok(a, exp: dict) -> bool:
    return a is not None and all(getattr(a, k) == v for k, v in exp.items())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--model", default="qwen2.5:3b")
    ap.add_argument("--api", default="ollama", choices=["ollama", "openai"])
    args = ap.parse_args()
    w = generate(7)
    c = candidates.build(w)
    p = optimizer.solve(w, c, hint=greedy.solve(w, c), time_limit=6)
    llm = LLMRouter(args.url, args.model, args.api, timeout=120) if args.url else None
    n = len(CASES)
    parser_ok = sum(_ok(rules(q, w, p, {}), e) for q, e in CASES)
    print(f"parser alone: {parser_ok}/{n}")
    if llm is None:
        return
    llm.route("status", w, p)  # warm the model's prompt cache
    model_ok, times = 0, []
    for q, e in CASES:
        t0 = time.perf_counter()
        a = llm.route(q, w, p)
        times.append(time.perf_counter() - t0)
        model_ok += _ok(a, e)
        print(f"  {'ok  ' if _ok(a, e) else 'MISS'} {times[-1]:4.1f}s  {q}")
    full = sum(_ok(route(q, w, p, {}, llm)[0], e) for q, e in CASES)
    print(f"model alone: {model_ok}/{n} ({sum(times) / n:.1f} s per question); "
          f"full pipeline (parser, then model): {full}/{n}")


if __name__ == "__main__":
    main()
