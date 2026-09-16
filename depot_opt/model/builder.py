"""High-level model object: build, solve, report."""

from __future__ import annotations

import logging
import time

import pulp

from depot_opt.config import ScenarioConfig
from depot_opt.model.constraints import add_constraints
from depot_opt.model.data import ModelData
from depot_opt.model.objective import build_cost_terms
from depot_opt.model.results import SolutionReport, extract_results
from depot_opt.model.solver import get_solver
from depot_opt.model.variables import DecisionVariables, create_variables

LOGGER = logging.getLogger(__name__)


class DepotNetworkModel:
    """Depot network design MILP.

    Example:
        >>> model = DepotNetworkModel(model_data, config).build()
        >>> report = model.solve()
    """

    def __init__(self, data: ModelData, config: ScenarioConfig, name: str | None = None):
        self.data = data
        self.config = config
        self.name = name or f"depot_network_{config.name}"
        self.problem: pulp.LpProblem | None = None
        self.variables: DecisionVariables | None = None
        self.cost_terms: dict[str, pulp.LpAffineExpression] = {}
        self.constraint_counts: dict[str, int] = {}

    def build(self) -> DepotNetworkModel:
        t0 = time.perf_counter()
        self.problem = pulp.LpProblem(self.name, pulp.LpMinimize)
        self.variables = create_variables(self.problem, self.data, self.config.constraints)
        self.cost_terms = build_cost_terms(self.data, self.variables, self.config.costs)
        self.problem += pulp.lpSum(self.cost_terms.values()), "total_cost"
        self.constraint_counts = add_constraints(
            self.problem, self.data, self.variables, self.config.constraints
        )
        LOGGER.info(
            "Built '%s': %d variables, %d constraints in %.2fs %s",
            self.name,
            self.problem.numVariables(),
            self.problem.numConstraints(),
            time.perf_counter() - t0,
            self.constraint_counts,
        )
        return self

    def solve(self) -> SolutionReport:
        if self.problem is None:
            self.build()
        t0 = time.perf_counter()
        self.problem.solve(get_solver(self.config.solver))
        LOGGER.info(
            "Solved '%s' -> %s in %.2fs",
            self.name,
            pulp.LpStatus[self.problem.status],
            time.perf_counter() - t0,
        )
        return extract_results(self.problem, self.data, self.variables, self.cost_terms)

    def write_lp(self, path: str) -> None:
        """Export the model in LP format for inspection or an external solver."""
        if self.problem is None:
            self.build()
        self.problem.writeLP(path)
