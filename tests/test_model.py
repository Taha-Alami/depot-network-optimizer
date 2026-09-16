import pytest

from depot_opt.data.builder import build_model_data
from depot_opt.model.builder import DepotNetworkModel
from depot_opt.scenarios.runner import run_scenario


def solve(dataset, cfg, **overrides):
    cfg = cfg.with_overrides(overrides) if overrides else cfg
    model = DepotNetworkModel(build_model_data(dataset, cfg), cfg).build()
    return model, model.solve()


def test_every_zone_served_exactly_once(tiny_dataset, cfg):
    _, rep = solve(tiny_dataset, cfg)
    assert rep.status == "Optimal"
    shares = rep.assignments.groupby("zone_id")["share"].sum()
    assert set(shares.index) == {"z1", "z2", "z3", "z4"}
    assert (shares.round(6) == 1).all()
    assert (rep.assignments["share"].round(6) == 1).all()  # single sourcing


def test_assignments_only_to_open_depots(tiny_dataset, cfg):
    _, rep = solve(tiny_dataset, cfg)
    open_ids = set(rep.depot_decisions.query("open == 1")["depot_id"])
    assert set(rep.assignments["depot_id"]) <= open_ids


def test_cost_components_add_up_to_objective(tiny_dataset, cfg):
    _, rep = solve(tiny_dataset, cfg)
    costs = rep.cost_breakdown.set_index("component")["cost"]
    parts = costs.drop("total_excl_penalty")
    assert parts.sum() == pytest.approx(rep.objective, rel=1e-6)


def test_forced_closure_removes_depot_and_its_stock(tiny_dataset, cfg):
    _, rep = solve(
        tiny_dataset,
        cfg,
        **{
            "constraints.forced_status": {
                "enabled": True,
                "must_open": [],
                "must_close": ["depot_a"],
                "others": "free",
            }
        },
    )
    assert rep.status == "Optimal"
    row = rep.depot_decisions.set_index("depot_id").loc["depot_a"]
    assert row["open"] == 0 and row["action"] == "close"
    assert "depot_a" not in set(rep.assignments["depot_id"])
    assert "depot_a" not in set(rep.stock["depot_id"])
    # Initial stock and returns of the closed depot are moved out.
    moved = rep.transfers.query("from_depot == 'depot_a'")["quantity"].sum()
    assert moved == pytest.approx(30 + 4)
    closing = rep.cost_breakdown.set_index("component").loc["closing", "cost"]
    assert closing > 0


def test_hard_capacity_is_respected(tiny_dataset, cfg):
    overrides = {"constraints.capacity.overrides": {"depot_a": 12.0, "depot_b": 100.0}}
    _, rep = solve(tiny_dataset, cfg, **overrides)
    assert rep.status == "Optimal"
    peak = rep.stock.query("depot_id == 'depot_a'").groupby("period")["quantity"].sum()
    assert (peak <= 12 + 1e-6).all()


def test_soft_capacity_buys_expansion_batches(tiny_dataset, cfg):
    overrides = {
        "constraints.capacity.mode": "soft",
        "constraints.capacity.expansion_batch_size": 4.0,
        "constraints.capacity.overrides": {"depot_a": 0.0, "depot_b": 0.0, "cand_c": 0.0},
        "constraints.forced_status": {
            "enabled": True,
            "must_open": ["depot_a", "depot_b"],
            "must_close": [],
            "others": "free",
        },
    }
    _, rep = solve(tiny_dataset, cfg, **overrides)
    assert rep.status == "Optimal"
    exp = rep.expansion.set_index("depot_id")
    peak = rep.stock.groupby(["depot_id", "period"])["quantity"].sum().groupby("depot_id").max()
    for depot, stock in peak.items():
        assert exp.loc[depot, "excess"] >= stock - 1e-6
        assert exp.loc[depot, "batches"] * 4 >= exp.loc[depot, "excess"] - 1e-6


