"""Threat-aware routing over a lat/lon grid.

Each threat contributes a per-km hazard rate inside its weapon-engagement zone,
scaled so a full crossing of the envelope yields its Pk. Intel age inflates the
envelope (a mobile SAM may have moved) while spreading the same lethality over a
wider area. Routes minimise distance + beta * hazard with Dijkstra (SciPy), so
the planner trades detour kilometres (fuel, range, time) against survivability.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from .geo import haversine_km, in_india
from .models import Threat, World

BETA_KM = 1500.0      # km of detour worth one unit of hazard exponent
MAX_INFLATION = 1.5   # cap on uncertainty growth of an envelope


@dataclass
class Route:
    km: float
    risk: float                       # two-way (ingress + egress) loss probability
    path: list[tuple[float, float]]


def effective_envelope(th: Threat, now: int) -> tuple[float, float]:
    """(radius_km, hazard_per_km) after positional uncertainty growth."""
    age_h = max(now - th.observed_at, 0) / 60.0
    r_eff = min(th.radius_km + th.mobile_kmh * age_h, th.radius_km * MAX_INFLATION)
    base_rate = -np.log(1.0 - min(th.pk, 0.999)) / (2.0 * th.radius_km)
    return r_eff, base_rate * (th.radius_km / r_eff) ** 2


@lru_cache(maxsize=8)
def _domestic_mask(area: tuple[float, float, float, float], res: float) -> np.ndarray:
    """Grid cells inside India's boundary (routes for domestic operations must not leave it)."""
    lat0, lat1, lon0, lon1 = area
    lats = lat0 + np.arange(int(round((lat1 - lat0) / res)) + 1) * res
    lons = lon0 + np.arange(int(round((lon1 - lon0) / res)) + 1) * res
    LAT, LON = np.meshgrid(lats, lons, indexing="ij")
    return in_india(LAT, LON)


