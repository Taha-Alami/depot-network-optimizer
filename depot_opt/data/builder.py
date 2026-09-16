"""Turn raw input tables into model sets and parameters."""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from depot_opt.config import ScenarioConfig
from depot_opt.geo.distance import blended_rates, distance_band, haversine_km
from depot_opt.model.data import ModelData, ModelParameters, ModelSets
from depot_opt.schemas import NetworkDataset

LOGGER = logging.getLogger(__name__)

AGGREGATE_PERIOD = "all"


def load_dataset(cfg: ScenarioConfig) -> NetworkDataset:
    """Load inputs from the configured source and apply the optional date filter."""
    source = cfg.data.source
    if source == "synthetic":
        from depot_opt.data.synthetic import generate_dataset

        dataset = generate_dataset(cfg.data.synthetic, max_band=cfg.transport.max_band)
    elif source == "csv":
        from depot_opt.data.io import read_csv

        if not cfg.data.csv_dir:
            raise ValueError("data.csv_dir is required when data.source is 'csv'.")
        dataset = read_csv(cfg.data.csv_dir)
    elif source == "odbc":
        from depot_opt.data.odbc_loader import OdbcLoader

        if not (cfg.data.start_date and cfg.data.end_date):
            raise ValueError("data.start_date and data.end_date are required for ODBC loads.")
        start, end = date.fromisoformat(cfg.data.start_date), date.fromisoformat(cfg.data.end_date)
        with OdbcLoader() as loader:
            dataset = loader.load_dataset(start, end)
        dataset.validate()
    else:  # pragma: no cover - guarded by pydantic
        raise ValueError(source)

    if cfg.data.start_date or cfg.data.end_date:
        start = pd.Timestamp(cfg.data.start_date or "1900-01-01")
        end = pd.Timestamp(cfg.data.end_date or "2999-12-31")
        dataset = _filter_periods(dataset, lambda s: (s >= start) & (s <= end))
    return dataset


def _filter_periods(dataset: NetworkDataset, mask_fn) -> NetworkDataset:
    out = dataset.copy()
    for name in ("demand", "returns", "inflows"):
        frame = getattr(out, name)
        if not frame.empty:
            setattr(out, name, frame[mask_fn(pd.to_datetime(frame["period"]))].copy())
    return out


def split_into_windows(
    dataset: NetworkDataset, window: str = "M"
) -> list[tuple[str, NetworkDataset]]:
    """Split time-dependent tables into consecutive monthly ('M') or quarterly ('Q') windows."""
    stamps = pd.concat(
        [
            pd.to_datetime(getattr(dataset, n)["period"])
            for n in ("demand", "returns", "inflows")
            if not getattr(dataset, n).empty
        ]
    )
    labels = sorted(stamps.dt.to_period(window).unique())
    return [
        (str(lbl), _filter_periods(dataset, lambda s, lbl=lbl: s.dt.to_period(window) == lbl))
        for lbl in labels
    ]


