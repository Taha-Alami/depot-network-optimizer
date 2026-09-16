"""depot_opt: MILP-based design of a service-depot network.

The package decides which depots to keep open, which to close, which
candidate sites to open, and how customer demand zones are assigned to
depots, while minimising total network cost.
"""

from depot_opt.config import ScenarioConfig, load_scenario
from depot_opt.model.builder import DepotNetworkModel
from depot_opt.scenarios.runner import run_scenario

__all__ = ["DepotNetworkModel", "ScenarioConfig", "load_scenario", "run_scenario"]
__version__ = "0.1.0"
