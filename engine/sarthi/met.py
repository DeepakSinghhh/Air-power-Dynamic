"""Airfield fog forecasting: MOS post-processing of open NWP forecasts.

Raw NWP visibility is notoriously poor for radiation fog (on 3 Jan 2025 the archived
forecast for Delhi said 24 km all day while the airport reported 0 m). The fields that
*precede* fog, however, are well forecast: saturation (RH, dew-point depression), light
winds, low cloud and time of night. A logistic Model Output Statistics (MOS) model maps
those fields to P(visibility < 1 km), trained on observed METARs (tools/build_fog_model.py).

Sources: Open-Meteo forecast / historical-forecast APIs (free, no key) and a cached
snapshot of a real dense-fog night so demos work offline.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from .geo import fmt_time
from .models import World

DATA = Path(__file__).parent / "data" / "met"
HOURLY = ["temperature_2m", "dew_point_2m", "relative_humidity_2m", "wind_speed_10m",
          "cloud_cover_low", "cloud_cover", "visibility"]
FOG_VIS_M = 1000.0  # ICAO fog: visibility below 1 km
FEATURES = ["rh", "rh_hi", "dpd", "wind", "low_cloud", "total_cloud", "hour_sin", "hour_cos",
            "rh_trend3", "calm_humid"]


# ---------- features & model ----------

def _arr(h: dict, key: str) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in h[key]], dtype=float)


def features(h: dict) -> np.ndarray:
    """Feature matrix (n_hours x len(FEATURES)) from Open-Meteo hourly arrays (local time)."""
    rh = _arr(h, "relative_humidity_2m")
    dpd = np.clip(_arr(h, "temperature_2m") - _arr(h, "dew_point_2m"), 0, 15)
    wind = np.clip(_arr(h, "wind_speed_10m"), 0, 40)  # km/h
    low = _arr(h, "cloud_cover_low") / 100
    tot = _arr(h, "cloud_cover") / 100
    hour = np.array([int(t[11:13]) for t in h["time"]], dtype=float)
    lag = np.concatenate([rh[:3], rh[:-3]]) if len(rh) > 3 else rh
    return np.column_stack([
        (rh - 85) / 10,
        np.maximum(rh - 95, 0) / 5,
        dpd / 3,
        wind / 10,
        low,
        tot,
        np.sin(2 * np.pi * hour / 24),
        np.cos(2 * np.pi * hour / 24),
        (rh - lag) / 10,
        ((rh >= 95) & (wind <= 8)).astype(float),
    ])


def _irls(Z: np.ndarray, y: np.ndarray, reg: np.ndarray, sw: np.ndarray, iters: int = 50) -> np.ndarray:
    w = np.zeros(Z.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(Z @ w, -30, 30)))
        g = Z.T @ (sw * (p - y)) + reg * w
        H = (Z * (sw * p * (1 - p))[:, None]).T @ Z + np.diag(reg)
        step = np.linalg.solve(H, g)
        w -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return w


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 50,
                 sample_weight: np.ndarray | None = None) -> dict:
    """L2-regularised logistic regression by IRLS (Newton). Returns standardisation + weights."""
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd == 0] = 1.0
    Z = np.column_stack([np.ones(len(X)), (X - mu) / sd])
    reg = np.full(Z.shape[1], l2)
    reg[0] = 0.0
    sw = np.ones(len(y)) if sample_weight is None else sample_weight
    w = _irls(Z, y, reg, sw, iters)
    return {"features": FEATURES, "mean": mu.tolist(), "std": sd.tolist(), "weights": w.tolist()}


def calibrate(model: dict, X: np.ndarray, y: np.ndarray) -> dict:
    """Platt recalibration on a later, held-apart period: p' = sigmoid(a * logit(p) + b)."""
    p = np.clip(predict({**model, "calibration": None}, X), 1e-6, 1 - 1e-6)
    Z = np.column_stack([np.ones(len(p)), np.log(p / (1 - p))])
    b, a = _irls(Z, y, np.zeros(2), np.ones(len(y)))
    return {**model, "calibration": {"a": float(a), "b": float(b)}}


def predict(model: dict, X: np.ndarray) -> np.ndarray:
    Z = np.column_stack([np.ones(len(X)), (X - np.array(model["mean"])) / np.array(model["std"])])
    p = 1 / (1 + np.exp(-np.clip(Z @ np.array(model["weights"]), -30, 30)))
    cal = model.get("calibration")
    if cal:
        q = np.clip(p, 1e-6, 1 - 1e-6)
        p = 1 / (1 + np.exp(-(cal["a"] * np.log(q / (1 - q)) + cal["b"])))
    return np.where(np.isnan(p), 0.0, p)


@lru_cache(maxsize=1)
def load_model() -> dict:
    return json.loads((DATA / "fog_model.json").read_text())


# ---------- data access ----------

