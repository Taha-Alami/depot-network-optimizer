"""Synthetic data generator.

Produces a fully artificial, internally consistent `NetworkDataset` so the
model can be run and tested without any real data. All identifiers,
coordinates and monetary values are random.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from depot_opt.config import SyntheticDataConfig
from depot_opt.geo.routing import HaversineRoutingProvider, build_distance_matrix
from depot_opt.schemas import NetworkDataset, empty_frame

CARRIER_OPTIONS = ("own_fleet", "carrier")


def generate_dataset(cfg: SyntheticDataConfig | None = None, max_band: int = 12) -> NetworkDataset:
    cfg = cfg or SyntheticDataConfig()
    rng = np.random.default_rng(cfg.seed)
    min_lat, max_lat, min_lon, max_lon = cfg.bounding_box
    regions = [f"region_{i + 1}" for i in range(cfg.n_regions)]

    def region_of(lon: np.ndarray) -> list[str]:
        # Regions are vertical slices of the bounding box.
        edges = np.linspace(min_lon, max_lon, cfg.n_regions + 1)
        idx = np.clip(np.digitize(lon, edges[1:-1]), 0, cfg.n_regions - 1)
        return [regions[i] for i in idx]

    # ---- zones: clustered around a few random centres --------------------- #
    n_centres = max(2, cfg.n_zones // 8)
    centres = np.column_stack(
        [rng.uniform(min_lat, max_lat, n_centres), rng.uniform(min_lon, max_lon, n_centres)]
    )
    pick = rng.integers(0, n_centres, cfg.n_zones)
    span = 0.08 * np.array([max_lat - min_lat, max_lon - min_lon])
    zlat = np.clip(centres[pick, 0] + rng.normal(0, span[0], cfg.n_zones), min_lat, max_lat)
    zlon = np.clip(centres[pick, 1] + rng.normal(0, span[1], cfg.n_zones), min_lon, max_lon)
    zones = pd.DataFrame(
        {
            "zone_id": [f"zone_{i + 1:03d}" for i in range(cfg.n_zones)],
            "region": region_of(zlon),
            "latitude": zlat.round(5),
            "longitude": zlon.round(5),
        }
    )

    # ---- demand ------------------------------------------------------------ #
    products = [f"prod_{chr(ord('A') + i)}" for i in range(cfg.n_products)]
    months = pd.date_range(cfg.start_date, periods=cfg.n_months, freq="MS")
    periods = []
    for m in months:
        days = pd.date_range(m, m + pd.offsets.MonthEnd(0), freq="D")
        chosen = rng.choice(
            len(days), size=min(cfg.delivery_days_per_month, len(days)), replace=False
        )
        periods.extend(sorted(days[chosen]))
    zone_intensity = rng.gamma(2.0, 1.0, cfg.n_zones)
    rows = []
    for t in periods:
        for zi, z in enumerate(zones["zone_id"]):
            for p in products:
                q = rng.poisson(0.35 * zone_intensity[zi])
                if q:
                    rows.append({"zone_id": z, "product_id": p, "period": t.date(), "quantity": q})
    demand = pd.DataFrame(rows)

    # ---- depots ------------------------------------------------------------ #
    n_e, n_c = cfg.n_existing_depots, cfg.n_candidate_depots
    n_d = n_e + n_c
    dlat = rng.uniform(min_lat, max_lat, n_d)
    dlon = rng.uniform(min_lon, max_lon, n_d)
    depots = pd.DataFrame(
        {
            "depot_id": [f"depot_{i + 1:02d}" for i in range(n_e)]
            + [f"cand_{i + 1:02d}" for i in range(n_c)],
            "status": ["existing"] * n_e + ["candidate"] * n_c,
            "region": region_of(dlon),
            "latitude": dlat.round(5),
            "longitude": dlon.round(5),
            "rent_cost_per_month": rng.uniform(800, 2000, n_d).round(0),
            "fixed_operating_cost_per_month": rng.uniform(200, 600, n_d).round(0),
            "opening_cost": np.where(np.arange(n_d) >= n_e, rng.uniform(3e4, 8e4, n_d), 0).round(0),
            "closing_cost": np.where(np.arange(n_d) < n_e, rng.uniform(1e4, 5e4, n_d), 0).round(0),
            "expansion_cost_per_batch": rng.uniform(50, 150, n_d).round(0),
        }
    )

    # ---- initial inventory: total supply covers demand with a margin ------- #
    total_by_product = (
        demand.groupby("product_id")["quantity"].sum().reindex(products, fill_value=0)
    )
    inv_rows = []
    for p in products:
        supply = int(np.ceil(total_by_product[p] * 1.3)) + 1
        shares = rng.dirichlet(np.ones(n_e))
        alloc = np.floor(shares * supply).astype(int)
        alloc[0] += supply - alloc.sum()
        for d, q in zip(depots["depot_id"][:n_e], alloc, strict=True):
            inv_rows.append({"depot_id": d, "product_id": p, "quantity": int(q)})
    inventory = pd.DataFrame(inv_rows)

    stock = inventory.groupby("depot_id")["quantity"].sum()
    mean_stock = float(stock.mean())
    depots["capacity_units"] = [
        float(np.ceil(1.6 * stock.get(d, 0)))
        if s == "existing"
        else float(np.ceil(1.2 * mean_stock))
        for d, s in zip(depots["depot_id"], depots["status"], strict=True)
    ]

    # ---- returns: part of the delivered volume comes back later ------------ #
    ret_rows = []
    for row in demand.sample(frac=0.25, random_state=cfg.seed).itertuples(index=False):
        later = [t for t in periods if t.date() > row.period]
        if later:
            ret_rows.append(
                {
                    "depot_id": depots["depot_id"].iloc[rng.integers(0, n_e)],
                    "product_id": row.product_id,
                    "period": later[rng.integers(0, len(later))].date(),
                    "quantity": row.quantity,
                }
            )
    returns = pd.DataFrame(ret_rows) if ret_rows else empty_frame("returns")
    if not returns.empty:
        returns = returns.groupby(["depot_id", "product_id", "period"], as_index=False).sum()

    # ---- distances and tariffs --------------------------------------------- #
    distances = build_distance_matrix(zones, depots, HaversineRoutingProvider(detour_factor=1.3))
    bands = np.arange(1, max_band + 1)
    base = {"own_fleet": 0.020, "carrier": 0.026}
    transport_rates = pd.DataFrame(
        [
            {
                "band": int(b),
                "carrier_option": opt,
                "rate_per_unit_km": round(base[opt] * (1 + 1.5 / b), 5),
            }
            for b in bands
            for opt in CARRIER_OPTIONS
        ]
    )

    dataset = NetworkDataset(
        depots=depots,
        zones=zones,
        demand=demand,
        inventory=inventory,
        zone_depot_distances=distances,
        transport_rates=transport_rates,
        returns=returns,
    )
    dataset.validate()
    return dataset
