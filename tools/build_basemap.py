"""Build the offline basemap and India outline from Natural Earth (public domain).

Uses Natural Earth's India point-of-view admin-0 file, so boundaries follow India's
official depiction (J&K and Ladakh in full).

    curl -o countries_ind.geojson https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries_ind.geojson
    curl -o rivers.geojson https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_rivers_lake_centerlines.geojson
    python -I tools/build_basemap.py countries_ind.geojson rivers.geojson
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
VIEW = (60.0, 15.0, 95.0, 42.0)      # lon0, lat0, lon1, lat1 kept in the basemap
RIVER_VIEW = (66.0, 20.0, 84.0, 38.0)


def dp(points: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker simplification (iterative)."""
    if len(points) < 3:
        return points
    keep = np.zeros(len(points), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = points[i], points[j]
        seg = points[i + 1:j]
        ab = b - a
        n = np.hypot(*ab)
        rel = seg - a
        d = np.abs(ab[0] * rel[:, 1] - ab[1] * rel[:, 0]) / n if n > 0 else np.hypot(rel[:, 0], rel[:, 1])
        k = int(np.argmax(d))
        if d[k] > tol:
            keep[i + 1 + k] = True
            stack += [(i, i + 1 + k), (i + 1 + k, j)]
    return points[keep]


def bbox_hits(coords: np.ndarray, box) -> bool:
    lo, hi = coords.min(axis=0), coords.max(axis=0)
    return not (hi[0] < box[0] or lo[0] > box[2] or hi[1] < box[1] or lo[1] > box[3])


def polys(geom) -> list:
    return [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]


def simplify_rings(poly, tol):
    out = []
    for ring in poly:
        r = dp(np.asarray(ring, dtype=float), tol)
        if len(r) >= 4:
            out.append(np.round(r, 3).tolist())
    return out


def main(countries_path: str, rivers_path: str) -> None:
    countries = json.loads(Path(countries_path).read_text())
    feats, india = [], []
    for f in countries["features"]:
        p = f["properties"]
        parts = [pl for pl in polys(f["geometry"]) if bbox_hits(np.asarray(pl[0]), VIEW)]
        if not parts:
            continue
        is_india = p.get("ADM0_A3") == "IND"
        simp = [s for s in (simplify_rings(pl, 0.01) for pl in parts) if s]
        feats.append({"type": "Feature", "properties": {"name": p.get("NAME"), "a3": p.get("ADM0_A3"),
                                                        "india": is_india},
                      "geometry": {"type": "MultiPolygon", "coordinates": simp}})
        if is_india:
            india = [s[0] for s in (simplify_rings(pl, 0.02) for pl in parts) if s]

    rivers = json.loads(Path(rivers_path).read_text())
    rfeats = []
    for f in rivers["features"]:
        g = f["geometry"]
        lines = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
        lines = [dp(np.asarray(ln, dtype=float), 0.01) for ln in lines]
        lines = [np.round(ln, 3).tolist() for ln in lines if len(ln) >= 2 and bbox_hits(ln, RIVER_VIEW)]
        if lines:
            rfeats.append({"type": "Feature", "properties": {"name": f["properties"].get("name"),
                                                             "rank": f["properties"].get("scalerank")},
                           "geometry": {"type": "MultiLineString", "coordinates": lines}})

    out = ROOT / "frontend" / "public" / "geo"
    out.mkdir(parents=True, exist_ok=True)
    (out / "countries.json").write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                                                   separators=(",", ":")))
    (out / "rivers.json").write_text(json.dumps({"type": "FeatureCollection", "features": rfeats},
                                                separators=(",", ":")))
    data = ROOT / "engine" / "sarthi" / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "india.json").write_text(json.dumps({"source": "Natural Earth 10m admin-0, India point of view",
                                                 "rings": india}, separators=(",", ":")))
    print(f"countries: {len(feats)}  rivers: {len(rfeats)}  india rings: {len(india)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
