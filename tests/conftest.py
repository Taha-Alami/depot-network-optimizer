from datetime import date

import pandas as pd
import pytest

from depot_opt.config import ScenarioConfig, SyntheticDataConfig
from depot_opt.data.synthetic import generate_dataset
from depot_opt.geo.routing import HaversineRoutingProvider, build_distance_matrix
from depot_opt.schemas import NetworkDataset


@pytest.fixture
def tiny_dataset() -> NetworkDataset:
    """Two existing depots, one candidate, four zones, one product, two dates."""
    depots = pd.DataFrame(
        {
            "depot_id": ["depot_a", "depot_b", "cand_c"],
            "status": ["existing", "existing", "candidate"],
            "region": ["north", "south", "south"],
            "latitude": [10.0, 10.0, 10.5],
            "longitude": [10.0, 11.0, 10.5],
            "rent_cost_per_month": [1000.0, 1200.0, 900.0],
            "fixed_operating_cost_per_month": [100.0, 100.0, 100.0],
            "opening_cost": [0.0, 0.0, 6000.0],
            "closing_cost": [3000.0, 3000.0, 0.0],
            "capacity_units": [100.0, 100.0, 100.0],
            "expansion_cost_per_batch": [50.0, 50.0, 50.0],
        }
    )
    zones = pd.DataFrame(
        {
            "zone_id": ["z1", "z2", "z3", "z4"],
            "region": ["north", "north", "south", "south"],
            "latitude": [10.05, 9.95, 10.05, 10.45],
            "longitude": [10.05, 9.95, 11.05, 10.45],
        }
    )
    d1, d2 = date(2025, 1, 10), date(2025, 2, 10)
    demand = pd.DataFrame(
        [
            {"zone_id": z, "product_id": "p1", "period": d, "quantity": 5}
            for z in zones["zone_id"]
            for d in (d1, d2)
        ]
    )
    inventory = pd.DataFrame(
        [
            {"depot_id": "depot_a", "product_id": "p1", "quantity": 30},
            {"depot_id": "depot_b", "product_id": "p1", "quantity": 30},
        ]
    )
    returns = pd.DataFrame(
        [{"depot_id": "depot_a", "product_id": "p1", "period": d2, "quantity": 4}]
    )
    rates = pd.DataFrame(
        [
            {"band": b, "carrier_option": o, "rate_per_unit_km": r}
            for b in range(1, 13)
            for o, r in (("own_fleet", 0.5), ("carrier", 0.7))
        ]
    )
    ds = NetworkDataset(
        depots=depots,
        zones=zones,
        demand=demand,
        inventory=inventory,
        zone_depot_distances=build_distance_matrix(zones, depots, HaversineRoutingProvider()),
        transport_rates=rates,
        returns=returns,
    )
    ds.validate()
    return ds


@pytest.fixture
def cfg() -> ScenarioConfig:
    return ScenarioConfig(name="test", solver={"time_limit_s": 60, "mip_gap": 0.0})


@pytest.fixture(scope="session")
def synthetic_dataset() -> NetworkDataset:
    return generate_dataset(SyntheticDataConfig(n_zones=15, n_months=2, delivery_days_per_month=2))
