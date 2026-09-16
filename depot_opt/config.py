"""Configuration.

Two kinds of configuration exist:

* `DatabaseSettings` - connection details and table names, read **only** from
  environment variables (optionally loaded from a local, git-ignored `.env`).
* `ScenarioConfig` - everything that defines a modelling scenario, read from
  YAML. A scenario file may `extends:` another file (paths are relative to the
  extending file); mappings are deep-merged, the child wins.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------------------- #
# Environment-based settings
# --------------------------------------------------------------------------- #

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}$")

TABLE_ENV_VARS = {
    "depots": "DEPOT_TABLE",
    "zones": "ZONE_TABLE",
    "demand": "DEMAND_TABLE",
    "inventory": "INVENTORY_TABLE",
    "returns": "RETURNS_TABLE",
    "inflows": "INFLOW_TABLE",
    "zone_depot_distances": "ZONE_DEPOT_DISTANCE_TABLE",
    "depot_depot_costs": "DEPOT_DEPOT_COST_TABLE",
    "transport_rates": "TRANSPORT_RATE_TABLE",
}


class DatabaseSettings(BaseModel):
    """ODBC connection parameters, sourced from environment variables."""

    driver: str = "ODBC Driver 18 for SQL Server"
    server: str | None = None
    port: str | None = None
    database: str | None = None
    trusted_connection: bool = False
    username: str | None = None
    password: str | None = Field(default=None, repr=False)
    encrypt: bool = True
    tables: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_env(cls, dotenv: bool = True) -> DatabaseSettings:
        if dotenv:
            load_dotenv()

        def flag(name: str, default: str) -> bool:
            return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}

        tables = {key: os.environ[var] for key, var in TABLE_ENV_VARS.items() if os.getenv(var)}
        for key, name in tables.items():
            if not _IDENTIFIER.match(name):
                raise ValueError(f"Environment table name for '{key}' is not a valid identifier.")
        return cls(
            driver=os.getenv("DB_DRIVER", cls.model_fields["driver"].default),
            server=os.getenv("DB_SERVER"),
            port=os.getenv("DB_PORT"),
            database=os.getenv("DB_DATABASE"),
            trusted_connection=flag("DB_TRUSTED_CONNECTION", "no"),
            username=os.getenv("DB_USERNAME"),
            password=os.getenv("DB_PASSWORD"),
            encrypt=flag("DB_ENCRYPT", "yes"),
            tables=tables,
        )


# --------------------------------------------------------------------------- #
# Scenario configuration
# --------------------------------------------------------------------------- #


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SyntheticDataConfig(_Strict):
    n_existing_depots: int = 6
    n_candidate_depots: int = 2
    n_zones: int = 30
    n_products: int = 3
    n_regions: int = 3
    start_date: str = "2025-01-01"
    n_months: int = 2
    delivery_days_per_month: int = 4
    seed: int = 7
    bounding_box: tuple[float, float, float, float] = Field(
        default=(40.0, 44.0, -2.0, 4.0),
        description="(min_lat, max_lat, min_lon, max_lon) of the synthetic service area.",
    )


class DataConfig(_Strict):
    source: Literal["synthetic", "csv", "odbc"] = "synthetic"
    csv_dir: str | None = None
    synthetic: SyntheticDataConfig = SyntheticDataConfig()
    start_date: str | None = Field(default=None, description="Optional horizon filter (inclusive).")
    end_date: str | None = Field(default=None, description="Optional horizon filter (inclusive).")


class HorizonConfig(_Strict):
    mode: Literal["aggregate", "full", "rolling"] = Field(
        default="aggregate",
        description=(
            "aggregate: collapse all dates into one bucket (strategic, static view); "
            "full: one model over all dates; "
            "rolling: one model per window, ending stock carried to the next window."
        ),
    )
    window: Literal["M", "Q"] = Field(default="M", description="Rolling window: month or quarter.")
    stop_on_non_optimal: bool = True


class CostConfig(_Strict):
    include_rent: bool = True
    include_fixed_operating: bool = True
    include_opening: bool = True
    include_closing: bool = True
    include_delivery: bool = True
    include_transfer: bool = True
    include_expansion: bool = True
    round_trip_factor: float = Field(
        default=1.0,
        ge=0,
        description="Multiplier on distance-based costs (2.0 = empty return leg).",
    )
    shortage_penalty: float = Field(
        default=1e6, ge=0, description="Penalty per unit of unmet demand."
    )
    one_off_amortisation_months: float = Field(
        default=60.0,
        gt=0,
        description="Opening/closing costs are spread over this many months in the objective.",
    )
    horizon_months: float | None = Field(
        default=None,
        gt=0,
        description="Months represented by one model; inferred from the data when empty.",
    )


class TransportConfig(_Strict):
    band_width_km: float = Field(default=25.0, gt=0, description="Width of one distance band.")
    max_band: int = Field(default=12, ge=1, description="All longer distances fall into this band.")
    carrier_mix: dict[str, float] = Field(
        default_factory=lambda: {"own_fleet": 0.5, "carrier": 0.5},
        description="Share of volume per carrier option; used to blend tariffs.",
    )
    road_detour_factor: float = Field(
        default=1.3, ge=1.0, description="Road/linear ratio used when road distances are unknown."
    )
    transfer_distance_basis: Literal["road", "linear"] = "road"

    @model_validator(mode="after")
    def _mix_sums_to_one(self) -> TransportConfig:
        total = sum(self.carrier_mix.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"transport.carrier_mix must sum to 1.0 (got {total:.4f}).")
        return self


class ToggleConfig(_Strict):
    enabled: bool = True


class SingleSourcingConfig(ToggleConfig):
    """enabled=True: binary assignment; False: fractional (split) assignment."""


class InventoryBalanceConfig(ToggleConfig):
    allow_shortage: bool = True
    allow_transfers: bool = True
    integer_quantities: bool = True


class StockOnlyIfOpenConfig(ToggleConfig):
    big_m: float | None = Field(default=None, description="Override the data-derived big-M.")


class CapacityConfig(ToggleConfig):
    mode: Literal["hard", "soft"] = Field(
        default="hard", description="hard: stock <= capacity; soft: overflow bought in batches."
    )
    expansion_batch_size: float = Field(
        default=10.0, gt=0, description="Units per expansion batch."
    )
    overrides: dict[str, float | None] = Field(
        default_factory=dict, description="Per-depot capacity override; null = unlimited."
    )


class MaxServiceDistanceConfig(ToggleConfig):
    enabled: bool = False
    max_distance_km: float | None = None
    max_travel_time_min: float | None = None
    distance_basis: Literal["road", "linear"] = "road"


class OpenDepotCountConfig(ToggleConfig):
    enabled: bool = False
    min_open: int | None = None
    max_open: int | None = None
    max_new_sites: int | None = None
    max_closures: int | None = None


class ForcedStatusConfig(ToggleConfig):
    enabled: bool = False
    must_open: list[str] = Field(default_factory=list)
    must_close: list[str] = Field(default_factory=list)
    others: Literal["free", "open", "closed"] = Field(
        default="free", description="Status imposed on depots not listed above."
    )

    @model_validator(mode="after")
    def _disjoint(self) -> ForcedStatusConfig:
        clash = set(self.must_open) & set(self.must_close)
        if clash:
            raise ValueError(f"Depots listed as both must_open and must_close: {sorted(clash)}")
        return self


class RegionalCoverageConfig(ToggleConfig):
    enabled: bool = False
    min_open_per_region: int = 1
    per_region: dict[str, int] = Field(default_factory=dict, description="Region-specific minimum.")


class ConstraintsConfig(_Strict):
    single_sourcing: SingleSourcingConfig = SingleSourcingConfig()
    assign_only_if_open: ToggleConfig = ToggleConfig()
    inventory_balance: InventoryBalanceConfig = InventoryBalanceConfig()
    stock_only_if_open: StockOnlyIfOpenConfig = StockOnlyIfOpenConfig()
    capacity: CapacityConfig = CapacityConfig()
    max_service_distance: MaxServiceDistanceConfig = MaxServiceDistanceConfig()
    open_depot_count: OpenDepotCountConfig = OpenDepotCountConfig()
    forced_status: ForcedStatusConfig = ForcedStatusConfig()
    regional_coverage: RegionalCoverageConfig = RegionalCoverageConfig()


class SolverConfig(_Strict):
    name: Literal["cbc", "highs", "gurobi"] = "cbc"
    time_limit_s: float | None = 300
    mip_gap: float | None = 0.01
    threads: int | None = None
    msg: bool = False


class ScenarioConfig(_Strict):
    name: str = "baseline"
    description: str = ""
    data: DataConfig = DataConfig()
    horizon: HorizonConfig = HorizonConfig()
    costs: CostConfig = CostConfig()
    transport: TransportConfig = TransportConfig()
    constraints: ConstraintsConfig = ConstraintsConfig()
    solver: SolverConfig = SolverConfig()
    output_dir: str = "outputs"

    def with_overrides(self, overrides: dict[str, Any]) -> ScenarioConfig:
        """Return a copy with dotted-path overrides applied, e.g. {'costs.round_trip_factor': 2}."""
        raw = self.model_dump()
        for path, value in overrides.items():
            node = raw
            *parents, leaf = path.split(".")
            for key in parents:
                node = node[key]
            if leaf not in node:
                raise KeyError(f"Unknown configuration path: {path}")
            node[leaf] = value
        return ScenarioConfig.model_validate(raw)


def _deep_merge(base: dict, child: dict) -> dict:
    merged = dict(base)
    for key, value in child.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml(path: Path) -> dict:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    parent = raw.pop("extends", None)
    if parent:
        raw = _deep_merge(_load_yaml((path.parent / parent).resolve()), raw)
    return raw


def load_scenario(path: str | Path) -> ScenarioConfig:
    """Load a scenario YAML file (resolving `extends:`) into a validated config."""
    return ScenarioConfig.model_validate(_load_yaml(Path(path)))
