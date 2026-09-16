"""Objective function: total network cost as a sum of named components.

Each component is built as its own PuLP expression so it can be reported
separately after the solve (see `results.cost_breakdown`).
"""

from __future__ import annotations

import pulp

from depot_opt.config import CostConfig
from depot_opt.model.data import ModelData
from depot_opt.model.variables import DecisionVariables


def rent_cost(data: ModelData, v: DecisionVariables) -> pulp.LpAffineExpression:
    """Σ_d R_d · y_d  - lease paid for every open depot."""
    return pulp.lpSum(data.params.rent[d] * v.open[d] for d in data.sets.depots)


def fixed_operating_cost(data: ModelData, v: DecisionVariables) -> pulp.LpAffineExpression:
    """Σ_d F_d · y_d  - maintenance and other site-fixed costs of open depots."""
    return pulp.lpSum(data.params.fixed_operating[d] * v.open[d] for d in data.sets.depots)


def opening_cost(data: ModelData, v: DecisionVariables) -> pulp.LpAffineExpression:
    """Σ_{d∈D_C} O_d · y_d  - (amortised) one-off cost of opening a candidate site."""
    return pulp.lpSum(data.params.opening_cost[d] * v.open[d] for d in data.sets.candidates)


def closing_cost(data: ModelData, v: DecisionVariables) -> pulp.LpAffineExpression:
    """Σ_{d∈D_E} K_d · (1 − y_d)  - (amortised) one-off cost of closing an existing depot."""
    return pulp.lpSum(data.params.closing_cost[d] * (1 - v.open[d]) for d in data.sets.existing)


def delivery_cost(data: ModelData, v: DecisionVariables) -> pulp.LpAffineExpression:
    """Σ_{(c,d)∈A} c_cd · Q_c · x_cd  - outbound freight from depots to zones."""
    p = data.params
    return pulp.lpSum(
        p.delivery_cost[c, d] * p.zone_volume(c) * x for (c, d), x in v.assign.items()
    )


def transfer_cost(data: ModelData, v: DecisionVariables) -> pulp.LpAffineExpression:
    """Σ τ_ij · z_ijpt  - repositioning stock between depots."""
    tc = data.params.transfer_cost
    return pulp.lpSum(tc[i, j] * z for (i, j, _p, _t), z in v.transfer.items())


def expansion_cost(data: ModelData, v: DecisionVariables) -> pulp.LpAffineExpression:
    """Σ_d G_d · b_d  - extra storage bought in blocks (soft capacity)."""
    return pulp.lpSum(data.params.expansion_cost[d] * b for d, b in v.batches.items())


def shortage_penalty(penalty: float, v: DecisionVariables) -> pulp.LpAffineExpression:
    """M_u · Σ u_dpt  - penalty keeping the model feasible when supply is short."""
    return pulp.lpSum(penalty * u for u in v.shortage.values())


def build_cost_terms(
    data: ModelData, v: DecisionVariables, costs: CostConfig
) -> dict[str, pulp.LpAffineExpression]:
    """Return the enabled cost components, keyed by name."""
    terms: dict[str, pulp.LpAffineExpression] = {}
    if costs.include_rent:
        terms["rent"] = rent_cost(data, v)
    if costs.include_fixed_operating:
        terms["fixed_operating"] = fixed_operating_cost(data, v)
    if costs.include_opening:
        terms["opening"] = opening_cost(data, v)
    if costs.include_closing:
        terms["closing"] = closing_cost(data, v)
    if costs.include_delivery:
        terms["delivery"] = delivery_cost(data, v)
    if costs.include_transfer:
        terms["transfer"] = transfer_cost(data, v)
    if costs.include_expansion:
        terms["expansion"] = expansion_cost(data, v)
    terms["shortage_penalty"] = shortage_penalty(costs.shortage_penalty, v)
    return terms
