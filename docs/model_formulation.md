# Model formulation

This document gives the full mixed-integer linear program (MILP) implemented in
`depot_opt/model/`. Each constraint family corresponds to one function in
[`constraints.py`](../depot_opt/model/constraints.py). Each cost component corresponds to one
function in [`objective.py`](../depot_opt/model/objective.py).

---

## 1. Sets and indices

| Symbol | Description | Code |
|---|---|---|
| $D^E$ | existing depots (can be kept or closed) | `sets.existing` |
| $D^C$ | candidate sites (can be opened) | `sets.candidates` |
| $D = D^E \cup D^C$ | all depots, index $d, i, j$ | `sets.depots` |
| $C$ | customer demand zones with demand in the horizon, index $c$ | `sets.zones` |
| $P$ | product / equipment types, index $p$ | `sets.products` |
| $T = \{1,\dots,\lvert T\rvert\}$ | ordered time buckets (dates), index $t$ | `sets.periods` |
| $R$ | regions, index $r$; $D_r \subseteq D$ are the depots located in region $r$ | `sets.regions` |
| $A \subseteq C \times D$ | admissible zone–depot arcs (pairs with a known distance) | `sets.arcs` |
| $D(c) = \{d : (c,d) \in A\}$ | depots that can serve zone $c$ | `depots_of_zone(c)` |
| $L \subseteq D \times D$ | transfer lanes, $i \neq j$ | `sets.transfer_arcs` |

Self-transfers are excluded by construction ($L$ contains no pair $(i,i)$), so no
explicit "no self-transfer" constraint is needed.

## 2. Parameters

### Costs

| Symbol | Unit | Description |
|---|---|---|
| $H$ | months | length of the modelled horizon (inferred from the data or configured) |
| $R_d = H \cdot \text{rent}_d$ | CU | lease / rent of depot $d$ over the horizon |
| $F_d = H \cdot \text{fixed}_d$ | CU | maintenance and other site-fixed operating costs |
| $O_d = \dfrac{H}{N^{am}} \cdot \text{open}_d$ | CU | amortised one-off opening cost of candidate $d$ |
| $K_d = \dfrac{H}{N^{am}} \cdot \text{close}_d$ | CU | amortised one-off closing cost of existing depot $d$ |
| $N^{am}$ | months | amortisation period for one-off costs |
| $c_{cd}$ | CU/unit | delivery cost per unit from $d$ to $c$ (see below) |
| $\tau_{ij}$ | CU/unit | transfer cost per unit from depot $i$ to depot $j$ |
| $G_d$ | CU/batch | cost of one capacity-expansion batch at $d$ |
| $M^u$ | CU/unit | penalty per unit of emergency supply (unmet demand) |

**Delivery cost.** Let $\ell_{cd}$ be the road distance, $\lambda_{cd}$ the great-circle
distance, $w$ the width of a distance band and $\bar b$ the last band. The tariff band is

$$
\beta(\lambda) = \min\left(\left\lfloor \lambda / w \right\rfloor + 1,\ \bar b\right).
$$

Tariffs $r_{b,k}$ are given per band $b$ and carrier option $k$ (own fleet, external
carrier, single or double load, ...). They are blended with volume shares $\omega_k$
($\sum_k \omega_k = 1$):

$$
r_b = \sum_k \omega_k \, r_{b,k}, \qquad
c_{cd} = \varphi \cdot \ell_{cd} \cdot r_{\beta(\lambda_{cd})}
$$

where $\varphi$ is the round-trip factor ($\varphi = 2$ when vehicles return empty).
If no depot–depot cost table is supplied, $\tau_{ij}$ is derived the same way from the
depot coordinates, using the road-detour factor to estimate road distance.

### Quantities and limits

