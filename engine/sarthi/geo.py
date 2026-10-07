"""Geodesy and integer-interval helpers."""
from __future__ import annotations

import math

import numpy as np

R_EARTH_KM = 6371.0088


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance; works on floats or numpy arrays."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    d = 2 * R_EARTH_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
    return float(d) if np.ndim(d) == 0 else d


def fmt_time(t: int) -> str:
    day, rem = divmod(int(t), 1440)
    s = f"{rem // 60:02d}:{rem % 60:02d}"
    return s if day == 0 else f"{s}+{day}"


# Closed integer intervals [a, b], kept sorted and disjoint.
Intervals = list[tuple[int, int]]


def intersect(xs: Intervals, ys: Intervals) -> Intervals:
    out, i, j = [], 0, 0
    while i < len(xs) and j < len(ys):
        lo, hi = max(xs[i][0], ys[j][0]), min(xs[i][1], ys[j][1])
        if lo <= hi:
            out.append((lo, hi))
        if xs[i][1] < ys[j][1]:
            i += 1
        else:
            j += 1
    return out


def subtract(xs: Intervals, a: int, b: int) -> Intervals:
    out = []
    for lo, hi in xs:
        if hi < a or lo > b:
            out.append((lo, hi))
            continue
        if lo < a:
            out.append((lo, a - 1))
        if hi > b:
            out.append((b + 1, hi))
    return out


def contains(xs: Intervals, t: int) -> bool:
    return any(lo <= t <= hi for lo, hi in xs)


def mask_to_intervals(times: np.ndarray, ok: np.ndarray, step: int) -> Intervals:
    """Convert a boolean mask sampled every `step` minutes to closed intervals."""
    out: Intervals = []
    start = None
    for t, flag in zip(times, ok):
        if flag and start is None:
            start = int(t)
        elif not flag and start is not None:
            out.append((start, int(t) - 1))
            start = None
    if start is not None:
        out.append((start, int(times[-1]) + step - 1))
    return out


def ceil_div(a: float, b: float) -> int:
    return int(math.ceil(a / b))


# --- India outline (Natural Earth 10m admin-0, India point of view) -------------------------

_INDIA: list[np.ndarray] | None = None


def _india_rings() -> list[np.ndarray]:
    global _INDIA
    if _INDIA is None:
        import json
        from pathlib import Path
        data = json.loads((Path(__file__).parent / "data" / "india.json").read_text())
        _INDIA = [np.asarray(r, dtype=float) for r in data["rings"]]
    return _INDIA


def in_india(lat, lon):
    """Point-in-polygon (even-odd ray casting); works on floats or numpy arrays."""
    lat, lon = np.broadcast_arrays(np.asarray(lat, dtype=float), np.asarray(lon, dtype=float))
    shape = lat.shape
    la, lo = lat.ravel(), lon.ravel()
    inside = np.zeros(la.shape, dtype=bool)
    for r in _india_rings():
        x1, y1 = r[:, 0], r[:, 1]
        x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
        keep = y1 != y2
        x1, y1, x2, y2 = x1[keep], y1[keep], x2[keep], y2[keep]
        if la.size <= 256:  # few points: vectorise across edges
            crosses = (y1 > la[:, None]) != (y2 > la[:, None])
            xint = (x2 - x1) * (la[:, None] - y1) / (y2 - y1) + x1
            inside ^= (np.count_nonzero(crosses & (lo[:, None] < xint), axis=1) % 2).astype(bool)
        else:               # many points: vectorise across points
            for a, b, c, d in zip(x1, y1, x2, y2):
                inside ^= ((b > la) != (d > la)) & (lo < (c - a) * (la - b) / (d - b) + a)
    inside = inside.reshape(shape)
    return bool(inside) if inside.ndim == 0 else inside


def ring_offsets(lat: float, lon: float, km: float, n: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """n points at distance km around (lat, lon)."""
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    dlat = km / 111.32 * np.cos(ang)
    dlon = km / (111.32 * np.cos(np.radians(lat))) * np.sin(ang)
    return lat + dlat, lon + dlon
