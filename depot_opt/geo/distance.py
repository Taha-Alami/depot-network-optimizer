"""Distance utilities and distance-band freight tariffs."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km. Works on scalars and numpy arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def distance_band(linear_km: float, band_width_km: float, max_band: int) -> int:
    """Map a linear distance to a tariff band.

    Band 1 covers [0, w), band 2 covers [w, 2w), ... and every distance
    beyond the last regular band falls into `max_band`.
    """
    if linear_km is None or (isinstance(linear_km, float) and math.isnan(linear_km)):
        raise ValueError("Cannot assign a distance band to a missing distance.")
    return min(int(linear_km // band_width_km) + 1, max_band)


def blended_rates(rates: pd.DataFrame, carrier_mix: dict[str, float]) -> dict[int, float]:
    """Blend tariffs of several carrier options into one rate per band.

    rate_b = sum_k  share_k * rate_{b,k}

    `rates` follows `TransportRateRecord`. Every carrier option in
    `carrier_mix` must have a rate for every band present.
    """
    wide = rates.pivot_table(
        index="band", columns="carrier_option", values="rate_per_unit_km", aggfunc="mean"
    )
    missing = set(carrier_mix) - set(wide.columns)
    if missing:
        raise ValueError(f"No tariff found for carrier options: {sorted(missing)}")
    subset = wide[list(carrier_mix)]
    if subset.isna().any().any():
        raise ValueError("Tariff table has gaps: every band needs a rate for every carrier option.")
    weights = pd.Series(carrier_mix)
    return (subset * weights).sum(axis=1).to_dict()


def filter_to_bbox(
    frame: pd.DataFrame,
    bbox: tuple[float, float, float, float],
    lat_col: str = "latitude",
    lon_col: str = "longitude",
) -> pd.DataFrame:
    """Drop geocoded points outside (min_lat, max_lat, min_lon, max_lon).

    Useful to discard obviously wrong geocoding hits before building a
    distance matrix.
    """
    min_lat, max_lat, min_lon, max_lon = bbox
    mask = frame[lat_col].between(min_lat, max_lat) & frame[lon_col].between(min_lon, max_lon)
    return frame.loc[mask].copy()
