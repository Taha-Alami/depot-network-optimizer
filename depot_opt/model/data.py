"""Sets and parameters consumed by the optimisation model.

The model never touches DataFrames: `depot_opt.data.builder` converts the
raw tables into these plain dictionaries, which keeps the formulation code
short and makes the mapping to `docs/model_formulation.md` explicit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Key2 = tuple[str, str]
Key3 = tuple[str, str, str]


@dataclass
class ModelSets:
    depots: list[str]  # D = D_E ∪ D_C
    existing: list[str]  # D_E
    candidates: list[str]  # D_C
    zones: list[str]  # C
    products: list[str]  # P
    periods: list[str]  # T, chronologically ordered labels
    regions: list[str]  # R
    depot_region: dict[str, str | None] = field(default_factory=dict)
    arcs: list[Key2] = field(default_factory=list)  # A ⊆ C×D, allowed zone-depot pairs
    transfer_arcs: list[Key2] = field(default_factory=list)  # L ⊆ D×D, i ≠ j

    def depots_of_zone(self, c: str) -> list[str]:
        return self._by_zone.get(c, [])

    def zones_of_depot(self, d: str) -> list[str]:
        return self._by_depot.get(d, [])

    def __post_init__(self) -> None:
        self.reindex()

    def reindex(self) -> None:
        self._by_zone: dict[str, list[str]] = {}
        self._by_depot: dict[str, list[str]] = {}
        for c, d in self.arcs:
            self._by_zone.setdefault(c, []).append(d)
            self._by_depot.setdefault(d, []).append(c)


@dataclass
class ModelParameters:
    # Costs already expressed for the modelled horizon [CU]
    rent: dict[str, float]  # R_d
    fixed_operating: dict[str, float]  # F_d
    opening_cost: dict[str, float]  # O_d (amortised share)
    closing_cost: dict[str, float]  # K_d (amortised share)
    expansion_cost: dict[str, float]  # G_d per batch
    # Per-unit costs [CU/unit]
    delivery_cost: dict[Key2, float]  # c_cd
    transfer_cost: dict[Key2, float]  # τ_ij
    # Quantities [units]
    demand: dict[Key3, float]  # q_cpt
    initial_stock: dict[Key2, float]  # S0_dp
    returns: dict[Key3, float]  # ρ_dpt
    inflows: dict[Key3, float]  # ε_dpt
    capacity: dict[str, float | None]  # Cap_d (None = unlimited)
    # Service metrics per arc
    road_km: dict[Key2, float]
    linear_km: dict[Key2, float]
    travel_min: dict[Key2, float | None]
    horizon_months: float = 1.0

    def zone_volume(self, c: str) -> float:
        """Q_c: total units delivered to zone c over the horizon."""
        return self._zone_volume.get(c, 0.0)

    def __post_init__(self) -> None:
        self._zone_volume: dict[str, float] = {}
        for (c, _p, _t), q in self.demand.items():
            self._zone_volume[c] = self._zone_volume.get(c, 0.0) + q


@dataclass
class ModelData:
    sets: ModelSets
    params: ModelParameters
    period_dates: dict[str, str] = field(default_factory=dict)