def fetch_open_meteo(points: list[tuple[float, float]], start: str | None = None, end: str | None = None,
                     historical: bool = False, days: int = 2, retries: int = 12) -> list[dict]:
    """Hourly NWP for several points in one request (local India time). Retries transient failures."""
    host = "historical-forecast-api.open-meteo.com" if historical else "api.open-meteo.com"
    q = {"latitude": ",".join(f"{p[0]:.4f}" for p in points),
         "longitude": ",".join(f"{p[1]:.4f}" for p in points),
         "hourly": ",".join(HOURLY), "timezone": "Asia/Kolkata", "wind_speed_unit": "kmh"}
    if start:
        q.update(start_date=start, end_date=end or start)
    else:
        q["forecast_days"] = days
    url = f"https://{host}/v1/forecast?{urllib.parse.urlencode(q)}"
    err: Exception | None = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                data = json.loads(r.read())
            data = data if isinstance(data, list) else [data]
            return [d["hourly"] for d in data]
        except Exception as e:  # network errors are expected offline; caller falls back to snapshot
            err = e
            # Free-tier quotas are per egress IP; a quick retry often lands on another IP.
            time.sleep(1 if "429" in str(e) else min(2 ** i, 4))
    raise RuntimeError(f"Open-Meteo unavailable: {err}")


# ---------- forecasts for the scenario ----------

class BaseMet(BaseModel):
    times: list[int]                    # scenario minutes (00:00 D-day = 0)
    p_fog: list[float]                  # MOS probability of visibility < 1 km
    nwp_vis_m: list[float | None]       # raw model visibility, for comparison
    rh: list[float | None]
    wind_kmh: list[float | None]
    observed_vis_m: list[float | None] | None = None  # METAR at nearest station (snapshots only)
    observed_station: str | None = None


class MetWindow(BaseModel):
    base: str
    start: int
    end: int
    peak: float


class MetForecast(BaseModel):
    source: str                         # "snapshot" | "live"
    label: str
    date: str                           # local date mapped to scenario D-day
    model: dict = Field(default_factory=dict)  # MOS metadata (skill scores)
    bases: dict[str, BaseMet]


def _to_base_met(h: dict, date: str, model: dict, observed: list | None = None,
                 station: str | None = None) -> BaseMet:
    t0 = np.datetime64(f"{date}T00:00")
    mins = [int((np.datetime64(t) - t0) / np.timedelta64(1, "m")) for t in h["time"]]
    p = predict(model, features(h))
    keep = [i for i, m in enumerate(mins) if 0 <= m <= 30 * 60]

    def pick(xs):
        return [None if xs[i] is None else float(xs[i]) for i in keep]
    return BaseMet(times=[mins[i] for i in keep], p_fog=[round(float(p[i]), 3) for i in keep],
                   nwp_vis_m=pick(h["visibility"]), rh=pick(h["relative_humidity_2m"]),
                   wind_kmh=pick(h["wind_speed_10m"]),
                   observed_vis_m=[observed[i] for i in keep] if observed else None, observed_station=station)


def snapshot_forecast(world: World) -> MetForecast:
    """Cached real dense-fog night (out-of-sample for the model), mapped onto the scenario's D-day."""
    snap = json.loads((DATA / "snapshot.json").read_text())
    model = load_model()
    bases = {}
    for bid in world.bases:
        if bid in snap["bases"]:
            b = snap["bases"][bid]
            bases[bid] = _to_base_met(b["hourly"], snap["date"], model, b.get("observed_vis_m"), b.get("station"))
    return MetForecast(source="snapshot", label=snap["label"], date=snap["date"], model=model.get("meta", {}),
                       bases=bases)


def live_forecast(world: World) -> MetForecast:
    """Next ~30 h from Open-Meteo for every base, with D-day = today (India time)."""
    ids = list(world.bases)
    hourly = fetch_open_meteo([(world.bases[b].lat, world.bases[b].lon) for b in ids], days=3)
    date = hourly[0]["time"][0][:10]
    model = load_model()
    return MetForecast(source="live", label=f"Open-Meteo forecast from {date}", date=date,
                       model=model.get("meta", {}),
                       bases={b: _to_base_met(h, date, model) for b, h in zip(ids, hourly)})


def fog_windows(fc: MetForecast, threshold: float, after: int = 0, min_hours: int = 1) -> list[MetWindow]:
    """Contiguous runs of hours with P(fog) >= threshold, starting no earlier than `after`."""
    out = []
    for bid, b in fc.bases.items():
        run: list[int] = []
        for i, (t, p) in enumerate(zip(b.times, b.p_fog)):
            if p >= threshold and t + 60 > after:
                run.append(i)
            if run and (p < threshold or i == len(b.times) - 1):
                if len(run) >= min_hours:
                    s = max(b.times[run[0]], after)
                    e = b.times[run[-1]] + 59
                    out.append(MetWindow(base=bid, start=s, end=e, peak=max(b.p_fog[j] for j in run)))
                run = []
    return sorted(out, key=lambda w: (w.start, w.base))


def describe(w: MetWindow, world: World) -> str:
    return f"{world.bases[w.base].name}: P(fog) up to {w.peak:.0%}, {fmt_time(w.start)}-{fmt_time(w.end)}"


def closure_events(world: World, windows: list[MetWindow], at: int) -> list:
    """BaseClosure events for forecast windows not already covered by an existing closure."""
    from .events import BaseClosure
    out = []
    for w in windows:
        covered = any(c.start <= w.start and w.end <= c.end for c in world.bases[w.base].closures)
        if not covered:
            out.append(BaseClosure(at=at, base=w.base, start=w.start, end=w.end,
                                   reason="fog forecast (MOS)", probability=round(w.peak, 2)))
    return out
