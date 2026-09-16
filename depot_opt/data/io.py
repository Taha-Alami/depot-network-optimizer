"""CSV persistence for `NetworkDataset` (one file per table)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from depot_opt.schemas import OPTIONAL_TABLES, TABLE_SCHEMAS, NetworkDataset, empty_frame

_ID_COLUMNS = {
    "depot_id", "zone_id", "product_id", "region", "status",
    "from_depot", "to_depot", "carrier_option",
}  # fmt: skip


def write_csv(dataset: NetworkDataset, directory: str | Path) -> Path:
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    for name, frame in dataset.tables().items():
        frame.to_csv(out / f"{name}.csv", index=False)
    return out


def read_csv(directory: str | Path) -> NetworkDataset:
    src = Path(directory)
    tables = {}
    for name in TABLE_SCHEMAS:
        path = src / f"{name}.csv"
        if not path.exists():
            if name in OPTIONAL_TABLES:
                tables[name] = empty_frame(name)
                continue
            raise FileNotFoundError(f"Required input table not found: {path}")
        frame = pd.read_csv(path, dtype={c: str for c in _ID_COLUMNS})
        if "period" in frame.columns:
            frame["period"] = pd.to_datetime(frame["period"]).dt.date
        tables[name] = frame
    dataset = NetworkDataset(**tables)
    dataset.validate()
    return dataset
