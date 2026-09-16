"""Solver factory (CBC by default, HiGHS or Gurobi optionally)."""

from __future__ import annotations

import warnings

import pulp

from depot_opt.config import SolverConfig


def get_solver(cfg: SolverConfig) -> pulp.LpSolver:
    """Instantiate the configured MILP solver and check that it is available."""
    common = {"msg": cfg.msg, "timeLimit": cfg.time_limit_s}
    if cfg.name == "cbc":
        solver = pulp.COIN_CMD(**common, gapRel=cfg.mip_gap, threads=cfg.threads)
        if not solver.available():
            # Fall back to the CBC binary bundled with PuLP.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                solver = pulp.PULP_CBC_CMD(**common, gapRel=cfg.mip_gap, threads=cfg.threads)
    elif cfg.name == "highs":
        solver = pulp.HiGHS(**common, gapRel=cfg.mip_gap, threads=cfg.threads)
    elif cfg.name == "gurobi":
        params = {}
        if cfg.mip_gap is not None:
            params["MIPGap"] = cfg.mip_gap
        if cfg.threads is not None:
            params["Threads"] = cfg.threads
        solver = pulp.GUROBI(**common, **params)
    else:  # pragma: no cover - guarded by pydantic
        raise ValueError(f"Unknown solver: {cfg.name}")

    if not solver.available():
        hint = {"highs": "pip install highspy", "gurobi": "pip install gurobipy (licence required)"}
        raise RuntimeError(
            f"Solver '{cfg.name}' is not available. {hint.get(cfg.name, '')}".strip()
        )
    return solver