| Symbol | Unit | Description |
|---|---|---|
| $q_{cpt}$ | units | demand of zone $c$ for product $p$ in period $t$ |
| $Q_c = \sum_{p,t} q_{cpt}$ | units | total volume of zone $c$ |
| $S^0_{dp}$ | units | opening stock (or previous window's ending stock) |
| $\rho_{dpt}$ | units | customer returns arriving at $d$ |
| $\varepsilon_{dpt}$ | units | other exogenous arrivals at $d$ (e.g. from a central hub) |
| $\text{Cap}_d$ | units | storage capacity of $d$; depots without a limit are excluded from (5) |
| $B$ | units | size of one capacity-expansion batch |
| $\text{dist}_{cd}$, $\text{time}_{cd}$ | km, min | service metrics per arc |
| $D^{max}$, $T^{max}$ | km, min | service-level limits |
| $\underline{N}, \overline{N}$ | depots | minimum / maximum number of open depots |
| $N^{new}$, $N^{close}$ | depots | maximum number of new sites / closures |
| $k_r$ | depots | minimum number of open depots in region $r$ |
| $M_{dp}$ | units | big-M bound on stock (Section 6) |

## 3. Decision variables

| Variable | Domain | Meaning |
|---|---|---|
| $y_d$ | $\{0,1\}$ | 1 if depot $d$ is open (kept or newly opened) |
| $x_{cd}$ | $\{0,1\}$ or $[0,1]$ | zone $c$ is served by depot $d$ (share of volume if split sourcing is allowed) |
| $s_{dpt}$ | $\mathbb{Z}_{\ge 0}$ or $\mathbb{R}_{\ge 0}$ | stock of $p$ at $d$ at the end of period $t$ |
| $z_{ijpt}$ | $\mathbb{Z}_{\ge 0}$ or $\mathbb{R}_{\ge 0}$ | units of $p$ moved from $i$ to $j$ in $t$, $(i,j) \in L$ |
| $u_{dpt}$ | $\mathbb{Z}_{\ge 0}$ or $\mathbb{R}_{\ge 0}$ | emergency supply (unmet demand) at $d$ |
| $e_d$ | $\mathbb{Z}_{\ge 0}$ or $\mathbb{R}_{\ge 0}$ | peak stock above nominal capacity (soft capacity) |
| $b_d$ | $\mathbb{Z}_{\ge 0}$ | number of expansion batches bought at $d$ |

Closure of an existing depot is expressed as $1 - y_d$; no separate variable is needed.

## 4. Objective function

$$
\min Z \;=\;
\underbrace{\sum_{d \in D} R_d\, y_d}_{\text{rent}}
+ \underbrace{\sum_{d \in D} F_d\, y_d}_{\text{fixed operating}}
+ \underbrace{\sum_{d \in D^C} O_d\, y_d}_{\text{opening}}
+ \underbrace{\sum_{d \in D^E} K_d\, (1 - y_d)}_{\text{closing}}
+ \underbrace{\sum_{(c,d) \in A} c_{cd}\, Q_c\, x_{cd}}_{\text{delivery}}
+ \underbrace{\sum_{(i,j) \in L} \sum_{p,t} \tau_{ij}\, z_{ijpt}}_{\text{transfer}}
+ \underbrace{\sum_{d} G_d\, b_d}_{\text{expansion}}
+ \underbrace{M^u \sum_{d,p,t} u_{dpt}}_{\text{shortage penalty}}
$$

Each component can be switched off (`costs.include_*`). Because $Q_c$ is data, the
delivery term stays linear even though it depends on the assignment.

## 5. Constraints

### (1) Demand satisfaction: `add_demand_satisfaction` (always on)

$$
\sum_{d \in D(c)} x_{cd} = 1 \qquad \forall c \in C
$$

Every zone is fully served. With `single_sourcing.enabled` the $x_{cd}$ are binary, so
each zone has exactly one serving depot. Otherwise $x_{cd}$ is the share of the zone's
volume served by $d$.

### (2) Assignment only to open depots: `add_assign_only_if_open`

$$
x_{cd} \le y_d \qquad \forall (c,d) \in A
$$

This is the disaggregated linking constraint. It gives a much tighter LP relaxation than
the aggregated form $\sum_c x_{cd} \le \lvert C\rvert\, y_d$.

### (3) Inventory balance: `add_inventory_balance`

$$
s_{dpt} = s_{dp,t-1}
+ \sum_{i:(i,d) \in L} z_{idpt}
- \sum_{j:(d,j) \in L} z_{djpt}
+ \rho_{dpt} + \varepsilon_{dpt}
- \sum_{c:(c,d) \in A} q_{cpt}\, x_{cd}
+ u_{dpt}
\qquad \forall d, p, t
$$

with $s_{dp,0} = S^0_{dp}$. Stock carries over from one period to the next. Because
$s \ge 0$, a depot can only ship what it holds, what it receives by transfer or return,
or what it gets as penalised emergency supply $u$. The shortage variable keeps the
model feasible when data are inconsistent; any positive $u$ in a solution should be
investigated. Setting `allow_shortage: false` removes it.

### (4) No stock in closed depots: `add_stock_only_if_open`

$$
s_{dpt} \le M_{dp}\, y_d \qquad \forall d, p, t
$$

If an existing depot is closed, its opening stock and any returns routed to it must be
transferred out in the same period, and those transfers are costed.

### (5) Capacity: `add_capacity`

Let $\text{load}_{dt} = \sum_p s_{dpt}$. If stock is not modelled
(`inventory_balance.enabled: false`), the shipped volume
$\sum_{c,p} q_{cpt} x_{cd}$ is used instead, which turns the constraint into a
throughput capacity.

*Hard mode*

$$
\text{load}_{dt} \le \text{Cap}_d\, y_d \qquad \forall d, t
$$

*Soft mode* (capacity can be extended in batches)

$$
e_d \ge \text{load}_{dt} - \text{Cap}_d \quad \forall d, t,
\qquad
B\, b_d \ge e_d \quad \forall d
$$

$e_d$ measures the **peak** overflow over the horizon, and the expansion is sized to
that peak in integer batches. A per-depot override can set a capacity to "unlimited",
which removes the depot from this family.

### (6) Maximum service distance / response time: `add_max_service_distance` (optional)

$$
x_{cd} = 0 \qquad \forall (c,d) \in A :\ \text{dist}_{cd} > D^{max} \ \lor\ \text{time}_{cd} > T^{max}
$$

This is a coverage (service-level) requirement. Distances can be road or great-circle.
The builder raises an error if a zone is left without any admissible depot, because the
model would otherwise be infeasible.

### (7) Network size: `add_open_depot_count` (optional)

$$
\underline{N} \le \sum_{d \in D} y_d \le \overline{N},
\qquad
\sum_{d \in D^C} y_d \le N^{new},
\qquad
\sum_{d \in D^E} (1 - y_d) \le N^{close}
$$

### (8) Forced status: `add_forced_status` (optional)

$$
y_d = 1 \quad \forall d \in D^{open},
\qquad
y_d = 0 \quad \forall d \in D^{closed}
$$

These are the scenario levers. Depots that are not listed can be left free, forced
open or forced closed (`others`). For example, "close depot X, keep everything else"
is `must_close: [X]` with `others: open`.

### (9) Regional coverage: `add_regional_coverage` (optional)

$$
\sum_{d \in D_r} y_d \ge k_r \qquad \forall r \in R
$$

### Domains

$$
y_d \in \{0,1\},\quad
x_{cd} \in \{0,1\}\ \text{or}\ [0,1],\quad
s, z, u, e \ge 0\ (\text{integer if } \texttt{integer\_quantities}),\quad
b_d \in \mathbb{Z}_{\ge 0}
$$

## 6. Big-M choice

For (4), $M_{dp}$ must be at least as large as any stock level that can occur in an
optimal solution. The model uses

$$
M_{dp} = \sum_{d'} S^0_{d'p} + \sum_{d',t} (\rho_{d'pt} + \varepsilon_{d'pt}) + \sum_{c,t} q_{cpt},
$$

which is the total supply of $p$ plus the most emergency supply that could ever be
useful. With hard capacity this is tightened to $\min(M_{dp}, \text{Cap}_d)$. A
smaller, valid $M$ strengthens the LP relaxation; `stock_only_if_open.big_m` can
override it.

## 7. Time handling

| Mode | $T$ | Use |
|---|---|---|
| `aggregate` | one bucket; all flows summed | strategic, static footprint decisions |
| `full` | every date in the horizon, one model | tactical view with stock dynamics |
| `rolling` | one model per month or quarter; $S^0$ of window $w+1$ = ending stock of window $w$ | long horizons that are too large for a single model |

In `aggregate` mode, (3) reduces to
$s_{dp} = S^0_{dp} + \text{in} - \text{out} - \sum_c Q_{cp} x_{cd} + u_{dp} \ge 0$.
This is the classic static requirement that available stock covers the assigned demand.

In `rolling` mode, open/close decisions are taken per window. For a single
network-wide decision, fix the footprint with (8) or use `aggregate`/`full`.

## 8. Model size

With $n_D$ depots, $n_C$ zones, $n_P$ products and $n_T$ periods:

- binary variables: $n_D + \lvert A\rvert \le n_D + n_C\, n_D$
- stock and shortage variables: $2\, n_D\, n_P\, n_T$
- transfer variables: $\lvert L\rvert\, n_P\, n_T \le n_D (n_D - 1)\, n_P\, n_T$

The transfer family dominates for fine time grids. Useful levers are aggregating
products into families, coarser time buckets, a rolling horizon, and pruning $L$ to
realistic lanes (by supplying a depot–depot cost table that only lists them).
