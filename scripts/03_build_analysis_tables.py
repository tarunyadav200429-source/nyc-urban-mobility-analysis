"""
03_build_analysis_tables.py
===========================
NYC Urban Mobility  |  STEP 3 of 4: Analyse & export Power BI tables

WHAT THIS SCRIPT DOES (in plain English)
----------------------------------------
Runs a set of analytical SQL queries against the cleaned `trips` table in our DuckDB
database, and saves each result as a small, tidy CSV in data/processed/. Power BI will
import these CSVs to build the dashboard.

WHY EXPORT SMALL SUMMARY TABLES (instead of the raw millions)?
  Power BI on a laptop struggles with tens of millions of rows. The professional pattern
  is: do the heavy aggregation in the database (DuckDB, fast), then hand Power BI compact
  pre-summarised tables (a few hundred rows each). The dashboard then flies.

EACH QUERY = ONE BUSINESS QUESTION:
  1. demand_by_hour_dow   -> WHEN is demand highest? (hour x day-of-week heatmap)
  2. daily_trend          -> how do trips/revenue move day to day across the period?
  3. by_borough           -> WHERE is demand & revenue concentrated? (by borough)
  4. by_zone              -> which specific pickup zones are the busiest?
  5. airport_vs_city      -> how do airport trips differ from city trips?
  6. payment_mix          -> card vs cash split, and tipping behaviour
  7. fare_by_distance     -> the economics: how fare scales with trip distance
  8. kpi_summary          -> the headline numbers for the dashboard's KPI cards

HOW TO RUN IT:
  python scripts/03_build_analysis_tables.py
"""

import duckdb
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DB_PATH = PROCESSED_DIR / "nyc_mobility.db"

# Open the database read-only (we're only querying, not changing it).
con = duckdb.connect(str(DB_PATH), read_only=True)


def run_and_save(name: str, sql: str, preview_rows: int = 6) -> None:
    """Run one query, print a short preview, and save the full result to a CSV.

    `name` becomes the output file name (data/processed/<name>.csv) and the heading.
    """
    df = con.execute(sql).df()              # run the SQL, get the result as a table
    out_path = PROCESSED_DIR / f"{name}.csv"
    df.to_csv(out_path, index=False)        # save for Power BI (no junk index column)
    print("\n" + "=" * 70)
    print(f"{name}.csv   ({len(df)} rows saved)")
    print("=" * 70)
    print(df.head(preview_rows).to_string(index=False))


print("STEP 3: BUILDING ANALYSIS TABLES FOR POWER BI")

# ---------------------------------------------------------------------------
# 1. WHEN is demand highest?  Hour-of-day x day-of-week -> a classic heatmap.
# ---------------------------------------------------------------------------
run_and_save("demand_by_hour_dow", """
    SELECT
        pickup_dow_num,                          -- 1=Mon..7=Sun (keeps days in order)
        pickup_day,                              -- 'Monday'.. (the label)
        pickup_hour,                             -- 0..23
        COUNT(*)                  AS trips,
        ROUND(AVG(fare_amount),2) AS avg_fare
    FROM trips
    GROUP BY pickup_dow_num, pickup_day, pickup_hour
    ORDER BY pickup_dow_num, pickup_hour
""")

# ---------------------------------------------------------------------------
# 2. Daily trend: trips & revenue per calendar day.
# ---------------------------------------------------------------------------
run_and_save("daily_trend", """
    SELECT
        pickup_date,
        COUNT(*)                   AS trips,
        ROUND(SUM(total_amount),0) AS revenue,
        ROUND(AVG(fare_amount),2)  AS avg_fare
    FROM trips
    GROUP BY pickup_date
    ORDER BY pickup_date
""")

# ---------------------------------------------------------------------------
# 3. WHERE: demand & revenue by pickup borough.
#    Tip % is computed for CARD trips only (cash tips aren't recorded).
# ---------------------------------------------------------------------------
run_and_save("by_borough", """
    SELECT
        pickup_borough,
        COUNT(*)                    AS trips,
        ROUND(SUM(total_amount),0)  AS total_revenue,
        ROUND(AVG(fare_amount),2)   AS avg_fare,
        ROUND(AVG(trip_distance),2) AS avg_distance_mi,
        ROUND(AVG(CASE WHEN payment_label = 'Card'
                       THEN tip_amount / NULLIF(fare_amount,0) * 100 END), 1) AS avg_tip_pct_card
    FROM trips
    GROUP BY pickup_borough
    ORDER BY trips DESC
""")

