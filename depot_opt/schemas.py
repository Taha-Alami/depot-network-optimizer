"""Input schemas.

Every table the model consumes is described by a pydantic record. The
records document field meaning and units, and `NetworkDataset.validate()`
checks that the tabular inputs (pandas DataFrames) conform to them.

Units convention
----------------
* money: any single currency (called "CU", currency units)
* distances: kilometres
* times: minutes
* quantities: units of equipment (one "unit" = one stored/shipped item)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field, ValidationError


class DepotRecord(BaseModel):
    """A service depot, either already operating or a candidate site."""

    depot_id: str = Field(description="Unique depot identifier, e.g. 'depot_01'.")
    status: Literal["existing", "candidate"] = Field(
        description="'existing' depots can be kept or closed; 'candidate' sites can be opened."
    )
    region: str | None = Field(default=None, description="Region label used for coverage rules.")
    latitude: float = Field(ge=-90, le=90, description="WGS84 latitude in degrees.")
    longitude: float = Field(ge=-180, le=180, description="WGS84 longitude in degrees.")
    rent_cost_per_month: float = Field(ge=0, description="Lease/rent while open [CU/month].")
    fixed_operating_cost_per_month: float = Field(
        default=0.0,
        ge=0,
        description="Maintenance and other fixed site costs while open [CU/month].",
    )
    opening_cost: float = Field(
        default=0.0, ge=0, description="One-off cost to open a candidate site [CU]."
    )
    closing_cost: float = Field(
        default=0.0, ge=0, description="One-off cost to close an existing depot [CU]."
    )
    capacity_units: float | None = Field(
        default=None, ge=0, description="Storage capacity [units]; empty means unlimited."
    )
    expansion_cost_per_batch: float = Field(
        default=0.0, ge=0, description="Cost of one block of extra storage capacity [CU/batch]."
    )


class CustomerZoneRecord(BaseModel):
    """An aggregated customer demand zone (e.g. a cluster of delivery sites)."""

    zone_id: str = Field(description="Unique zone identifier, e.g. 'zone_001'.")
    region: str | None = Field(default=None, description="Region label.")
    latitude: float = Field(ge=-90, le=90, description="Zone centroid latitude.")
    longitude: float = Field(ge=-180, le=180, description="Zone centroid longitude.")


class DemandRecord(BaseModel):
    """Units of a product type delivered to a zone on a given date."""

    zone_id: str
    product_id: str = Field(description="Product / equipment type identifier.")
    period: date = Field(description="Delivery date (the model's time bucket).")
    quantity: float = Field(ge=0, description="Units to deliver [units].")


class InventoryRecord(BaseModel):
    """Stock available at a depot at the start of the planning horizon."""

    depot_id: str
    product_id: str
    quantity: float = Field(ge=0, description="Units in stock and not at a customer site.")


class ReturnRecord(BaseModel):
    """Units coming back from customers into a depot (exogenous inflow)."""

    depot_id: str
    product_id: str
    period: date
    quantity: float = Field(ge=0, description="Returned units [units].")


class ExternalInflowRecord(BaseModel):
    """Other exogenous arrivals at a depot (e.g. from a central refurbishment hub)."""

    depot_id: str
    product_id: str
    period: date
    quantity: float = Field(ge=0, description="Arriving units [units].")


class ZoneDepotDistanceRecord(BaseModel):
    """Travel metrics between a customer zone and a depot."""

    zone_id: str
    depot_id: str
    road_distance_km: float = Field(ge=0, description="Road distance (routing engine) [km].")
    travel_time_min: float | None = Field(default=None, ge=0, description="Road travel time [min].")
    linear_distance_km: float = Field(ge=0, description="Great-circle distance [km].")


class DepotDepotCostRecord(BaseModel):
    """Cost of repositioning one unit between two depots."""

    from_depot: str
    to_depot: str
    cost_per_unit: float = Field(ge=0, description="Transfer cost [CU/unit].")


class TransportRateRecord(BaseModel):
    """Freight tariff: rate per unit-kilometre for a distance band and carrier option."""

    band: int = Field(ge=1, description="Distance band index (1 = closest).")
    carrier_option: str = Field(
        description="Carrier/load option, e.g. 'own_fleet_single' or 'carrier_double'."
    )
    rate_per_unit_km: float = Field(ge=0, description="Tariff [CU/(unit*km)].")


TABLE_SCHEMAS: dict[str, type[BaseModel]] = {
    "depots": DepotRecord,
    "zones": CustomerZoneRecord,
    "demand": DemandRecord,
    "inventory": InventoryRecord,
    "returns": ReturnRecord,
    "inflows": ExternalInflowRecord,
    "zone_depot_distances": ZoneDepotDistanceRecord,
    "depot_depot_costs": DepotDepotCostRecord,
    "transport_rates": TransportRateRecord,
}

OPTIONAL_TABLES = {"returns", "inflows", "depot_depot_costs"}


def empty_frame(table: str) -> pd.DataFrame:
    """Return an empty DataFrame with the columns of `table`."""
    return pd.DataFrame(columns=list(TABLE_SCHEMAS[table].model_fields))


@dataclass
class NetworkDataset:
    """All input tables for one optimisation run."""

    depots: pd.DataFrame
    zones: pd.DataFrame
    demand: pd.DataFrame
    inventory: pd.DataFrame
    zone_depot_distances: pd.DataFrame
    transport_rates: pd.DataFrame
    returns: pd.DataFrame = field(default_factory=lambda: empty_frame("returns"))
    inflows: pd.DataFrame = field(default_factory=lambda: empty_frame("inflows"))
    depot_depot_costs: pd.DataFrame = field(
        default_factory=lambda: empty_frame("depot_depot_costs")
    )

    def tables(self) -> dict[str, pd.DataFrame]:
        return {name: getattr(self, name) for name in TABLE_SCHEMAS}

    def copy(self) -> NetworkDataset:
        return NetworkDataset(**{k: v.copy() for k, v in self.tables().items()})

    def validate(self) -> None:
        """Validate every row against its schema and check referential integrity."""
        for name, frame in self.tables().items():
            schema = TABLE_SCHEMAS[name]
            missing = [
                col
                for col, info in schema.model_fields.items()
                if info.is_required() and col not in frame.columns
            ]
            if missing:
                raise ValueError(f"Table '{name}' is missing required columns: {missing}")
            records = frame.astype(object).where(frame.notna(), None).to_dict("records")
            for i, row in enumerate(records):
                try:
                    schema.model_validate(row)
                except ValidationError as exc:
                    raise ValueError(f"Table '{name}', row {i} is invalid:\n{exc}") from exc

        depot_ids = set(self.depots["depot_id"])
        zone_ids = set(self.zones["zone_id"])
        if len(depot_ids) != len(self.depots):
            raise ValueError("Duplicate depot_id values in 'depots'.")
        if len(zone_ids) != len(self.zones):
            raise ValueError("Duplicate zone_id values in 'zones'.")
        _check_refs("demand", self.demand, "zone_id", zone_ids)
        for name in ("inventory", "returns", "inflows"):
            _check_refs(name, getattr(self, name), "depot_id", depot_ids)
        _check_refs("zone_depot_distances", self.zone_depot_distances, "zone_id", zone_ids)
        _check_refs("zone_depot_distances", self.zone_depot_distances, "depot_id", depot_ids)
        _check_refs("depot_depot_costs", self.depot_depot_costs, "from_depot", depot_ids)
        _check_refs("depot_depot_costs", self.depot_depot_costs, "to_depot", depot_ids)


def _check_refs(table: str, frame: pd.DataFrame, column: str, valid: set[str]) -> None:
    if frame.empty:
        return
    unknown = set(frame[column]) - valid
    if unknown:
        sample = sorted(map(str, unknown))[:5]
        raise ValueError(f"Table '{table}' references unknown {column} values: {sample}")
