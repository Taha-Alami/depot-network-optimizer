"""Data access: ODBC loader, CSV persistence, synthetic generator, model-data builder."""

from depot_opt.data.builder import build_model_data, load_dataset, split_into_windows
from depot_opt.data.io import read_csv, write_csv
from depot_opt.data.odbc_loader import OdbcLoader
from depot_opt.data.synthetic import generate_dataset

__all__ = [
    "OdbcLoader",
    "build_model_data",
    "generate_dataset",
    "load_dataset",
    "read_csv",
    "split_into_windows",
    "write_csv",
]
