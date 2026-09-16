"""Scenario execution: single solve, rolling horizon and scenario comparison."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from depot_opt.config import ScenarioConfig
from depot_opt.data.builder import build_model_data, load_dataset, split_into_windows
from depot_opt.model.builder import DepotNetworkModel
from depot_opt.model.results import SolutionReport
from depot_opt.schemas import NetworkDataset

LOGGER = logging.getLogger(__name__)

SOLVED = {"Optimal"}


def run_scenario(
    cfg: ScenarioConfig,
    dataset: NetworkDataset | None = None,
    write: bool = True,
) -> SolutionReport:
    """Solve one scenario according to its horizon mode and optionally write CSV outputs."""
    dataset = dataset if dataset is not None else load_dataset(cfg)

    if cfg.horizon.mode in ("aggregate", "full"):
        data = build_model_data(dataset, cfg)
        report = DepotNetworkModel(data, cfg).solve().tag(scenario=cfg.name, window="all")
    else:
        reports: list[SolutionReport] = []
        stock = None
        for label, window_ds in split_into_windows(dataset, cfg.horizon.window):
            data = build_model_data(window_ds, cfg, initial_stock=stock, aggregate=False)
            rep = DepotNetworkModel(data, cfg, name=f"{cfg.name}_{label}").solve()
            reports.append(rep.tag(scenario=cfg.name, window=label))
            if rep.status not in SOLVED:
                LOGGER.warning("Window %s finished with status %s.", label, rep.status)
                if cfg.horizon.stop_on_non_optimal:
                    break
            # Carry the ending stock into the next window.
            stock = rep.ending_stock or None
        report = SolutionReport.concat(reports)

    if write:
        out = report.to_csv(Path(cfg.output_dir) / cfg.name)
        LOGGER.info("Results written to %s", out)
    return report


def summarize(report: SolutionReport) -> dict:
    """One-line KPI summary of a (possibly multi-window) solution."""
    costs = report.cost_breakdown.groupby("component")["cost"].sum().to_dict()
    decisions = report.depot_decisions
    # In rolling mode a depot counts as open if it is open in any window.
    last = decisions.groupby("depot_id")["action"].agg(lambda s: s.iloc[-1])
    return {
        "scenario": report.depot_decisions["scenario"].iloc[0] if "scenario" in decisions else None,
        "status": report.status,
        "objective": report.objective,
        **{f"cost_{k}": v for k, v in costs.items()},
        "depots_kept": int((last == "keep").sum()),
        "depots_closed": int((last == "close").sum()),
        "sites_opened": int((last == "open").sum()),
        "units_transferred": float(report.transfers["quantity"].sum()),
        "units_short": float(report.shortages["quantity"].sum()),
        "expansion_batches": float(report.expansion["batches"].sum()),
    }


def compare_scenarios(
    configs: list[ScenarioConfig],
    dataset: NetworkDataset | None = None,
    write: bool = False,
) -> pd.DataFrame:
    """Run several scenarios on the same dataset and tabulate their KPIs."""
    if dataset is None:
        dataset = load_dataset(configs[0])
    rows = [summarize(run_scenario(cfg, dataset, write=write)) for cfg in configs]
    return pd.DataFrame(rows)
