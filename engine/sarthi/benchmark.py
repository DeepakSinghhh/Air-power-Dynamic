"""Optimiser vs greedy "manual planner" baseline across many random scenarios.

    python -m sarthi.benchmark [--seeds 20] [--time-limit 10]
    python -m sarthi.benchmark --coa [--seeds 8]     # the three courses of action, side by side
    python -m sarthi.benchmark --hadr | --quake [--seeds 20]   # flood / earthquake relief
    python -m sarthi.benchmark --retask [--seeds 8] [--scale 2]   # fog at the busiest base: churn vs naive re-plan
"""
from __future__ import annotations

import argparse
import statistics as st
import time

from . import candidates, coa, greedy, optimizer
from .events import BaseClosure
from .retask import retask
from .kpi import kpis
from .scenario import generate
from .scenario_hadr import generate_hadr
from .scenario_quake import generate_quake
from .validate import validate


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--time-limit", type=float, default=10.0)
    ap.add_argument("--strikes", type=int, default=14, help="strike missions per scenario (load)")
    ap.add_argument("--coa", action="store_true", help="compare the courses of action instead")
    ap.add_argument("--hadr", action="store_true", help="flood-relief scenario instead of the conflict one")
    ap.add_argument("--quake", action="store_true", help="earthquake-relief scenario instead of the conflict one")
    ap.add_argument("--retask", action="store_true", help="retask benchmark: busiest base fogged 05:00-09:30")
    ap.add_argument("--scale", type=int, default=1, help="mission-count multiplier for --retask (1, 2, 3)")
    args = ap.parse_args()
    if args.coa:
        return coa_benchmark(args)
    if args.retask:
        return retask_benchmark(args)

    relief = args.hadr or args.quake
    rows = []
    print(f"{'seed':>4} {'missions':>8} | {'greedy fulfil':>13} {'EV':>5} | {'sarthi fulfil':>13} {'EV':>5} {'t(s)':>6}")
    for seed in range(1, args.seeds + 1):
        w = (generate_quake(seed) if args.quake else generate_hadr(seed) if args.hadr
             else generate(seed, n_strike=args.strikes))
        c = candidates.build(w)
        g = greedy.solve(w, c)
        p = optimizer.solve(w, c, hint=g, time_limit=args.time_limit)
        errs = validate(w, g, c) + validate(w, p, c)
        if errs:
            raise SystemExit(f"constraint violation on seed {seed}: {errs[:5]}")
        kg, kp = kpis(w, g), kpis(w, p)
        if relief:
            kg["relief_t"], kp["relief_t"] = (sum(w.missions[m].cargo_t for m in x.assignments) /
                                              sum(m.cargo_t for m in w.missions.values()) for x in (g, p))
        rows.append((kg, kp))
        print(f"{seed:>4} {len(w.missions):>8} | {kg['priority_weighted_fulfilment']:>13.0%} "
              f"{kg['expected_value']:>5.0%} | {kp['priority_weighted_fulfilment']:>13.0%} "
              f"{kp['expected_value']:>5.0%} {kp['solve_seconds']:>6.1f}")

    def mean(side, key):
        return st.mean(r[side][key] for r in rows)

    print("-" * 64)
    for key in ("priority_weighted_fulfilment", "expected_value", "mean_sortie_risk") + (("relief_t",) if relief else ()):
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


def retask_benchmark(args) -> None:
    """Busiest base fogged 05:00-09:30, learnt at 02:00: minimal-disruption retask vs a naive re-plan."""
    k = args.scale
    rows = []
    for seed in range(1, args.seeds + 1):
        w = generate(seed, n_strike=14 * k, n_dca=5 * k, n_cas=4 * k, n_isr=3 * k, n_airlift=3 * k)
        c = candidates.build(w)
        plan = optimizer.solve(w, c, hint=greedy.solve(w, c), time_limit=args.time_limit)
        busiest = max(w.bases, key=lambda b: sum(s.base == b for a in plan.assignments.values() for s in a.sorties))
        t0 = time.perf_counter()
        res = retask(w, plan, [BaseClosure(at=120, base=busiest, start=300, end=570)], args.time_limit,
                     compare_naive=True)
        secs = time.perf_counter() - t0 - (res.naive_seconds or 0)
        errs = validate(res.world, res.plan, candidates.build(res.world), optimizer.frozen_missions(res.world, res.plan))
        if errs:
            raise SystemExit(f"constraint violation on seed {seed}: {errs[:5]}")
        before, after = kpis(w, plan), kpis(res.world, res.plan)
        sorties = after["sorties"]
        rows.append((len(w.missions), res.naive_diff.aircraft_changes, res.diff.aircraft_changes, secs,
                     before["priority_weighted_fulfilment"], after["priority_weighted_fulfilment"], sorties))
        print(f"seed {seed}: {rows[-1][0]} missions, aircraft changed naive {rows[-1][1]} vs {rows[-1][2]} "
              f"in {secs:.1f} s, fulfilment {rows[-1][4]:.1%} -> {rows[-1][5]:.1%} ({sorties} sorties)")
    m = lambda i: st.mean(r[i] for r in rows)
    print("-" * 64)
    print(f"mean over {args.seeds} scenarios ({m(0):.0f} missions): aircraft changed naive {m(1):.1f} vs "
          f"{m(2):.1f} ({1 - m(2) / max(m(1), 1e-9):.0%} fewer), retask {m(3):.1f} s, "
          f"fulfilment {m(4):.1%} -> {m(5):.1%}")


if __name__ == "__main__":
    main()