def build_model_data(
    dataset: NetworkDataset,
    cfg: ScenarioConfig,
    initial_stock: dict[tuple[str, str], float] | None = None,
    aggregate: bool | None = None,
) -> ModelData:
    """Build `ModelData` for one model instance.

    Args:
        dataset: validated input tables.
        cfg: scenario configuration.
        initial_stock: overrides the inventory table (used by rolling horizons).
        aggregate: collapse all dates into a single period; defaults to
            ``cfg.horizon.mode == "aggregate"``.
    """
    aggregate = cfg.horizon.mode == "aggregate" if aggregate is None else aggregate
    tr = cfg.transport
    costs = cfg.costs
    depots = dataset.depots.set_index("depot_id")

    # ---- periods ------------------------------------------------------------ #
    dated = [getattr(dataset, n) for n in ("demand", "returns", "inflows")]
    all_dates = sorted({pd.Timestamp(p) for f in dated if not f.empty for p in f["period"]})
    if not all_dates:
        raise ValueError("No demand, returns or inflows in the selected horizon.")
    months = costs.horizon_months or float(len({(d.year, d.month) for d in all_dates}))
    if aggregate:
        periods = [AGGREGATE_PERIOD]
        period_dates = {AGGREGATE_PERIOD: f"{all_dates[0].date()}..{all_dates[-1].date()}"}
    else:
        periods = [d.strftime("%Y-%m-%d") for d in all_dates]
        period_dates = {p: p for p in periods}

    def keyed(frame: pd.DataFrame, first: str) -> dict:
        if frame.empty:
            return {}
        f = frame.copy()
        f["t"] = (
            AGGREGATE_PERIOD if aggregate else pd.to_datetime(f["period"]).dt.strftime("%Y-%m-%d")
        )
        grouped = f.groupby([first, "product_id", "t"])["quantity"].sum()
        return {k: float(v) for k, v in grouped.items() if v}

    demand = keyed(dataset.demand, "zone_id")
    returns = keyed(dataset.returns, "depot_id")
    inflows = keyed(dataset.inflows, "depot_id")

    products = sorted(
        set(dataset.demand.get("product_id", []))
        | set(dataset.inventory.get("product_id", []))
        | set(dataset.returns.get("product_id", []))
        | set(dataset.inflows.get("product_id", []))
    )
    if initial_stock is None:
        initial_stock = {
            (r.depot_id, r.product_id): float(r.quantity)
            for r in dataset.inventory.groupby(["depot_id", "product_id"], as_index=False)[
                "quantity"
            ]
            .sum()
            .itertuples(index=False)
        }

    # ---- depots --------------------------------------------------------------- #
    depot_ids = list(depots.index)
    existing = [d for d in depot_ids if depots.at[d, "status"] == "existing"]
    candidates = [d for d in depot_ids if depots.at[d, "status"] == "candidate"]

    def col(name: str) -> dict[str, float]:
        if name not in depots.columns:
            return dict.fromkeys(depot_ids, 0.0)
        return {d: float(v) for d, v in depots[name].fillna(0.0).items()}

    amortise = months / costs.one_off_amortisation_months
    capacity: dict[str, float | None] = {}
    for d in depot_ids:
        raw = depots.at[d, "capacity_units"] if "capacity_units" in depots.columns else None
        capacity[d] = None if raw is None or pd.isna(raw) else float(raw)
    for d, value in cfg.constraints.capacity.overrides.items():
        if d not in capacity:
            raise ValueError(f"Capacity override for unknown depot '{d}'.")
        capacity[d] = value

    # ---- zone-depot arcs and delivery cost ------------------------------------ #
    rates = blended_rates(dataset.transport_rates, tr.carrier_mix)

    def rate_for(linear_km: float) -> float:
        band = distance_band(linear_km, tr.band_width_km, tr.max_band)
        if band not in rates:
            raise ValueError(f"No tariff for distance band {band}.")
        return rates[band]

    # Only zones with demand in this horizon take part in the assignment.
    demanded = {c for (c, _p, _t) in demand}
    zones = [z for z in dataset.zones["zone_id"] if z in demanded]
    dist = dataset.zone_depot_distances.dropna(subset=["road_distance_km", "linear_distance_km"])
    dist = dist[dist["zone_id"].isin(demanded)]
    arcs, delivery_cost, road_km, linear_km, travel_min = [], {}, {}, {}, {}
    for r in dist.itertuples(index=False):
        key = (r.zone_id, r.depot_id)
        arcs.append(key)
        road_km[key] = float(r.road_distance_km)
        linear_km[key] = float(r.linear_distance_km)
        tt = getattr(r, "travel_time_min", None)
        travel_min[key] = None if tt is None or pd.isna(tt) else float(tt)
        delivery_cost[key] = road_km[key] * rate_for(linear_km[key]) * costs.round_trip_factor

    reachable = {c for c, _ in arcs}
    unreachable = sorted(demanded - reachable)
    if unreachable:
        raise ValueError(f"Zones with demand but no distance to any depot: {unreachable[:5]}")

    # ---- depot-depot transfers ------------------------------------------------ #
    transfer_cost: dict[tuple[str, str], float] = {}
    if not dataset.depot_depot_costs.empty:
        for r in dataset.depot_depot_costs.itertuples(index=False):
            if r.from_depot != r.to_depot:
                transfer_cost[(r.from_depot, r.to_depot)] = float(r.cost_per_unit)
    else:
        LOGGER.info("No depot-depot cost table: deriving transfer costs from the tariff.")
        for i in depot_ids:
            for j in depot_ids:
                if i == j:
                    continue
                lin = float(
                    haversine_km(
                        depots.at[i, "latitude"],
                        depots.at[i, "longitude"],
                        depots.at[j, "latitude"],
                        depots.at[j, "longitude"],
                    )
                )
                km = lin * tr.road_detour_factor if tr.transfer_distance_basis == "road" else lin
                transfer_cost[(i, j)] = km * rate_for(lin) * costs.round_trip_factor

    regions = sorted({r for r in depots.get("region", pd.Series(dtype=str)).dropna()})
    sets = ModelSets(
        depots=depot_ids,
        existing=existing,
        candidates=candidates,
        zones=zones,
        products=products,
        periods=periods,
        regions=regions,
        depot_region={
            d: (None if pd.isna(depots.at[d, "region"]) else depots.at[d, "region"])
            for d in depot_ids
        }
        if "region" in depots.columns
        else {},
        arcs=arcs,
        transfer_arcs=list(transfer_cost),
    )
    params = ModelParameters(
        rent={d: v * months for d, v in col("rent_cost_per_month").items()},
        fixed_operating={d: v * months for d, v in col("fixed_operating_cost_per_month").items()},
        opening_cost={d: v * amortise for d, v in col("opening_cost").items()},
        closing_cost={d: v * amortise for d, v in col("closing_cost").items()},
        expansion_cost=col("expansion_cost_per_batch"),
        delivery_cost=delivery_cost,
        transfer_cost=transfer_cost,
        demand=demand,
        initial_stock=initial_stock,
        returns=returns,
        inflows=inflows,
        capacity=capacity,
        road_km=road_km,
        linear_km=linear_km,
        travel_min=travel_min,
        horizon_months=months,
    )
    check_supply(params)
    return ModelData(sets=sets, params=params, period_dates=period_dates)


def check_supply(params: ModelParameters) -> dict[str, float]:
    """Warn about products whose total supply cannot cover demand.

    Such products can only be balanced through the (penalised) shortage
    variables, which usually signals a data problem rather than a network one.
    Returns the uncovered quantity per product.
    """
    need: dict[str, float] = {}
    for (_c, p, _t), q in params.demand.items():
        need[p] = need.get(p, 0.0) + q
    for (_d, p), q in params.initial_stock.items():
        need[p] = need.get(p, 0.0) - q
    for table in (params.returns, params.inflows):
        for (_d, p, _t), q in table.items():
            need[p] = need.get(p, 0.0) - q
    gaps = {p: gap for p, gap in need.items() if gap > 1e-9}
    for p, gap in gaps.items():
        LOGGER.warning("Product '%s': demand exceeds total supply by %.1f units.", p, gap)
    return gaps
