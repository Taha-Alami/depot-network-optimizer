"""Routing providers used to build zone-to-depot distance matrices.

A real deployment plugs a routing engine (commercial API or a self-hosted
OSRM/Valhalla instance) behind the `RoutingProvider` protocol. The provider
should read any credentials from environment variables. The bundled
`HaversineRoutingProvider` needs no service: it estimates road distance as
great-circle distance times a detour factor, which is also the recommended
fallback when a routing request fails.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from depot_opt.geo.distance import haversine_km


@dataclass(frozen=True)
class RouteResult:
    distance_km: float
    travel_time_min: float | None


class RoutingProvider(Protocol):
    def route(self, origin: tuple[float, float], destination: tuple[float, float]) -> RouteResult:
        """Return road distance/time between two (lat, lon) points."""
        ...


@dataclass
class HaversineRoutingProvider:
    """Approximates road metrics from straight-line distance."""

    detour_factor: float = 1.3
    avg_speed_kmh: float = 60.0

    def route(self, origin: tuple[float, float], destination: tuple[float, float]) -> RouteResult:
        km = float(haversine_km(*origin, *destination)) * self.detour_factor
        return RouteResult(distance_km=km, travel_time_min=km / self.avg_speed_kmh * 60)


def build_distance_matrix(
    zones: pd.DataFrame,
    depots: pd.DataFrame,
    provider: RoutingProvider,
    existing: pd.DataFrame | None = None,
    fallback: RoutingProvider | None = None,
) -> pd.DataFrame:
    """Compute zone-depot distances in long format (`ZoneDepotDistanceRecord`).

    Pairs already present in `existing` are skipped (incremental refresh),
    and a failing provider call falls back to `fallback` when given.
    """
    done: set[tuple[str, str]] = set()
    if existing is not None and not existing.empty:
        done = set(zip(existing["zone_id"], existing["depot_id"], strict=True))

    rows = []
    for z, d in _pairs(zones, depots):
        if (z.zone_id, d.depot_id) in done:
            continue
        origin, dest = (z.latitude, z.longitude), (d.latitude, d.longitude)
        try:
            res = provider.route(origin, dest)
        except Exception:
            if fallback is None:
                raise
            res = fallback.route(origin, dest)
        rows.append(
            {
                "zone_id": z.zone_id,
                "depot_id": d.depot_id,
                "road_distance_km": res.distance_km,
                "travel_time_min": res.travel_time_min,
                "linear_distance_km": float(haversine_km(*origin, *dest)),
            }
        )
    new = pd.DataFrame(rows)
    if existing is None or existing.empty:
        return new
    return pd.concat([existing, new], ignore_index=True)


def _pairs(zones: pd.DataFrame, depots: pd.DataFrame) -> Iterable:
    for z in zones.itertuples(index=False):
        for d in depots.itertuples(index=False):
            yield z, d
