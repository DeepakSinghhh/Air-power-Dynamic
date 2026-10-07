"""Optimiser vs greedy "manual planner" baseline across many random scenarios.

    python -m sarthi.benchmark [--seeds 20] [--time-limit 10]
"""
from __future__ import annotations

import argparse
import statistics as st

from . import candidates, greedy, optimizer
from .kpi import kpis
from .scenario import generate
from .validate import validate


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--time-limit", type=float, default=10.0)
    ap.add_argument("--strikes", type=int, default=14, help="strike missions per scenario (load)")
    args = ap.parse_args()

    rows = []
    print(f"{'seed':>4} {'missions':>8} | {'greedy fulfil':>13} {'EV':>5} | {'sarthi fulfil':>13} {'EV':>5} {'t(s)':>6}")
    for seed in range(1, args.seeds + 1):
        w = generate(seed, n_strike=args.strikes)
        c = candidates.build(w)
        g = greedy.solve(w, c)
        p = optimizer.solve(w, c, hint=g, time_limit=args.time_limit)
        errs = validate(w, g, c) + validate(w, p, c)
        if errs:
            raise SystemExit(f"constraint violation on seed {seed}: {errs[:5]}")
        kg, kp = kpis(w, g), kpis(w, p)
        rows.append((kg, kp))
        print(f"{seed:>4} {len(w.missions):>8} | {kg['priority_weighted_fulfilment']:>13.0%} "
              f"{kg['expected_value']:>5.0%} | {kp['priority_weighted_fulfilment']:>13.0%} "
              f"{kp['expected_value']:>5.0%} {kp['solve_seconds']:>6.1f}")

    def mean(side, key):
        return st.mean(r[side][key] for r in rows)

    print("-" * 64)
    for key in ("priority_weighted_fulfilment", "expected_value", "mean_sortie_risk"):
        g, p = mean(0, key), mean(1, key)
        print(f"{key:<30} greedy {g:6.1%}   sarthi {p:6.1%}   delta {p - g:+.1%}")
    print(f"{'solve_seconds (mean)':<30} greedy {mean(0, 'solve_seconds'):6.3f}   "
          f"sarthi {mean(1, 'solve_seconds'):6.2f}")


if __name__ == "__main__":
    main()
