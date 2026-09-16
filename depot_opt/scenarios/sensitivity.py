"""One-at-a-time sensitivity analysis.

A sweep re-solves a scenario for a list of values of one lever:

* a configuration path, e.g. ``costs.round_trip_factor`` or
  ``constraints.open_depot_count.max_open`` (value replaces the setting);
* a data column, written ``data:<table>.<column>``, e.g.
  ``data:depots.rent_cost_per_month`` (value is a multiplier).
"""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from depot_opt.config import ScenarioConfig
from depot_opt.data.builder import load_dataset
from depot_opt.scenarios.runner import run_scenario, summarize
from depot_opt.schemas import NetworkDataset


def scale_column(dataset: NetworkDataset, table: str, column: str, factor: float) -> NetworkDataset:
    out = dataset.copy()
    frame = getattr(out, table)
    if column not in frame.columns:
        raise KeyError(f"Column '{column}' not found in table '{table}'.")
    frame[column] = frame[column] * factor
    return out


def sweep(
    cfg: ScenarioConfig,
    parameter: str,
    values: Iterable,
    dataset: NetworkDataset | None = None,
) -> pd.DataFrame:
    base = dataset if dataset is not None else load_dataset(cfg)
    rows = []
    for value in values:
        if parameter.startswith("data:"):
            table, column = parameter.removeprefix("data:").split(".", 1)
            run_ds, run_cfg = scale_column(base, table, column, float(value)), cfg
        else:
            run_ds, run_cfg = base, cfg.with_overrides({parameter: value})
        run_cfg = run_cfg.model_copy(update={"name": f"{cfg.name}__{parameter}={value}"})
        summary = summarize(run_scenario(run_cfg, run_ds, write=False))
        rows.append({"parameter": parameter, "value": value, **summary})
    return pd.DataFrame(rows)