def test_max_service_distance(tiny_dataset, cfg):
    limit = 40.0
    model, rep = solve(
        tiny_dataset,
        cfg,
        **{
            "constraints.max_service_distance": {
                "enabled": True,
                "max_distance_km": limit,
                "max_travel_time_min": None,
                "distance_basis": "road",
            }
        },
    )
    assert rep.status == "Optimal"
    assert (rep.assignments["road_km"] <= limit).all()


def test_max_service_distance_without_admissible_depot_raises(tiny_dataset, cfg):
    with pytest.raises(ValueError, match="no depot within the service limit"):
        solve(
            tiny_dataset,
            cfg,
            **{
                "constraints.max_service_distance": {
                    "enabled": True,
                    "max_distance_km": 1.0,
                    "max_travel_time_min": None,
                    "distance_basis": "road",
                }
            },
        )


def test_open_depot_count_bounds(tiny_dataset, cfg):
    _, rep = solve(
        tiny_dataset,
        cfg,
        **{
            "constraints.open_depot_count": {
                "enabled": True,
                "min_open": 3,
                "max_open": 3,
                "max_new_sites": None,
                "max_closures": None,
            }
        },
    )
    assert rep.depot_decisions["open"].sum() == 3
    assert "open" in set(rep.depot_decisions["action"])


def test_max_new_sites_zero_blocks_candidates(tiny_dataset, cfg):
    _, rep = solve(
        tiny_dataset,
        cfg,
        **{
            "constraints.open_depot_count": {
                "enabled": True,
                "min_open": None,
                "max_open": None,
                "max_new_sites": 0,
                "max_closures": None,
            }
        },
    )
    assert rep.depot_decisions.set_index("depot_id").loc["cand_c", "open"] == 0


def test_regional_coverage(tiny_dataset, cfg):
    _, rep = solve(
        tiny_dataset,
        cfg,
        **{
            "constraints.regional_coverage": {
                "enabled": True,
                "min_open_per_region": 1,
                "per_region": {"south": 2},
            }
        },
    )
    d = rep.depot_decisions.set_index("depot_id")["open"]
    assert d["depot_a"] == 1
    assert d["depot_b"] + d["cand_c"] == 2


def test_split_assignment_is_continuous(tiny_dataset, cfg):
    model, rep = solve(tiny_dataset, cfg, **{"constraints.single_sourcing.enabled": False})
    assert rep.status == "Optimal"
    assert all(not v.cat == "Integer" for v in model.variables.assign.values())


def test_rolling_horizon_conserves_stock(tiny_dataset, cfg):
    cfg = cfg.with_overrides({"horizon.mode": "rolling", "output_dir": "unused"})
    rep = run_scenario(cfg, tiny_dataset, write=False)
    assert rep.status == "Optimal"
    assert list(rep.cost_breakdown["window"].unique()) == ["2025-01", "2025-02"]
    # initial 60 + returns 4 - demand 40 + shortage = ending stock
    ending = sum(rep.ending_stock.values())
    short = rep.shortages["quantity"].sum()
    assert ending == pytest.approx(60 + 4 - 40 + short)


def test_throughput_capacity_without_inventory(tiny_dataset, cfg):
    _, rep = solve(
        tiny_dataset,
        cfg,
        **{
            "constraints.inventory_balance.enabled": False,
            "constraints.capacity.overrides": {"depot_a": 10.0},
        },
    )
    assert rep.status == "Optimal"
    assert rep.stock.empty
    vol = rep.assignments.query("depot_id == 'depot_a'")["volume"].sum()
    assert vol <= 2 * 10 + 1e-6  # aggregate mode: one period holding both months


def test_synthetic_end_to_end_all_modes(synthetic_dataset, cfg):
    for mode in ("aggregate", "full", "rolling"):
        rep = run_scenario(
            cfg.with_overrides({"horizon.mode": mode}), synthetic_dataset, write=False
        )
        assert rep.status == "Optimal", mode
        assert rep.shortages.empty, mode
