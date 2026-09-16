# Depot Network Optimizer

A mixed-integer linear programming (MILP) model, built with [PuLP](https://coin-or.github.io/pulp/), for **service-depot network design**.
Given demand zones, existing depots and candidate sites, it decides:

- which existing depots to **keep** or **close**,
- which candidate sites to **open**,
- which depot serves each **customer demand zone**,
- how stock is **repositioned** between depots and where capacity must be **extended**,

while minimising the **total network cost**:

| Component | Driver |
|---|---|
| Rent / lease | open depots × monthly rent |
| Fixed operating & maintenance | open depots × monthly fixed cost |
| Opening costs | new sites × one-off cost (amortised) |
| Closing costs | closed depots × one-off cost (amortised) |
| Delivery | road km × distance-band tariff × volume |
| Transfers | units moved between depots × lane cost |
| Capacity expansion | expansion batches × batch cost |
| Shortage penalty | unmet demand (keeps the model feasible and makes data issues visible) |

> The repository contains **methodology and code only**. All data used by the examples and
> tests is synthetic and generated at runtime.

## Approach

1. **Data preparation**: demand, depots, inventory, returns and tariffs are loaded
   through an ODBC loader (placeholder SQL, configured through environment variables),
   from CSV files, or from the synthetic generator.
2. **Geography**: addresses are geocoded, road distances and travel times come from a
   pluggable routing provider, and great-circle distance selects the tariff band.
   Carrier options are blended into one rate per band.
3. **Optimisation**: a MILP with binary open/assign decisions, a stock balance over
   time, depot-to-depot transfers and soft or hard capacity. Every constraint family
   can be switched on or off in YAML.
4. **Scenarios & sensitivity**: what-if runs (closures, redesign, greenfield, capacity
   relief) on the same data, plus one-at-a-time parameter sweeps.

See [docs/model_formulation.md](docs/model_formulation.md) for the full mathematical model and
[docs/methodology.md](docs/methodology.md) for the end-to-end approach.

### Constraint families

| Constraint | Config key | Default |
|---|---|---|
| Demand satisfaction (each zone fully served) | always on; `single_sourcing` sets binary or split assignment | on (binary) |
| Assign only to open depots | `assign_only_if_open` | on |
| Inventory balance (stock, transfers, returns, shortages) | `inventory_balance` | on |
| No stock in closed depots | `stock_only_if_open` | on |
| Depot capacity (hard, or soft with expansion batches) | `capacity` | on (hard) |
| Maximum service distance / travel time | `max_service_distance` | off |
| Min / max open depots, max new sites, max closures | `open_depot_count` | off |
| Must-keep depots / forced closures | `forced_status` | off |
| Minimum open depots per region | `regional_coverage` | off |

## Project structure

```
depot-network-optimizer/
├── configs/
│   ├── base.yaml                  # every setting, documented
│   └── scenarios/                 # scenarios that `extends` the base
│       ├── baseline.yaml          # as-is footprint
│       ├── forced_closure.yaml    # close one depot
│       ├── network_redesign.yaml  # free keep/close decisions
│       ├── greenfield.yaml        # candidates + service limits
│       └── multi_period.yaml      # rolling monthly horizon, soft capacity
├── depot_opt/
│   ├── config.py                  # env settings + YAML scenario schema (pydantic)
│   ├── schemas.py                 # input table schemas (fields, units, meaning)
│   ├── data/
│   │   ├── odbc_loader.py         # pyodbc loader (env-configured, parameterised)
│   │   ├── queries.py             # placeholder SQL templates
│   │   ├── synthetic.py           # synthetic dataset generator
│   │   ├── io.py                  # CSV read/write
│   │   └── builder.py             # tables -> model sets & parameters
│   ├── geo/
│   │   ├── distance.py            # great-circle distance, bands, tariff blending
│   │   └── routing.py             # routing-provider protocol, distance matrix
│   ├── model/
│   │   ├── data.py                # ModelSets / ModelParameters
│   │   ├── variables.py           # decision variables
│   │   ├── objective.py           # named cost components
│   │   ├── constraints.py         # one function per constraint family
│   │   ├── solver.py              # CBC / HiGHS / Gurobi factory
│   │   ├── results.py             # tidy result tables
│   │   └── builder.py             # DepotNetworkModel (build / solve)
│   ├── scenarios/
│   │   ├── runner.py              # single, full and rolling runs; comparison
│   │   └── sensitivity.py         # one-at-a-time sweeps
│   └── cli.py                     # `depot-opt` command
├── docs/
│   ├── model_formulation.md
│   └── methodology.md
└── tests/
```

## Installation

Requires Python ≥ 3.10.

```bash
git clone https://github.com/Taha-Alami/depot-network-optimizer.git
cd depot-network-optimizer
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
```

Optional extras: `.[db]` (pyodbc), `.[highs]` (HiGHS solver), `.[gurobi]` (Gurobi, licence
required). CBC ships with PuLP and needs no installation.

## Usage (synthetic data)

```bash
# solve a scenario (synthetic data is generated on the fly)
depot-opt run configs/scenarios/baseline.yaml

# compare several scenarios on the same dataset
depot-opt compare configs/scenarios/baseline.yaml \
                  configs/scenarios/forced_closure.yaml \
                  configs/scenarios/network_redesign.yaml \
                  configs/scenarios/greenfield.yaml

# rolling monthly horizon with soft capacity
depot-opt run configs/scenarios/multi_period.yaml

# sensitivity: config value or data multiplier
depot-opt sensitivity configs/scenarios/network_redesign.yaml \
    --param data:depots.rent_cost_per_month --values 0.8 1.0 1.2

# export the synthetic dataset as CSV (e.g. to replace it with your own files)
depot-opt generate-data --out data/synthetic
```

Results are written to `outputs/<scenario>/` as CSV files: `depot_decisions`,
`assignments`, `stock`, `transfers`, `shortages`, `expansion` and `cost_breakdown`.

From Python:

```python
from depot_opt import load_scenario, run_scenario

cfg = load_scenario("configs/scenarios/greenfield.yaml")
report = run_scenario(cfg, write=False)
print(report.status)
print(report.depot_decisions)
print(report.cost_breakdown)
```

## Using your own data

- **CSV**: set `data.source: csv` and `data.csv_dir`, with one file per table as defined in
  [`depot_opt/schemas.py`](depot_opt/schemas.py). `depot-opt generate-data` writes an
  example of every file.
- **Database**: copy `.env.example` to `.env`, fill in the connection details and table
  names, set `data.source: odbc` plus `data.start_date` / `data.end_date`, and adapt the
  templates in [`depot_opt/data/queries.py`](depot_opt/data/queries.py) to your schema.
  `.env` is git-ignored.
- **Routing**: implement `RoutingProvider.route()` for your routing engine and build the
  zone–depot matrix with `geo.routing.build_distance_matrix`.

## Development

```bash
pytest            # unit tests: every constraint family + end-to-end runs
ruff check .      # lint
pre-commit install  # optional: secret scanning and lint before each commit
```

## License

[Apache License 2.0](LICENSE)
