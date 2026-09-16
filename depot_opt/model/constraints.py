"""Model constraints.

Each business rule lives in its own function with the signature
``add_xxx(model, data, v, cfg) -> int`` (returns the number of rows added).
`CONSTRAINTS` lists them in build order together with the config switch that
activates them, so scenarios are assembled purely from YAML.

Notation follows `docs/model_formulation.md`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import pulp

from depot_opt.config import ConstraintsConfig
from depot_opt.model.data import ModelData
from depot_opt.model.variables import DecisionVariables

LOGGER = logging.getLogger(__name__)

ConstraintFn = Callable[[pulp.LpProblem, ModelData, DecisionVariables, ConstraintsConfig], int]


def add_demand_satisfaction(model, data, v, cfg) -> int:
    """Every demand zone is fully served.

        Σ_{d : (c,d)∈A} x_cd = 1                      ∀ c ∈ C

    With `single_sourcing.enabled` the x_cd are binary, so each zone is served
    by exactly one depot (one service contact per zone). Otherwise x_cd is the
    share of the zone's volume served by depot d.
    """
    n = 0
    for c in data.sets.zones:
        model += (
            pulp.lpSum(v.assign[c, d] for d in data.sets.depots_of_zone(c)) == 1,
            f"demand_satisfaction[{c}]",
        )
        n += 1
    return n


def add_assign_only_if_open(model, data, v, cfg) -> int:
    """Zones can only be served by open depots (linking constraint).

        x_cd ≤ y_d                                   ∀ (c,d) ∈ A

    The disaggregated form is used (instead of Σ_c x_cd ≤ |C|·y_d) because
    its LP relaxation is much tighter.
    """
    for (c, d), x in v.assign.items():
        model += x <= v.open[d], f"assign_only_if_open[{c},{d}]"
    return len(v.assign)


def add_inventory_balance(model, data, v, cfg) -> int:
    """Stock conservation per depot, product and period.

        s_dpt = s_dp,t−1 + Σ_i z_idpt − Σ_j z_djpt + ρ_dpt + ε_dpt
                − Σ_c q_cpt · x_cd + u_dpt           ∀ d, p, t

    with s_dp,0 = S0_dp (initial stock, or the previous window's ending stock
    in a rolling horizon). Because s ≥ 0, a depot can only ship what it holds,
    receives by transfer, gets back from customers or obtains as emergency
    supply u (penalised in the objective).
    """
    S, P = data.sets, data.params
    inbound: dict[str, list[str]] = {}
    outbound: dict[str, list[str]] = {}
    for i, j in S.transfer_arcs:
        outbound.setdefault(i, []).append(j)
        inbound.setdefault(j, []).append(i)

    n = 0
    for d in S.depots:
        zones_d = S.zones_of_depot(d)
        for p in S.products:
            previous: pulp.LpAffineExpression | float = P.initial_stock.get((d, p), 0.0)
            for t in S.periods:
                transfers_in = pulp.lpSum(
                    v.transfer[i, d, p, t] for i in inbound.get(d, []) if v.transfer
                )
                transfers_out = pulp.lpSum(
                    v.transfer[d, j, p, t] for j in outbound.get(d, []) if v.transfer
                )
                shipped = pulp.lpSum(
                    P.demand[c, p, t] * v.assign[c, d] for c in zones_d if (c, p, t) in P.demand
                )
                shortage = v.shortage[d, p, t] if v.shortage else 0
                model += (
                    v.stock[d, p, t]
                    == previous
                    + transfers_in
                    - transfers_out
                    + P.returns.get((d, p, t), 0.0)
                    + P.inflows.get((d, p, t), 0.0)
                    - shipped
                    + shortage,
                    f"inventory_balance[{d},{p},{t}]",
                )
                previous = v.stock[d, p, t]
                n += 1
    return n


def _big_m(data: ModelData, cfg: ConstraintsConfig) -> dict[tuple[str, str], float]:
    """Valid upper bound on s_dpt: total supply of p plus total demand of p.

    An optimal solution never holds more of p than the system ever receives
    plus what emergency supply could add (bounded by demand); a hard capacity
    tightens the bound further.
    """
    S, P = data.sets, data.params
    total: dict[str, float] = dict.fromkeys(S.products, 0.0)
    for (_d, p), q in P.initial_stock.items():
        if p in total:
            total[p] += q
    for table in (P.returns, P.inflows, P.demand):
        for (_x, p, _t), q in table.items():
            total[p] += q
    bounds = {}
    hard = cfg.capacity.enabled and cfg.capacity.mode == "hard"
    for d in S.depots:
        cap = P.capacity.get(d)
        for p in S.products:
            m = cfg.stock_only_if_open.big_m or total[p]
            bounds[d, p] = min(m, cap) if (hard and cap is not None) else m
    return bounds


def add_stock_only_if_open(model, data, v, cfg) -> int:
    """A closed depot cannot hold stock.

        s_dpt ≤ M_dp · y_d                           ∀ d, p, t

    If an existing depot is closed, its initial stock and any returns routed
    to it must be transferred out in the same period (at transfer cost).
    """
    m = _big_m(data, cfg)
    n = 0
    for (d, p, t), s in v.stock.items():
        model += s <= m[d, p] * v.open[d], f"stock_only_if_open[{d},{p},{t}]"
        n += 1
    return n


def _load_expr(data: ModelData, v: DecisionVariables, d: str, t: str) -> pulp.LpAffineExpression:
    """Stock held at d at the end of t, or the volume shipped in t if stock is not modelled."""
    S, P = data.sets, data.params
    if v.stock:
        return pulp.lpSum(v.stock[d, p, t] for p in S.products)
    return pulp.lpSum(
        P.demand[c, p, t] * v.assign[c, d]
        for c in S.zones_of_depot(d)
        for p in S.products
        if (c, p, t) in P.demand
    )


def add_capacity(model, data, v, cfg) -> int:
    """Depot storage capacity (throughput capacity when stock is not modelled).

    hard mode:
        Σ_p s_dpt ≤ Cap_d · y_d                      ∀ d, t

    soft mode (capacity can be extended in blocks of size B):
        e_d ≥ Σ_p s_dpt − Cap_d                      ∀ d, t   (peak overflow)
        B · b_d ≥ e_d                                ∀ d

    Depots with an unlimited capacity (None) are skipped.
    """
    S, P = data.sets, data.params
    cap_cfg = cfg.capacity
    n = 0
    for d in S.depots:
        cap = P.capacity.get(d)
        if cap is None:
            continue
        for t in S.periods:
            load = _load_expr(data, v, d, t)
            if cap_cfg.mode == "hard":
                model += load <= cap * v.open[d], f"capacity[{d},{t}]"
            else:
                model += v.excess[d] >= load - cap, f"capacity_overflow[{d},{t}]"
            n += 1
        if cap_cfg.mode == "soft":
            model += (
                cap_cfg.expansion_batch_size * v.batches[d] >= v.excess[d],
                f"expansion_batches[{d}]",
            )
            n += 1
    return n


def add_max_service_distance(model, data, v, cfg) -> int:
    """Service-level coverage: a zone may only be served within a distance/time limit.

        x_cd = 0     ∀ (c,d) ∈ A with dist_cd > D_max or time_cd > T_max

    Raises if a zone is left without any admissible depot.
    """
    sd = cfg.max_service_distance
    P = data.params
    dist = P.road_km if sd.distance_basis == "road" else P.linear_km
    banned = set()
    for arc in v.assign:
        too_far = sd.max_distance_km is not None and dist[arc] > sd.max_distance_km
        tt = P.travel_min.get(arc)
        too_slow = (
            sd.max_travel_time_min is not None and tt is not None and tt > sd.max_travel_time_min
        )
        if too_far or too_slow:
            banned.add(arc)
    for c in data.sets.zones:
        if all((c, d) in banned for d in data.sets.depots_of_zone(c)):
            raise ValueError(f"Zone '{c}' has no depot within the service limit.")
    for c, d in sorted(banned):
        model += v.assign[c, d] == 0, f"max_service_distance[{c},{d}]"
    return len(banned)


def add_open_depot_count(model, data, v, cfg) -> int:
    """Bounds on the size and change of the network.

    L ≤ Σ_d y_d ≤ U
    Σ_{d∈D_C} y_d ≤ N_new          (new sites)
    Σ_{d∈D_E} (1 − y_d) ≤ N_close  (closures)
    """
    oc = cfg.open_depot_count
    S = data.sets
    n = 0
    total = pulp.lpSum(v.open[d] for d in S.depots)
    if oc.min_open is not None:
        model += total >= oc.min_open, "min_open_depots"
        n += 1
    if oc.max_open is not None:
        model += total <= oc.max_open, "max_open_depots"
        n += 1
    if oc.max_new_sites is not None:
        model += pulp.lpSum(v.open[d] for d in S.candidates) <= oc.max_new_sites, "max_new_sites"
        n += 1
    if oc.max_closures is not None:
        model += pulp.lpSum(1 - v.open[d] for d in S.existing) <= oc.max_closures, "max_closures"
        n += 1
    return n


def add_forced_status(model, data, v, cfg) -> int:
    """Scenario levers: must-keep depots and forced closures.

        y_d = 1   ∀ d ∈ D_open        y_d = 0   ∀ d ∈ D_closed

    `others` optionally fixes every unlisted depot ('open' reproduces a
    "close only these sites" what-if; 'free' lets the optimiser decide).
    """
    fs = cfg.forced_status
    known = set(data.sets.depots)
    unknown = (set(fs.must_open) | set(fs.must_close)) - known
    if unknown:
        raise ValueError(f"forced_status references unknown depots: {sorted(unknown)}")
    n = 0
    for d in data.sets.depots:
        if d in fs.must_open or (fs.others == "open" and d not in fs.must_close):
            model += v.open[d] == 1, f"forced_open[{d}]"
        elif d in fs.must_close or fs.others == "closed":
            model += v.open[d] == 0, f"forced_closed[{d}]"
        else:
            continue
        n += 1
    return n


def add_regional_coverage(model, data, v, cfg) -> int:
    """Keep a minimum footprint in every region.

    Σ_{d ∈ D_r} y_d ≥ k_r                        ∀ r ∈ R
    """
    rc = cfg.regional_coverage
    S = data.sets
    n = 0
    for r in S.regions:
        members = [d for d in S.depots if S.depot_region.get(d) == r]
        k = rc.per_region.get(r, rc.min_open_per_region)
        if k > len(members):
            raise ValueError(f"Region '{r}' needs {k} open depots but only has {len(members)}.")
        model += pulp.lpSum(v.open[d] for d in members) >= k, f"regional_coverage[{r}]"
        n += 1
    return n


CONSTRAINTS: list[tuple[str, ConstraintFn, Callable[[ConstraintsConfig], bool]]] = [
    ("demand_satisfaction", add_demand_satisfaction, lambda c: True),
    ("assign_only_if_open", add_assign_only_if_open, lambda c: c.assign_only_if_open.enabled),
    ("inventory_balance", add_inventory_balance, lambda c: c.inventory_balance.enabled),
    (
        "stock_only_if_open",
        add_stock_only_if_open,
        lambda c: c.stock_only_if_open.enabled and c.inventory_balance.enabled,
    ),
    ("capacity", add_capacity, lambda c: c.capacity.enabled),
    ("max_service_distance", add_max_service_distance, lambda c: c.max_service_distance.enabled),
    ("open_depot_count", add_open_depot_count, lambda c: c.open_depot_count.enabled),
    ("forced_status", add_forced_status, lambda c: c.forced_status.enabled),
    ("regional_coverage", add_regional_coverage, lambda c: c.regional_coverage.enabled),
]


def add_constraints(model, data, v, cfg: ConstraintsConfig) -> dict[str, int]:
    """Add every enabled constraint family; return row counts per family."""
    counts = {}
    for name, fn, enabled in CONSTRAINTS:
        if enabled(cfg):
            counts[name] = fn(model, data, v, cfg)
            LOGGER.debug("Added %d '%s' constraints.", counts[name], name)
    return counts
