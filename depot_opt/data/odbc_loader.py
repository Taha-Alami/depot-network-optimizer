"""ODBC data access.

`OdbcLoader` keeps a small, explicit interface (`connect`, `disconnect`,
`execute_query`, `read_table`, plus typed loaders) so that the source system
can be swapped without touching the model. Connection details and table
names come exclusively from environment variables (`DatabaseSettings`).
`pyodbc` is imported lazily, so the rest of the package works without it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date
from typing import Any

import pandas as pd

from depot_opt.config import _IDENTIFIER, DatabaseSettings
from depot_opt.data import queries
from depot_opt.schemas import NetworkDataset, empty_frame

LOGGER = logging.getLogger(__name__)


class OdbcLoader:
    """Thin wrapper around a pyodbc connection returning pandas DataFrames."""

    def __init__(self, settings: DatabaseSettings | None = None):
        self.settings = settings or DatabaseSettings.from_env()
        self.connection = None

    # ------------------------------------------------------------------ #
    # Connection handling
    # ------------------------------------------------------------------ #
    def connection_string(self) -> str:
        s = self.settings
        if not s.server or not s.database:
            raise RuntimeError("DB_SERVER and DB_DATABASE must be set in the environment.")
        server = f"{s.server},{s.port}" if s.port else s.server
        parts = [
            f"DRIVER={{{s.driver}}}",
            f"SERVER={server}",
            f"DATABASE={s.database}",
            f"Encrypt={'yes' if s.encrypt else 'no'}",
        ]
        if s.trusted_connection:
            parts.append("Trusted_Connection=yes")
        else:
            if not s.username or not s.password:
                raise RuntimeError("DB_USERNAME and DB_PASSWORD must be set (or use trusted auth).")
            parts += [f"UID={s.username}", f"PWD={s.password}"]
        return ";".join(parts) + ";"

    def connect(self) -> None:
        try:
            import pyodbc
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError(
                "Install the 'db' extra: pip install 'depot-network-optimizer[db]'"
            ) from exc
        self.connection = pyodbc.connect(self.connection_string(), timeout=60)
        LOGGER.info("Connected to the database.")

    def disconnect(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
            LOGGER.info("Database connection closed.")

    def __enter__(self) -> OdbcLoader:
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.disconnect()

    # ------------------------------------------------------------------ #
    # Generic access
    # ------------------------------------------------------------------ #
    def execute_query(self, query: str, params: Sequence[Any] | None = None) -> pd.DataFrame:
        """Run a parameterised query and return the result set."""
        if not query.strip():
            raise ValueError("Query cannot be empty.")
        if self.connection is None:
            self.connect()
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, list(params or []))
            columns = [col[0] for col in cursor.description]
            frame = pd.DataFrame.from_records(cursor.fetchall(), columns=columns)
        finally:
            cursor.close()
        LOGGER.info("Query returned %d rows.", len(frame))
        return frame

    def read_table(self, table: str, columns: Sequence[str] | None = None) -> pd.DataFrame:
        """Read a whole table (or a column subset). Identifiers are validated."""
        for name in [table, *(columns or [])]:
            if not _IDENTIFIER.match(name):
                raise ValueError(f"Invalid SQL identifier: {name!r}")
        cols = ", ".join(columns) if columns else "*"
        return self.execute_query(f"SELECT {cols} FROM {table}")

    def _table(self, key: str) -> str:
        try:
            return self.settings.tables[key]
        except KeyError as exc:
            raise RuntimeError(f"No table configured for '{key}' (see .env.example).") from exc

    def get_zone_addresses(self, zone_ids: Sequence[str]) -> pd.DataFrame:
        """Fetch postal addresses for zones, e.g. to (re-)geocode them."""
        if not zone_ids:
            raise ValueError("zone_ids cannot be empty.")
        sql = queries.ZONE_ADDRESSES.format(
            zones=self._table("zones"), id_placeholders=", ".join("?" * len(zone_ids))
        )
        return self.execute_query(sql, list(zone_ids))

    # ------------------------------------------------------------------ #
    # Typed loaders
    # ------------------------------------------------------------------ #
    def load_dataset(self, start: date, end: date, snapshot: date | None = None) -> NetworkDataset:
        """Load every model input for the horizon [start, end]."""
        snapshot = snapshot or start
        t = self._table
        optional = self.settings.tables

        def maybe(key: str, sql: str, params: list) -> pd.DataFrame:
            if key not in optional:
                return empty_frame(key)
            return self.execute_query(sql.format(**{key: t(key)}), params)

        return NetworkDataset(
            depots=self.execute_query(queries.DEPOTS.format(depots=t("depots")), [1]),
            zones=self.execute_query(queries.ZONES.format(zones=t("zones"))),
            demand=self.execute_query(queries.DEMAND.format(demand=t("demand")), [start, end]),
            inventory=self.execute_query(
                queries.INVENTORY.format(inventory=t("inventory")), [snapshot]
            ),
            zone_depot_distances=self.execute_query(
                queries.ZONE_DEPOT_DISTANCES.format(zone_depot_distances=t("zone_depot_distances"))
            ),
            transport_rates=self.execute_query(
                queries.TRANSPORT_RATES.format(transport_rates=t("transport_rates")),
                [snapshot, snapshot],
            ),
            returns=maybe("returns", queries.RETURNS, [start, end]),
            inflows=maybe("inflows", queries.INFLOWS, [start, end]),
            depot_depot_costs=maybe("depot_depot_costs", queries.DEPOT_DEPOT_COSTS, []),
        )
