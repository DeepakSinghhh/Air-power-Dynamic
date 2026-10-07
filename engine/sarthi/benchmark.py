"""Optimiser vs greedy "manual planner" baseline across many random scenarios.

    python -m sarthi.benchmark [--seeds 20] [--time-limit 10]
    python -m sarthi.benchmark --coa [--seeds 8]     # the three courses of action, side by side
"""
from __future__ import annotations

import argparse
import statistics as st
import time

from . import candidates, coa, greedy, optimizer
from .kpi import kpis
from .scenario import generate
from .scenario_hadr import generate_hadr
from .validate import validate


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--time-limit", type=float, default=10.0)
    ap.add_argument("--strikes", type=int, default=14, help="strike missions per scenario (load)")
    ap.add_argument("--coa", action="store_true", help="compare the courses of action instead")
    ap.add_argument("--hadr", action="store_true", help="flood-relief scenario instead of the conflict one")
    args = ap.parse_args()
    if args.coa:
        return coa_benchmark(args)

    rows = []
    print(f"{'seed':>4} {'missions':>8} | {'greedy fulfil':>13} {'EV':>5} | {'sarthi fulfil':>13} {'EV':>5} {'t(s)':>6}")
    for seed in range(1, args.seeds + 1):
        w = generate_hadr(seed) if args.hadr else generate(seed, n_strike=args.strikes)
        c = candidates.build(w)
        g = greedy.solve(w, c)
        p = optimizer.solve(w, c, hint=g, time_limit=args.time_limit)
        errs = validate(w, g, c) + validate(w, p, c)
        if errs:
            raise SystemExit(f"constraint violation on seed {seed}: {errs[:5]}")
        kg, kp = kpis(w, g), kpis(w, p)
        if args.hadr:
            kg["relief_t"], kp["relief_t"] = (sum(w.missions[m].cargo_t for m in x.assignments) /
                                              sum(m.cargo_t for m in w.missions.values()) for x in (g, p))
        rows.append((kg, kp))
        print(f"{seed:>4} {len(w.missions):>8} | {kg['priority_weighted_fulfilment']:>13.0%} "
              f"{kg['expected_value']:>5.0%} | {kp['priority_weighted_fulfilment']:>13.0%} "
              f"{kp['expected_value']:>5.0%} {kp['solve_seconds']:>6.1f}")

    def mean(side, key):
        return st.mean(r[side][key] for r in rows)

    print("-" * 64)
    for key in ("priority_weighted_fulfilment", "expected_value", "mean_sortie_risk") + (("relief_t",) if args.hadr else ()):
        g, p = mean(0, key), mean(1, key)
        print(f"{key:<30} greedy {g:6.1%}   sarthi {p:6.1%}   delta {p - g:+.1%}")
    print(f"{'solve_seconds (mean)':<30} greedy {mean(0, 'solve_seconds'):6.3f}   "
          f"sarthi {mean(1, 'solve_seconds'):6.2f}")


def coa_benchmark(args) -> None:
    """Each COA re-planned from the Max-effect plan; every plan must pass the validator."""
    cols = [("fulfil", lambda c: c.kpis["priority_weighted_fulfilment"] * 100, "{:5.1f}"),
            ("losses", lambda c: c.metrics["expected_losses"], "{:5.2f}"),
            ("worst", lambda c: c.kpis["max_sortie_risk"] * 100, "{:4.0f}%"),
            ("sorties", lambda c: c.kpis["sorties"], "{:4.0f}"),
            ("weapons", lambda c: c.metrics["munitions_total"], "{:4.0f}"),
            ("ground", lambda c: c.metrics["min_fighters_on_ground"], "{:4.0f}"),
            ("p05", lambda c: c.robustness_p05 * 100, "{:4.0f}%")]
    agg: dict[str, list[list[float]]] = {}
    secs = []
    for seed in range(1, args.seeds + 1):
        w = generate(seed, n_strike=args.strikes)
        c = candidates.build(w)
        plan = optimizer.solve(w, c, hint=greedy.solve(w, c), time_limit=args.time_limit)
        t0 = time.perf_counter()
        res = coa.compare(w, plan, time_limit=6.0)
        secs.append(time.perf_counter() - t0)
        print(f"seed {seed}  ({len(w.missions)} missions, {secs[-1]:.1f} s)")
        for cw, x in res:
            errs = validate(cw, x.plan, candidates.build(cw))
            if errs:
                raise SystemExit(f"constraint violation, seed {seed} {x.name}: {errs[:5]}")
            vals = [f(x) for _, f, _ in cols]
            agg.setdefault(x.name, []).append(vals)
            print(f"  {x.name:<18}" + " ".join(f"{k}={fmt.format(v)}" for (k, _, fmt), v in zip(cols, vals)))
    print("-" * 72)
    print(f"mean over {args.seeds} scenarios (fulfil %, expected losses, worst sortie risk, sorties, "
          f"guided weapons, min fighters on ground, p05 robustness); compare {st.mean(secs):.1f} s")
    for name, rows in agg.items():
        means = [st.mean(r[i] for r in rows) for i in range(len(cols))]
        print(f"  {name:<18}" + " ".join(f"{k}={fmt.format(v)}" for (k, _, fmt), v in zip(cols, means)))


if __name__ == "__main__":
    main()
