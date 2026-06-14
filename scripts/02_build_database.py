"""
02_build_database.py
====================
NYC Urban Mobility  |  STEP 2 of 4: Clean & model the data at scale

WHAT THIS SCRIPT DOES (in plain English)
----------------------------------------
Takes the raw TLC Parquet files (millions of rows, full of junk records) and builds a
clean, analysis-ready database using DuckDB. It produces ONE DuckDB database file:

    data/processed/nyc_mobility.db

...containing two tables:
  * dim_zone  - the 265 taxi zones (LocationID -> Zone name + Borough). A dimension.
  * trips     - the cleaned trips, with junk removed and useful new columns added.

WHY DuckDB? Our raw data is millions of rows. DuckDB is a free analytics database that
runs ordinary SQL directly over the Parquet files and processes tens of millions of rows
in seconds on a laptop. It reads ALL the monthly files at once via a "glob" pattern
(yellow_tripdata_*.parquet), so this exact script works for 1 month OR the full year with
no changes.

THE CLEANING RULES (and why) - applied in the big query below:
  * keep pickups inside 2023 only          -> drops stray junk dates (e.g. one from 2008)
  * trip_distance > 0 and < 100 miles       -> drops zero-distance and impossible trips
  * fare_amount > 0 and total_amount > 0     -> drops free/negative/refund records
  * duration between 1 and 180 minutes       -> drops zero-time and absurdly long trips
  * speed (mph) > 0 and <= 80                 -> drops GPS-error "teleport" speeds
We DON'T drop trips just for a missing passenger_count - the ride still happened; that
column simply isn't needed to count demand.

THE NEW COLUMNS WE ADD (so analysis is easy later):
  pickup_date, pickup_hour, pickup_day (Mon..Sun), pickup_month, trip duration in
  minutes, average speed (mph), pickup/dropoff zone + borough names, an airport flag,
  and a friendly payment label.

HOW TO RUN IT:
  python scripts/02_build_database.py
"""

# ---------------------------------------------------------------------------
# SECTION 0 — Imports and paths
# ---------------------------------------------------------------------------
import duckdb
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = PROCESSED_DIR / "nyc_mobility.db"

# A "glob" pattern that matches every monthly Yellow Taxi file we've downloaded.
# The * is a wildcard: 1 file today, up to 12 when we scale to the full year.
TRIPS_GLOB = str(RAW_DIR / "yellow_tripdata_*.parquet")
ZONE_CSV = str(RAW_DIR / "taxi_zone_lookup.csv")

# Airport taxi-zone IDs (so we can flag airport trips): Newark=1, JFK=132, LaGuardia=138.
AIRPORT_ZONES = "(1, 132, 138)"


# ---------------------------------------------------------------------------
# SECTION 1 — Connect (fresh rebuild each run)
# ---------------------------------------------------------------------------
print("=" * 70)
print("STEP 2: CLEANING & BUILDING THE DATABASE (DuckDB)")
print("=" * 70)

# Start clean so re-running always gives the same result.
if DB_PATH.exists():
    DB_PATH.unlink()
con = duckdb.connect(str(DB_PATH))

# How many raw rows are we starting from? (DuckDB counts straight from Parquet.)
raw_count = con.execute(f"SELECT COUNT(*) FROM read_parquet('{TRIPS_GLOB}')").fetchone()[0]
print(f"\nRaw trips across all files : {raw_count:,}")


# ---------------------------------------------------------------------------
# SECTION 2 — Build the zone dimension
# ---------------------------------------------------------------------------
# read_csv_auto figures out the columns/types automatically. The lookup has:
# LocationID, Borough, Zone, service_zone.
con.execute(f"""
    CREATE TABLE dim_zone AS
    SELECT
        LocationID,
        Borough,
        Zone        AS zone_name,
        service_zone
    FROM read_csv_auto('{ZONE_CSV}')
""")
zone_count = con.execute("SELECT COUNT(*) FROM dim_zone").fetchone()[0]
print(f"Built dim_zone             : {zone_count} zones")


