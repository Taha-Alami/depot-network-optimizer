"""Decision variables.

| Symbol        | PuLP name      | Domain            | Meaning                                   |
|---------------|----------------|-------------------|-------------------------------------------|
| y_d           | open           | {0,1}             | depot d is open during the horizon        |
| x_cd          | assign         | {0,1} or [0,1]    | (share of) zone c served by depot d       |
| s_dpt         | stock          | Z+ or R+          | units of p held at d at the end of t      |
| z_ijpt        | transfer       | Z+ or R+          | units of p moved from depot i to j in t   |
| u_dpt         | shortage       | Z+ or R+          | emergency supply / unmet demand at d      |
| e_d           | excess         | Z+ or R+          | peak stock above nominal capacity         |
| b_d           | batches        | Z+                | capacity expansion blocks bought at d     |

Variables that belong to a disabled feature are simply not created
(their dictionaries stay empty), so the objective and constraints can
iterate over them unconditionally.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pulp

from depot_opt.config import ConstraintsConfig
from depot_opt.model.data import ModelData


@dataclass
class DecisionVariables:
    open: dict[str, pulp.LpVariable]
    assign: dict[tuple[str, str], pulp.LpVariable]
    stock: dict[tuple[str, str, str], pulp.LpVariable] = field(default_factory=dict)
    transfer: dict[tuple[str, str, str, str], pulp.LpVariable] = field(default_factory=dict)
    shortage: dict[tuple[str, str, str], pulp.LpVariable] = field(default_factory=dict)
    excess: dict[str, pulp.LpVariable] = field(default_factory=dict)
    batches: dict[str, pulp.LpVariable] = field(default_factory=dict)


def _dicts(model: pulp.LpProblem, name: str, indices, **kwargs) -> dict:
    """Create an indexed variable family (PuLP >= 3.3 API with a fallback for older PuLP)."""
    if hasattr(model, "add_variable_dicts"):
        return model.add_variable_dicts(name, indices, **kwargs)
    return pulp.LpVariable.dicts(name, indices, **kwargs)


def create_variables(
    model: pulp.LpProblem, data: ModelData, cons: ConstraintsConfig
) -> DecisionVariables:
    S = data.sets
    inv = cons.inventory_balance
    qty_cat = pulp.LpInteger if inv.integer_quantities else pulp.LpContinuous
    assign_cat = pulp.LpBinary if cons.single_sourcing.enabled else pulp.LpContinuous

    v = DecisionVariables(
        open=_dicts(model, "open", S.depots, cat=pulp.LpBinary),
        assign=_dicts(model, "assign", S.arcs, lowBound=0, upBound=1, cat=assign_cat),
    )
    if inv.enabled:
        dpt = [(d, p, t) for d in S.depots for p in S.products for t in S.periods]
        v.stock = _dicts(model, "stock", dpt, lowBound=0, cat=qty_cat)
        if inv.allow_shortage:
            v.shortage = _dicts(model, "shortage", dpt, lowBound=0, cat=qty_cat)
        if inv.allow_transfers:
            ijpt = [
                (i, j, p, t) for (i, j) in S.transfer_arcs for p in S.products for t in S.periods
            ]
            v.transfer = _dicts(model, "transfer", ijpt, lowBound=0, cat=qty_cat)

    cap = cons.capacity
    if cap.enabled and cap.mode == "soft":
        limited = [d for d in S.depots if data.params.capacity.get(d) is not None]
        v.excess = _dicts(model, "excess", limited, lowBound=0, cat=qty_cat)
        v.batches = _dicts(model, "batches", limited, lowBound=0, cat=pulp.LpInteger)
    return v