# ---------------------------------------------------------------------------
# 4. Busiest pickup zones (the specific neighbourhoods).
# ---------------------------------------------------------------------------
run_and_save("by_zone", """
    SELECT
        pickup_zone,
        pickup_borough,
        COUNT(*)                   AS trips,
        ROUND(SUM(total_amount),0) AS revenue,
        ROUND(AVG(fare_amount),2)  AS avg_fare
    FROM trips
    GROUP BY pickup_zone, pickup_borough
    ORDER BY trips DESC
""")

# ---------------------------------------------------------------------------
# 5. Airport trips vs everything else.
# ---------------------------------------------------------------------------
run_and_save("airport_vs_city", """
    SELECT
        CASE WHEN is_airport_trip THEN 'Airport trip' ELSE 'City trip' END AS trip_type,
        COUNT(*)                    AS trips,
        ROUND(AVG(trip_distance),2) AS avg_distance_mi,
        ROUND(AVG(duration_min),1)  AS avg_duration_min,
        ROUND(AVG(fare_amount),2)   AS avg_fare,
        ROUND(AVG(total_amount),2)  AS avg_total,
        ROUND(AVG(CASE WHEN payment_label = 'Card' THEN tip_amount END), 2) AS avg_tip_card
    FROM trips
    GROUP BY is_airport_trip
""")

# ---------------------------------------------------------------------------
# 6. Payment mix and tipping. SUM(COUNT(*)) OVER () = the grand total, so we can
#    show each payment type as a % of all trips (a window function).
# ---------------------------------------------------------------------------
run_and_save("payment_mix", """
    SELECT
        payment_label,
        COUNT(*)                                              AS trips,
        ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1)    AS pct_of_trips,
        ROUND(AVG(fare_amount),2)                             AS avg_fare,
        ROUND(AVG(tip_amount),2)                              AS avg_tip
    FROM trips
    GROUP BY payment_label
    ORDER BY trips DESC
""")

# ---------------------------------------------------------------------------
# 7. Trip economics: how does fare scale with distance? Bin distance to whole
#    miles (capped at 30) so we get a clean fare-vs-distance curve.
# ---------------------------------------------------------------------------
run_and_save("fare_by_distance", """
    SELECT
        ROUND(trip_distance)        AS distance_mi,
        COUNT(*)                    AS trips,
        ROUND(AVG(fare_amount),2)   AS avg_fare,
        ROUND(AVG(total_amount),2)  AS avg_total,
        ROUND(AVG(duration_min),1)  AS avg_duration_min
    FROM trips
    WHERE trip_distance <= 30
    GROUP BY distance_mi
    ORDER BY distance_mi
""")

# ---------------------------------------------------------------------------
# 8. KPI summary: the one-row headline numbers for the dashboard cards.
# ---------------------------------------------------------------------------
run_and_save("kpi_summary", """
    SELECT
        COUNT(*)                                                       AS total_trips,
        ROUND(SUM(total_amount),0)                                     AS total_revenue,
        ROUND(AVG(fare_amount),2)                                      AS avg_fare,
        ROUND(AVG(trip_distance),2)                                    AS avg_distance_mi,
        ROUND(AVG(duration_min),1)                                     AS avg_duration_min,
        ROUND(AVG(mph),1)                                              AS avg_mph,
        ROUND(SUM(CASE WHEN is_airport_trip THEN 1 ELSE 0 END)*100.0/COUNT(*),1) AS pct_airport_trips,
        ROUND(SUM(CASE WHEN payment_label='Card' THEN 1 ELSE 0 END)*100.0/COUNT(*),1) AS pct_paid_by_card
    FROM trips
""")

con.close()

print("\n" + "=" * 70)
print(f"DONE. {8} summary tables written to data/processed/ (ready for Power BI).")
print("=" * 70)
print("\nNEXT (Step 4): build the Power BI dashboard from these CSVs.")