class RiskField:
    def __init__(self, world: World, suppress: dict[str, float] | None = None, res: float = 0.1):
        lat0, lat1, lon0, lon1 = world.area
        self.lat0, self.lon0, self.res = lat0, lon0, res
        self.nlat = int(round((lat1 - lat0) / res)) + 1
        self.nlon = int(round((lon1 - lon0) / res)) + 1
        lats = lat0 + np.arange(self.nlat) * res
        lons = lon0 + np.arange(self.nlon) * res
        self.LAT, self.LON = np.meshgrid(lats, lons, indexing="ij")

        hazard = np.zeros_like(self.LAT)
        suppress = suppress or {}
        for th in world.threats.values():
            r_eff, rate = effective_envelope(th, world.now)
            rate *= suppress.get(th.id, 1.0)
            d = haversine_km(th.lat, th.lon, self.LAT, self.LON)
            hazard += np.where(d <= r_eff, rate, 0.0)
        self.hazard = hazard

        blocked = ~_domestic_mask(world.area, res) if world.domestic_only else np.zeros_like(self.LAT, dtype=bool)
        for z in world.zones.values():
            blocked |= haversine_km(z.lat, z.lon, self.LAT, self.LON) <= z.radius_km
        self.blocked = blocked
        self._build_graph()

    def _build_graph(self) -> None:
        n_lat, n_lon = self.nlat, self.nlon
        idx = np.arange(n_lat * n_lon).reshape(n_lat, n_lon)
        rows, cols, w = [], [], []
        km_lat = self.res * 111.32
        for di, dj in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
            i0, i1 = max(0, -di), n_lat - max(0, di)
            j0, j1 = max(0, -dj), n_lon - max(0, dj)
            u = idx[i0:i1, j0:j1]
            v = idx[i0 + di:i1 + di, j0 + dj:j1 + dj]
            lat_mid = self.LAT[i0:i1, j0:j1] + di * self.res / 2
            dx = dj * self.res * 111.32 * np.cos(np.radians(lat_mid))
            length = np.hypot(di * km_lat, dx)
            h = (self.hazard[i0:i1, j0:j1] + self.hazard[i0 + di:i1 + di, j0 + dj:j1 + dj]) / 2 * length
            ok = ~(self.blocked[i0:i1, j0:j1] | self.blocked[i0 + di:i1 + di, j0 + dj:j1 + dj])
            rows.append(u[ok])
            cols.append(v[ok])
            w.append(length[ok] + BETA_KM * h[ok])
        n = n_lat * n_lon
        self.graph = csr_matrix((np.concatenate(w), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n))

    def node(self, lat: float, lon: float) -> int:
        i = int(np.clip(round((lat - self.lat0) / self.res), 0, self.nlat - 1))
        j = int(np.clip(round((lon - self.lon0) / self.res), 0, self.nlon - 1))
        return i * self.nlon + j

    def latlon(self, node: int) -> tuple[float, float]:
        i, j = divmod(node, self.nlon)
        return float(self.LAT[i, j]), float(self.LON[i, j])

    def _segment(self, a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
        """(km, hazard exponent) along a straight leg, sampled at half-cell spacing; inf if blocked."""
        km = haversine_km(a[0], a[1], b[0], b[1])
        n = max(2, int(km / (self.res * 111.32 * 0.5)) + 2)
        t = np.linspace(0.0, 1.0, n)
        i = np.clip(np.round((a[0] + (b[0] - a[0]) * t - self.lat0) / self.res).astype(int), 0, self.nlat - 1)
        j = np.clip(np.round((a[1] + (b[1] - a[1]) * t - self.lon0) / self.res).astype(int), 0, self.nlon - 1)
        if self.blocked[i, j].any():
            return float("inf"), float("inf")
        h = self.hazard[i, j]
        return km, float(np.sum((h[:-1] + h[1:]) / 2) * km / (n - 1))

    def _smooth(self, nodes: list[int], tol: float = 0.01) -> list[tuple[float, float]]:
        """Any-angle string pulling: replace grid zig-zags with straight legs that cost no more.

        Greedy from each vertex, exponential + binary search for the farthest node whose
        direct leg costs <= the grid path between them (km + beta * hazard), within `tol`.
        """
        pts = [self.latlon(nd) for nd in nodes]
        if len(pts) <= 2:
            return pts
        ii, jj = np.divmod(np.asarray(nodes), self.nlon)
        seg = haversine_km(self.LAT[ii[:-1], jj[:-1]], self.LON[ii[:-1], jj[:-1]],
                           self.LAT[ii[1:], jj[1:]], self.LON[ii[1:], jj[1:]])
        h = self.hazard[ii, jj]
        cum = np.concatenate([[0.0], np.cumsum(seg + BETA_KM * (h[:-1] + h[1:]) / 2 * seg)])

        def ok(a: int, b: int) -> bool:
            km, hz = self._segment(pts[a], pts[b])
            return km + BETA_KM * hz <= (cum[b] - cum[a]) * (1 + tol) + 1e-9

        last = len(pts) - 1
        out, i = [0], 0
        while i < last:
            good, step, bad = i + 1, 2, None
            while True:  # exponential search
                cand = min(i + step, last)
                if ok(i, cand):
                    good = cand
                    if cand == last:
                        break
                    step *= 2
                else:
                    bad = cand
                    break
            while bad is not None and bad - good > 1:  # binary search
                mid = (good + bad) // 2
                if ok(i, mid):
                    good = mid
                else:
                    bad = mid
            out.append(good)
            i = good
        return [pts[k] for k in out]

    def routes_to(self, lat: float, lon: float, origins: dict[str, tuple[float, float]]) -> dict[str, Route]:
        """Best route from each origin to (lat, lon). Undirected grid, so search from the target."""
        target = self.node(lat, lon)
        _, pred = dijkstra(self.graph, directed=True, indices=target, return_predecessors=True)
        out = {}
        for key, (olat, olon) in origins.items():
            src = self.node(olat, olon)
            if src != target and pred[src] < 0:
                out[key] = Route(km=float("inf"), risk=1.0, path=[])
                continue
            nodes = [src]
            while nodes[-1] != target:
                nodes.append(int(pred[nodes[-1]]))
            pts = self._smooth(nodes)
            km, hz = 0.0, 0.0
            for a, b in zip(pts, pts[1:]):
                dk, dh = self._segment(a, b)
                km += dk
                hz += dh
            # Snap error between real endpoints and grid cell centres.
            km += haversine_km(olat, olon, *pts[0]) + haversine_km(lat, lon, *pts[-1])
            path = [(round(a, 4), round(b, 4)) for a, b in pts]
            out[key] = Route(km=float(km), risk=float(1.0 - np.exp(-2.0 * hz)), path=path)
        return out
