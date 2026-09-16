"""Placeholder SQL templates.

These describe *shape*, not a real warehouse: every table name is a
`{placeholder}` filled from environment variables (see `.env.example`), and
all values are bound as `?` parameters. Adapt the column expressions to your
own source system; the loader only requires that each query returns the
columns of the matching schema in `depot_opt.schemas`.
"""

DEPOTS = """
SELECT depot_id,
       status,                          -- 'existing' | 'candidate'
       region,
       latitude,
       longitude,
       rent_cost_per_month,
       fixed_operating_cost_per_month,
       opening_cost,
       closing_cost,
       capacity_units,
       expansion_cost_per_batch
FROM {depots}
WHERE is_active = ?
"""

ZONES = """
SELECT zone_id, region, latitude, longitude
FROM {zones}
"""

DEMAND = """
SELECT zone_id,
       product_id,
       CAST(delivery_date AS DATE) AS period,
       SUM(quantity)               AS quantity
FROM {demand}
WHERE delivery_date BETWEEN ? AND ?
GROUP BY zone_id, product_id, CAST(delivery_date AS DATE)
"""

INVENTORY = """
SELECT depot_id, product_id, SUM(quantity) AS quantity
FROM {inventory}
WHERE snapshot_date = ?
GROUP BY depot_id, product_id
"""

RETURNS = """
SELECT depot_id,
       product_id,
       CAST(return_date AS DATE) AS period,
       SUM(quantity)             AS quantity
FROM {returns}
WHERE return_date BETWEEN ? AND ?
GROUP BY depot_id, product_id, CAST(return_date AS DATE)
"""

INFLOWS = """
SELECT depot_id,
       product_id,
       CAST(arrival_date AS DATE) AS period,
       SUM(quantity)              AS quantity
FROM {inflows}
WHERE arrival_date BETWEEN ? AND ?
GROUP BY depot_id, product_id, CAST(arrival_date AS DATE)
"""

ZONE_DEPOT_DISTANCES = """
SELECT zone_id, depot_id, road_distance_km, travel_time_min, linear_distance_km
FROM {zone_depot_distances}
"""

DEPOT_DEPOT_COSTS = """
SELECT from_depot, to_depot, cost_per_unit
FROM {depot_depot_costs}
"""

TRANSPORT_RATES = """
SELECT band, carrier_option, rate_per_unit_km
FROM {transport_rates}
WHERE valid_from <= ? AND (valid_to IS NULL OR valid_to >= ?)
"""

# Generic lookup used by `OdbcLoader.get_zone_addresses`; the IN-list
# placeholders are generated at runtime, one `?` per id.
ZONE_ADDRESSES = """
SELECT zone_id, street, postal_code, city, country
FROM {zones}
WHERE zone_id IN ({id_placeholders})
"""