# ---------------------------------------------------------------------------
# SECTION 3 — Build the cleaned trips table (the heart of this step)
# ---------------------------------------------------------------------------
# We read the raw Parquet, compute helper columns, JOIN the zone names, label the
# payment type and airport flag, and FILTER OUT the junk - all in one query.
con.execute(f"""
    CREATE TABLE trips AS
    WITH raw AS (
        -- Read every monthly file and compute trip duration in seconds once.
        SELECT
            *,
            date_diff('second', tpep_pickup_datetime, tpep_dropoff_datetime) AS dur_sec
        FROM read_parquet('{TRIPS_GLOB}')
    ),
    derived AS (
        -- Turn seconds into minutes, and compute average speed (mph).
        -- NULLIF(..., 0) avoids divide-by-zero: it returns NULL instead of erroring.
        SELECT
            *,
            dur_sec / 60.0                              AS duration_min,
            trip_distance / NULLIF(dur_sec / 3600.0, 0) AS mph
        FROM raw
    )
    SELECT
        -- Timestamps + the time features we'll group by in the analysis.
        d.tpep_pickup_datetime                          AS pickup_ts,
        d.tpep_dropoff_datetime                         AS dropoff_ts,
        CAST(d.tpep_pickup_datetime AS DATE)            AS pickup_date,
        hour(d.tpep_pickup_datetime)                    AS pickup_hour,     -- 0..23
        dayname(d.tpep_pickup_datetime)                 AS pickup_day,      -- 'Monday'..
        isodow(d.tpep_pickup_datetime)                  AS pickup_dow_num,  -- 1=Mon..7=Sun (for sorting)
        monthname(d.tpep_pickup_datetime)               AS pickup_month,    -- 'January'..

        -- Trip facts.
        d.passenger_count,
        d.trip_distance,
        ROUND(d.duration_min, 1)                        AS duration_min,
        ROUND(d.mph, 1)                                 AS mph,

        -- Locations, with friendly names joined in from dim_zone.
        d.PULocationID                                  AS pickup_location_id,
        puz.zone_name                                   AS pickup_zone,
        puz.Borough                                     AS pickup_borough,
        d.DOLocationID                                  AS dropoff_location_id,
        doz.zone_name                                   AS dropoff_zone,
        doz.Borough                                     AS dropoff_borough,

        -- Was either end of the trip an airport? Great for the airport-vs-city story.
        (d.PULocationID IN {AIRPORT_ZONES}
         OR d.DOLocationID IN {AIRPORT_ZONES})          AS is_airport_trip,

        -- Money. (Remember: tips are recorded for CARD payments only.)
        d.payment_type,
        CASE d.payment_type
            WHEN 1 THEN 'Card' WHEN 2 THEN 'Cash' WHEN 3 THEN 'No charge'
            WHEN 4 THEN 'Dispute' ELSE 'Other'
        END                                             AS payment_label,
        d.fare_amount,
        d.tip_amount,
        d.tolls_amount,
        d.total_amount,
        d.airport_fee

    FROM derived d
    LEFT JOIN dim_zone puz ON d.PULocationID = puz.LocationID
    LEFT JOIN dim_zone doz ON d.DOLocationID = doz.LocationID

    -- ===== THE CLEANING FILTER (documented rules from the data-quality scan) =====
    WHERE d.tpep_pickup_datetime >= TIMESTAMP '2023-01-01'
      AND d.tpep_pickup_datetime <  TIMESTAMP '2024-01-01'   -- 2023 only
      AND d.trip_distance > 0  AND d.trip_distance < 100      -- sane distance
      AND d.fare_amount   > 0  AND d.total_amount  > 0        -- real, paid trips
      AND d.duration_min >= 1  AND d.duration_min <= 180      -- 1 min .. 3 hours
      AND d.mph > 0            AND d.mph <= 80                 -- believable speed
""")

clean_count = con.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
dropped = raw_count - clean_count
print(f"Built trips (cleaned)      : {clean_count:,}")


# ---------------------------------------------------------------------------
# SECTION 4 — Quality report (prove the cleaning was sensible)
# ---------------------------------------------------------------------------
print("\n" + "-" * 70)
print("DATA QUALITY REPORT")
print("-" * 70)
print(f"Rows kept    : {clean_count:,}  ({clean_count/raw_count*100:.1f}% of raw)")
print(f"Rows removed : {dropped:,}  ({dropped/raw_count*100:.1f}% of raw)")

# A few sanity checks on the cleaned data: the ranges should now look reasonable.
checks = con.execute("""
    SELECT
        MIN(pickup_date)            AS first_day,
        MAX(pickup_date)            AS last_day,
        ROUND(AVG(trip_distance),2) AS avg_miles,
        ROUND(AVG(duration_min),1)  AS avg_minutes,
        ROUND(AVG(fare_amount),2)   AS avg_fare,
        ROUND(AVG(mph),1)           AS avg_mph
    FROM trips
""").fetchone()
print(f"\nDate range   : {checks[0]} to {checks[1]}")
print(f"Avg trip     : {checks[2]} miles, {checks[3]} min, ${checks[4]} fare, {checks[5]} mph")

# Did the zone join work? Count trips whose pickup borough couldn't be matched.
unmatched = con.execute(
    "SELECT COUNT(*) FROM trips WHERE pickup_borough IS NULL"
).fetchone()[0]
print(f"Trips with unmatched pickup zone : {unmatched:,}")

print("\nSample of the cleaned data:")
sample = con.execute("""
    SELECT pickup_date, pickup_hour, pickup_day, pickup_borough, pickup_zone,
           trip_distance, duration_min, mph, fare_amount, tip_amount, is_airport_trip
    FROM trips
    LIMIT 5
""").df()
print(sample.to_string(index=False))

con.close()

print("\n" + "=" * 70)
print(f"DONE. Database ready at: data/processed/{DB_PATH.name}")
print("=" * 70)
print("\nNEXT (Step 3): analytical SQL on the trips table -> Power BI summary tables.")
