"""Extraction of a solved model into tidy DataFrames."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import pulp

from depot_opt.model.data import ModelData
from depot_opt.model.variables import DecisionVariables

TOL = 1e-6


def _val(var) -> float:
    value = pulp.value(var)
    return 0.0 if value is None else float(value)


@dataclass
class SolutionReport:
    status: str
    objective: float | None
    depot_decisions: pd.DataFrame
    assignments: pd.DataFrame
    stock: pd.DataFrame
    transfers: pd.DataFrame
    shortages: pd.DataFrame
    expansion: pd.DataFrame
    cost_breakdown: pd.DataFrame
    ending_stock: dict[tuple[str, str], float] = field(default_factory=dict)

    TABLES = (
        "depot_decisions", "assignments", "stock", "transfers",
        "shortages", "expansion", "cost_breakdown",
    )  # fmt: skip

    def tag(self, **labels) -> SolutionReport:
        """Add constant label columns (scenario, window, ...) to every table."""
        for name in self.TABLES:
            frame = getattr(self, name)
            for key, value in labels.items():
                frame.insert(0, key, value)
        return self

    def to_csv(self, directory: str | Path) -> Path:
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        for name in self.TABLES:
            getattr(self, name).to_csv(out / f"{name}.csv", index=False)
        return out

    @staticmethod
    def concat(reports: list[SolutionReport]) -> SolutionReport:
        if not reports:
            raise ValueError("Nothing to concatenate.")
        tables = {
            n: pd.concat([getattr(r, n) for r in reports], ignore_index=True)
            for n in SolutionReport.TABLES
        }
        statuses = {r.status for r in reports}
        objectives = [r.objective for r in reports]
        return SolutionReport(
            status=statuses.pop() if len(statuses) == 1 else "Mixed",
            objective=None if None in objectives else float(sum(objectives)),
            ending_stock=reports[-1].ending_stock,
            **tables,
        )


def extract_results(
    model: pulp.LpProblem,
    data: ModelData,
    v: DecisionVariables,
    cost_terms: dict[str, pulp.LpAffineExpression],
) -> SolutionReport:
    S, P = data.sets, data.params
    status = pulp.LpStatus[model.status]
    has_solution = model.sol_status in (pulp.LpSolutionOptimal, pulp.LpSolutionIntegerFeasible)

    decisions = []
    for d in S.depots:
        is_open = _val(v.open[d]) > 0.5
        existing = d in S.existing
        action = (
            ("keep" if is_open else "close") if existing else ("open" if is_open else "not_opened")
        )
        decisions.append(
            {"depot_id": d, "status": "existing" if existing else "candidate",
             "open": int(is_open), "action": action,
             "zones_served": sum(_val(v.assign[c, d]) > 0.5 for c in S.zones_of_depot(d))}
        )  # fmt: skip

    assignments = [
        {"zone_id": c, "depot_id": d, "share": round(_val(x), 6),
         "volume": P.zone_volume(c) * _val(x), "road_km": P.road_km[c, d],
         "unit_delivery_cost": P.delivery_cost[c, d]}
        for (c, d), x in v.assign.items() if _val(x) > TOL
    ]  # fmt: skip
    stock = [
        {"depot_id": d, "product_id": p, "period": t, "quantity": _val(s)}
        for (d, p, t), s in v.stock.items() if _val(s) > TOL
    ]  # fmt: skip
    transfers = [
        {"from_depot": i, "to_depot": j, "product_id": p, "period": t,
         "quantity": _val(z), "cost": _val(z) * P.transfer_cost[i, j]}
        for (i, j, p, t), z in v.transfer.items() if _val(z) > TOL
    ]  # fmt: skip
    shortages = [
        {"depot_id": d, "product_id": p, "period": t, "quantity": _val(u)}
        for (d, p, t), u in v.shortage.items() if _val(u) > TOL
    ]  # fmt: skip
    expansion = [
        {"depot_id": d, "capacity": P.capacity[d], "excess": _val(v.excess[d]), "batches": _val(b)}
        for d, b in v.batches.items() if _val(b) > TOL or _val(v.excess[d]) > TOL
    ]  # fmt: skip

    # Rounding removes solver round-off (e.g. 1 - y = -1e-10); "+ 0.0" turns -0.0 into 0.0.
    costs = {
        name: round(float(pulp.value(expr) or 0.0), 6) + 0.0 for name, expr in cost_terms.items()
    }
    costs["total_excl_penalty"] = sum(val for k, val in costs.items() if k != "shortage_penalty")
    breakdown = pd.DataFrame([{"component": k, "cost": val} for k, val in costs.items()])

    ending: dict[tuple[str, str], float] = {}
    if v.stock and S.periods:
        last = S.periods[-1]
        ending = {(d, p): _val(v.stock[d, p, last]) for d in S.depots for p in S.products}

    return SolutionReport(
        status=status,
        objective=float(pulp.value(model.objective)) if has_solution else None,
        depot_decisions=pd.DataFrame(decisions),
        assignments=pd.DataFrame(
            assignments,
            columns=["zone_id", "depot_id", "share", "volume", "road_km", "unit_delivery_cost"],
        ),
        stock=pd.DataFrame(stock, columns=["depot_id", "product_id", "period", "quantity"]),
        transfers=pd.DataFrame(
            transfers,
            columns=["from_depot", "to_depot", "product_id", "period", "quantity", "cost"],
        ),
        shortages=pd.DataFrame(shortages, columns=["depot_id", "product_id", "period", "quantity"]),
        expansion=pd.DataFrame(expansion, columns=["depot_id", "capacity", "excess", "batches"]),
        cost_breakdown=breakdown,
        ending_stock=ending,
    )
