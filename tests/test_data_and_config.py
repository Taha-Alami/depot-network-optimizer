from pathlib import Path

import pandas as pd
import pytest

from depot_opt.config import DatabaseSettings, ScenarioConfig, TransportConfig, load_scenario
from depot_opt.data.io import read_csv, write_csv
from depot_opt.data.odbc_loader import OdbcLoader
from depot_opt.geo.distance import blended_rates, distance_band, filter_to_bbox, haversine_km

ROOT = Path(__file__).resolve().parents[1]


def test_haversine_one_degree_latitude():
    assert haversine_km(0, 0, 1, 0) == pytest.approx(111.2, abs=0.2)


@pytest.mark.parametrize(("km", "band"), [(0, 1), (24.9, 1), (25, 2), (299, 12), (5000, 12)])
def test_distance_band(km, band):
    assert distance_band(km, 25, 12) == band


def test_blended_rates():
    rates = pd.DataFrame(
        [
            {"band": 1, "carrier_option": "a", "rate_per_unit_km": 1.0},
            {"band": 1, "carrier_option": "b", "rate_per_unit_km": 3.0},
        ]
    )
    assert blended_rates(rates, {"a": 0.25, "b": 0.75}) == {1: pytest.approx(2.5)}
    with pytest.raises(ValueError):
        blended_rates(rates, {"a": 0.5, "c": 0.5})


def test_filter_to_bbox():
    df = pd.DataFrame({"latitude": [0.5, 5.0], "longitude": [0.5, 0.5]})
    assert len(filter_to_bbox(df, (0, 1, 0, 1))) == 1


def test_carrier_mix_must_sum_to_one():
    with pytest.raises(ValueError):
        TransportConfig(carrier_mix={"a": 0.5, "b": 0.2})


def test_all_scenario_files_load():
    files = sorted((ROOT / "configs" / "scenarios").glob("*.yaml"))
    assert files
    for f in files:
        cfg = load_scenario(f)
        assert cfg.name == f.stem


def test_extends_merges_nested_keys():
    cfg = load_scenario(ROOT / "configs" / "scenarios" / "multi_period.yaml")
    assert cfg.horizon.mode == "rolling"
    assert cfg.constraints.capacity.mode == "soft"
    assert cfg.constraints.capacity.enabled is True  # inherited from base
    assert cfg.transport.carrier_mix == {"own_fleet": 0.6, "carrier": 0.4}


def test_overrides_reject_unknown_paths():
    with pytest.raises(KeyError):
        ScenarioConfig().with_overrides({"costs.does_not_exist": 1})


def test_csv_roundtrip(tmp_path, tiny_dataset):
    write_csv(tiny_dataset, tmp_path)
    loaded = read_csv(tmp_path)
    assert len(loaded.demand) == len(tiny_dataset.demand)
    assert loaded.depots["depot_id"].tolist() == tiny_dataset.depots["depot_id"].tolist()


def test_validation_catches_unknown_references(tiny_dataset):
    ds = tiny_dataset.copy()
    ds.inventory.loc[0, "depot_id"] = "ghost"
    with pytest.raises(ValueError, match="unknown depot_id"):
        ds.validate()


def test_odbc_connection_string_from_settings():
    dummy = "not-a-real-pw"  # pragma: allowlist secret
    settings = DatabaseSettings(
        server="db.example.com", port="1433", database="demo", username="user", password=dummy
    )
    conn = OdbcLoader(settings).connection_string()
    assert "SERVER=db.example.com,1433;" in conn
    assert "UID=user;" in conn
    assert dummy not in repr(settings)


def test_odbc_settings_reject_bad_table_names(monkeypatch):
    monkeypatch.setenv("DEPOT_TABLE", "x; DROP TABLE y")
    with pytest.raises(ValueError):
        DatabaseSettings.from_env(dotenv=False)


def test_read_table_validates_identifiers():
    with pytest.raises(ValueError):
        OdbcLoader(DatabaseSettings()).read_table("bad name")
