"""Train and evaluate the airfield fog MOS model; build the offline demo snapshot.

Labels: observed METAR visibility (Iowa Environmental Mesonet archive) at north-Indian plains
airfields. Features: archived Open-Meteo forecasts (historical-forecast API) at the same points.
Temporal split: fit on the two oldest winters (equal weight per winter, so an unusually foggy
winter cannot set the base rate), Platt-recalibrate on the next winter, test on the latest one.

    python -I tools/build_fog_model.py <cache-dir>

Writes engine/sarthi/data/met/fog_model.json and snapshot.json. Downloads are cached in <cache-dir>.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from sarthi import met  # noqa: E402
from sarthi.geo import haversine_km  # noqa: E402
from sarthi.scenario import BASES  # noqa: E402

# Indo-Gangetic plain / fog-belt METAR stations (IEM network IN__ASOS).
STATIONS = {
    "VIDP": ("Delhi IGI", 28.567, 77.117), "VIAR": ("Amritsar", 31.633, 74.867),
    "VICG": ("Chandigarh", 30.677, 76.789), "VILD": ("Ludhiana", 30.855, 75.953),
    "VIJP": ("Jaipur", 26.824, 75.812), "VILK": ("Lucknow", 26.761, 80.889),
    "VIAG": ("Agra", 27.156, 77.961), "VIGR": ("Gwalior", 26.233, 78.25),
    "VIJO": ("Jodhpur", 26.3, 73.017), "VIJU": ("Jammu", 32.689, 74.837),
    "VIBY": ("Bareilly", 28.367, 79.4), "VISP": ("Sarsawa", 29.994, 77.425),
}
WINTERS = [("2022-11-15", "2023-02-15"), ("2023-11-15", "2024-02-15"),
           ("2024-11-15", "2025-02-15"), ("2025-11-15", "2026-02-15")]
FIT, CALIB, TEST = WINTERS[:2], WINTERS[2], WINTERS[3]
MILE_M = 1609.344


def cached(cache: Path, name: str, fetch) -> str:
    p = cache / name
    if not p.exists():
        p.write_text(fetch())
        time.sleep(1.0)  # be gentle with free services
    return p.read_text()


def get(url: str, retries: int = 120) -> str:
    """GET with retries. Free-tier quotas are per egress IP; behind a pool of IPs a quick retry
    usually lands on one with quota left, so waits stay short."""
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return r.read().decode()
        except Exception as e:
            if i == retries - 1:
                raise
            wait = 3 if "429" in str(e) else min(2 ** i, 10)
            if i % 5 == 0:
                print(f"  retry {i + 1}: {url[8:50]}... ({str(e)[:60]})", flush=True)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def iem_url(station: str, start: str, end: str) -> str:
    s = dt.date.fromisoformat(start)
    e = dt.date.fromisoformat(end) + dt.timedelta(days=1)
    q = {"station": station, "data": "vsby", "year1": s.year, "month1": s.month, "day1": s.day,
         "year2": e.year, "month2": e.month, "day2": e.day, "tz": "Asia/Kolkata", "format": "onlycomma",
         "latlon": "no", "missing": "M", "trace": "T", "direct": "no"}
    return "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?" + urllib.parse.urlencode(q) \
        + "&report_type=3&report_type=4"


def hourly_obs(text: str) -> dict[str, float]:
    """Min observed visibility (m) per local hour, using reports within +-30 min of the hour."""
    out: dict[str, float] = {}
    for row in csv.DictReader(io.StringIO(text)):
        v = row.get("vsby", "M")
        if v in ("M", "", None):
            continue
        t = dt.datetime.strptime(row["valid"], "%Y-%m-%d %H:%M")
        hour = (t + dt.timedelta(minutes=30)).replace(minute=0)
        key = hour.strftime("%Y-%m-%dT%H:00")
        out[key] = min(out.get(key, 1e9), float(v) * MILE_M)
    return out


def om_url(points, start, end) -> str:
    q = {"latitude": ",".join(f"{p[0]:.4f}" for p in points), "longitude": ",".join(f"{p[1]:.4f}" for p in points),
         "hourly": ",".join(met.HOURLY), "timezone": "Asia/Kolkata", "wind_speed_unit": "kmh",
         "start_date": start, "end_date": end}
    return "https://historical-forecast-api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(q)


def auc(y: np.ndarray, p: np.ndarray) -> float:
    order = np.argsort(p)
    ranks = np.empty(len(p))
    ranks[order] = np.arange(1, len(p) + 1)
    n1 = y.sum()
    n0 = len(y) - n1
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def contingency(y: np.ndarray, yhat: np.ndarray) -> dict:
    hits = int(((yhat == 1) & (y == 1)).sum())
    misses = int(((yhat == 0) & (y == 1)).sum())
    fa = int(((yhat == 1) & (y == 0)).sum())
    return {"pod": round(hits / max(hits + misses, 1), 3), "far": round(fa / max(hits + fa, 1), 3),
            "csi": round(hits / max(hits + misses + fa, 1), 3)}


def main(cache_dir: str) -> None:
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    ids = list(STATIONS)
    pts = [(STATIONS[s][1], STATIONS[s][2]) for s in ids]

    rows: dict[str, list] = {"fit": [], "calib": [], "test": []}
    for start, end in WINTERS:
        split = "test" if (start, end) == TEST else "calib" if (start, end) == CALIB else "fit"
        print(f"winter {start}..{end} ({split})", flush=True)
        # One multi-station request per winter: behind rate-limited shared IPs the number of
        # successful requests needed matters more than request size. Cache is split per station.
        missing = [i for i, sid in enumerate(ids) if not (cache / f"om_{sid}_{start}.json").exists()]
        if missing:
            got = json.loads(get(om_url([pts[i] for i in missing], start, end)))
            got = got if isinstance(got, list) else [got]
            for i, loc in zip(missing, got):
                (cache / f"om_{ids[i]}_{start}.json").write_text(json.dumps(loc))
        for sid in ids:
            h = json.loads((cache / f"om_{sid}_{start}.json").read_text())["hourly"]
            obs = hourly_obs(cached(cache, f"iem_{sid}_{start}.csv", lambda: get(iem_url(sid, start, end))))
            X = met.features(h)
            for i, t in enumerate(h["time"]):
                v = obs.get(t)
                if v is None or np.isnan(X[i]).any():
                    continue
                nwp = h["visibility"][i]
                rows[split].append((X[i], float(v < met.FOG_VIS_M), nwp if nwp is not None else 1e9, sid, t, start))
        print(f"  rows so far: fit {len(rows['fit'])}, calib {len(rows['calib'])}, test {len(rows['test'])}", flush=True)

    def arrays(split):
        r = rows[split]
        return (np.array([x[0] for x in r]), np.array([x[1] for x in r]), np.array([x[2] for x in r], dtype=float))

    Xtr, ytr, _ = arrays("fit")
    Xca, yca, _ = arrays("calib")
    Xte, yte, nwp_te = arrays("test")
    # Equal total weight per fitting winter.
    winters = np.array([r[5] for r in rows["fit"]])
    sw = np.array([len(winters) / (len(set(winters)) * (winters == w).sum()) for w in winters])
    raw = met.fit_logistic(Xtr, ytr, l2=1.0, sample_weight=sw)
    model = met.calibrate(raw, Xca, yca)
    p = met.predict(model, Xte)
    p_raw = met.predict(raw, Xte)
    clim = float(np.concatenate([ytr, yca]).mean())
    brier = float(np.mean((p - yte) ** 2))
    brier_clim = float(np.mean((clim - yte) ** 2))
    bins = np.linspace(0, 1, 11)
    reliability = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if m.sum() >= 20:
            reliability.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": int(m.sum()),
                                "forecast": round(float(p[m].mean()), 3), "observed": round(float(yte[m].mean()), 3)})
    meta = {
        "target": "P(visibility < 1 km) at the hour",
        "stations": {s: STATIONS[s][0] for s in ids},
        "train_winters": [f"{a}..{b}" for a, b in FIT],
        "calibration_winter": f"{CALIB[0]}..{CALIB[1]}",
        "test_winter": f"{TEST[0]}..{TEST[1]}",
        "n_train": int(len(ytr) + len(yca)), "n_fit": int(len(ytr)), "n_calibration": int(len(yca)),
        "n_test": int(len(yte)),
        "brier_uncalibrated": round(float(np.mean((p_raw - yte) ** 2)), 4),
        "fog_rate_test": round(float(yte.mean()), 4),
        "auc": round(auc(yte, p), 3),
        "brier": round(brier, 4), "brier_climatology": round(brier_clim, 4),
        "brier_skill": round(1 - brier / brier_clim, 3),
        "mos_at_0.5": contingency(yte, (p >= 0.5).astype(int)),
        "raw_nwp_vis_below_1km": contingency(yte, (nwp_te < met.FOG_VIS_M).astype(int)),
        "reliability": reliability,
        "weights": dict(zip(["intercept"] + met.FEATURES, [round(w, 3) for w in model["weights"]])),
    }
    model["meta"] = meta
    out = ROOT / "engine" / "sarthi" / "data" / "met"
    out.mkdir(parents=True, exist_ok=True)
    (out / "fog_model.json").write_text(json.dumps(model, indent=1))
    print(json.dumps({k: v for k, v in meta.items() if k not in ("reliability", "stations")}, indent=1))
    for r in reliability:
        print("  reliability", r)

    # Demo night: the test-winter date with the most stations reporting >= 3 fog hours, 00-10 local.
    per_day: dict[str, dict[str, int]] = {}
    for x, y, _, sid, t, _w in rows["test"]:
        if y and int(t[11:13]) <= 10:
            per_day.setdefault(t[:10], {}).setdefault(sid, 0)
            per_day[t[:10]][sid] += 1
    date = max(per_day, key=lambda d: (sum(1 for n in per_day[d].values() if n >= 3), d))
    n_st = sum(1 for n in per_day[date].values() if n >= 3)
    print(f"demo night: {date} ({n_st} stations with >= 3 fog hours before 10:00)")

    nxt = (dt.date.fromisoformat(date) + dt.timedelta(days=1)).isoformat()
    bases = [(b[0], b[2], b[3]) for b in BASES]
    om = json.loads(cached(cache, f"om_demo_{date}.json", lambda: get(om_url([(b[1], b[2]) for b in bases], date, nxt))))
    om = om if isinstance(om, list) else [om]
    snap = {"date": date, "label": f"Dense-fog night of {date} (cached; held-out test winter)", "bases": {}}
    for (bid, lat, lon), loc in zip(bases, om):
        entry = {"hourly": loc["hourly"]}
        near = min(ids, key=lambda s: haversine_km(lat, lon, STATIONS[s][1], STATIONS[s][2]))
        if haversine_km(lat, lon, STATIONS[near][1], STATIONS[near][2]) <= 50:
            obs = hourly_obs(cached(cache, f"iem_{near}_{date}.csv", lambda: get(iem_url(near, date, nxt))))
            vis = [None if obs.get(t) is None else round(obs[t]) for t in loc["hourly"]["time"]]
            # Only claim verification where the station actually reported most hours (archives have gaps).
            if sum(v is not None for v in vis[:31]) >= 12:
                entry["observed_vis_m"] = vis
                entry["station"] = f"{near} {STATIONS[near][0]}"
        snap["bases"][bid] = entry
    (out / "snapshot.json").write_text(json.dumps(snap, separators=(",", ":")))
    print("wrote", out / "fog_model.json", "and", out / "snapshot.json")


if __name__ == "__main__":
    main(sys.argv[1])
