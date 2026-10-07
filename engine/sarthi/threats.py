"""Threat-aware routing over a lat/lon grid.

Each threat contributes a per-km hazard rate inside its weapon-engagement zone,
scaled so a full crossing of the envelope yields its Pk. Intel age inflates the
envelope (a mobile SAM may have moved) while spreading the same lethality over a
wider area. Routes minimise distance + beta * hazard with Dijkstra (SciPy), so
the planner trades detour kilometres (fuel, range, time) against survivability.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from .geo import haversine_km
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

        blocked = np.zeros_like(self.LAT, dtype=bool)
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
            ii, jj = np.divmod(np.asarray(nodes), self.nlon)
            seg = haversine_km(self.LAT[ii[:-1], jj[:-1]], self.LON[ii[:-1], jj[:-1]],
                               self.LAT[ii[1:], jj[1:]], self.LON[ii[1:], jj[1:]])
            h = self.hazard[ii, jj]
            km = float(np.sum(seg))
            hz = float(np.sum((h[:-1] + h[1:]) / 2 * seg))
            # Snap error between real endpoints and grid cell centres.
            km += haversine_km(olat, olon, *self.latlon(src)) + haversine_km(lat, lon, *self.latlon(target))
            path = [self.latlon(nd) for nd in nodes[:: max(1, len(nodes) // 40)]] + [self.latlon(target)]
            out[key] = Route(km=float(km), risk=float(1.0 - np.exp(-2.0 * hz)), path=path)
        return out
