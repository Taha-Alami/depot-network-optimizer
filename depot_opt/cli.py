"""Command-line interface.

depot-opt generate-data --out data/synthetic
depot-opt run configs/scenarios/baseline.yaml
depot-opt compare configs/scenarios/baseline.yaml configs/scenarios/forced_closure.yaml
depot-opt sensitivity configs/scenarios/baseline.yaml \\
    --param costs.round_trip_factor --values 1 1.5 2
"""

from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd
import yaml

from depot_opt.config import SyntheticDataConfig, load_scenario
from depot_opt.data.io import write_csv
from depot_opt.data.synthetic import generate_dataset
from depot_opt.scenarios.runner import compare_scenarios, run_scenario, summarize
from depot_opt.scenarios.sensitivity import sweep


def _parse_value(text: str):
    return yaml.safe_load(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="depot-opt", description=__doc__.splitlines()[0])
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate-data", help="write a synthetic dataset as CSV files")
    gen.add_argument("--out", default="data/synthetic")
    gen.add_argument("--seed", type=int, default=7)
    gen.add_argument("--zones", type=int, default=30)

    run = sub.add_parser("run", help="solve one scenario")
    run.add_argument("scenario")
    run.add_argument("--output-dir")

    cmp_ = sub.add_parser("compare", help="solve several scenarios and compare KPIs")
    cmp_.add_argument("scenarios", nargs="+")

    sens = sub.add_parser("sensitivity", help="one-at-a-time parameter sweep")
    sens.add_argument("scenario")
    sens.add_argument("--param", required=True)
    sens.add_argument("--values", nargs="+", required=True)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    pd.set_option("display.width", 160)
    pd.set_option("display.max_columns", 30)

    if args.command == "generate-data":
        ds = generate_dataset(SyntheticDataConfig(seed=args.seed, n_zones=args.zones))
        print(f"Synthetic dataset written to {write_csv(ds, args.out)}")
    elif args.command == "run":
        cfg = load_scenario(args.scenario)
        if args.output_dir:
            cfg = cfg.model_copy(update={"output_dir": args.output_dir})
        report = run_scenario(cfg)
        print(pd.Series(summarize(report)).to_string())
        print(f"\nOutputs: {cfg.output_dir}/{cfg.name}/")
    elif args.command == "compare":
        table = compare_scenarios([load_scenario(p) for p in args.scenarios])
        print(table.T.to_string())
    elif args.command == "sensitivity":
        cfg = load_scenario(args.scenario)
        table = sweep(cfg, args.param, [_parse_value(v) for v in args.values])
        print(table.T.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
