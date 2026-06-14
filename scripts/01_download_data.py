"""
01_download_data.py
===================
NYC Urban Mobility  |  STEP 1 of 4: Download the real data

WHAT THIS SCRIPT DOES (in plain English)
----------------------------------------
Downloads real NYC taxi trip data, published by the New York City Taxi & Limousine
Commission (TLC), straight from their official servers into data/raw/:

  * Yellow Taxi trip records, one Parquet file per month (each ~3 million trips).
  * The Taxi Zone lookup table (turns numeric zone IDs into real neighbourhood
    and borough names).

WHY PARQUET? The TLC publishes these files in "Parquet" format - a compact, columnar
file built for big data. One month is ~47 MB but holds ~3 million rows; the same data
as CSV would be several times larger and slower. pandas/DuckDB read Parquet natively.

BUILD STRATEGY (important):
  We start in TEST MODE = just ONE month, so we can build and check the whole pipeline
  quickly. Once everything works end-to-end, flip TEST_MODE to False to download the
  full year (~38 million trips) and re-run the pipeline at full scale.

HOW TO RUN IT:
  python scripts/01_download_data.py
"""

# ---------------------------------------------------------------------------
# SECTION 0 — Imports, settings, and file paths
# ---------------------------------------------------------------------------
import requests              # downloads files over the internet
from pathlib import Path     # safe, cross-platform file paths

# --- THE ONE SWITCH THAT CONTROLS SCALE ---------------------------------------
# True  -> download just the first month (fast, for building/testing the pipeline)
# False -> download the whole year 2023 (~38 million trips, ~600 MB)
TEST_MODE = False
# ------------------------------------------------------------------------------

YEAR = 2023
ALL_MONTHS = [f"{YEAR}-{month:02d}" for month in range(1, 13)]  # ['2023-01', ..., '2023-12']
MONTHS = ALL_MONTHS[:1] if TEST_MODE else ALL_MONTHS            # first month, or all 12

# The TLC serves files from this base address. The file name pattern is fixed, e.g.
# yellow_tripdata_2023-01.parquet — we just swap in each month.
TRIP_BASE_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data"
ZONE_LOOKUP_URL = "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv"

# Save everything under this project's data/raw/ folder.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)   # create the folder if it doesn't exist


# ---------------------------------------------------------------------------
# SECTION 1 — A small helper to download one file with a progress readout
# ---------------------------------------------------------------------------
def download_file(url: str, destination: Path) -> None:
    """Download `url` to `destination`, streaming in chunks and printing progress.

    We stream (download in pieces) instead of loading the whole file into memory at
    once, because these files are large. We also SKIP the download if the file is
    already there, so re-running the script is cheap and safe.
    """
    if destination.exists():
        size_mb = destination.stat().st_size / 1_000_000
        print(f"   already have {destination.name} ({size_mb:.1f} MB) - skipping")
        return

    # stream=True means "don't download it all at once; let me read it in chunks".
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()                       # error out on a bad URL/HTTP error
        total = int(response.headers.get("content-length", 0))   # file size in bytes
        downloaded = 0
        # Write the bytes to disk as they arrive, 1 MB at a time.
        with open(destination, "wb") as f:
            for chunk in response.iter_content(chunk_size=1_000_000):
                f.write(chunk)
                downloaded += len(chunk)
                # \r returns to the start of the line so the % updates in place.
                if total:
                    pct = downloaded / total * 100
                    print(f"\r   downloading {destination.name}: {pct:5.1f}%", end="")
        print(f"\r   downloaded  {destination.name} ({downloaded/1_000_000:.1f} MB)        ")


# ---------------------------------------------------------------------------
# SECTION 2 — Download the trip files and the zone lookup
# ---------------------------------------------------------------------------
print("=" * 70)
print(f"STEP 1: DOWNLOADING NYC TLC DATA  ({'TEST MODE - 1 month' if TEST_MODE else 'FULL YEAR'})")
print("=" * 70)

# 2a. The monthly Yellow Taxi trip files.
print(f"\nTrip files to fetch: {len(MONTHS)}  ({', '.join(MONTHS)})")
for month in MONTHS:
    file_name = f"yellow_tripdata_{month}.parquet"
    download_file(f"{TRIP_BASE_URL}/{file_name}", RAW_DIR / file_name)

# 2b. The zone lookup (small CSV that names each of the 265 pickup/dropoff zones).
print("\nZone lookup:")
download_file(ZONE_LOOKUP_URL, RAW_DIR / "taxi_zone_lookup.csv")


# ---------------------------------------------------------------------------
# SECTION 3 — Quick confirmation of what we downloaded
# ---------------------------------------------------------------------------
print("\n" + "-" * 70)
print("DOWNLOAD COMPLETE — files now in data/raw/:")
print("-" * 70)
total_mb = 0
for f in sorted(RAW_DIR.iterdir()):
    mb = f.stat().st_size / 1_000_000
    total_mb += mb
    print(f"   {f.name:40} {mb:8.1f} MB")
print(f"\nTotal downloaded: {total_mb:.1f} MB")

print("\n" + "=" * 70)
print("DONE.")
print("=" * 70)
print("\nNEXT (Step 2): clean & aggregate the trips at scale with DuckDB.")
if TEST_MODE:
    print("(Currently TEST MODE = 1 month. After the pipeline works, set TEST_MODE = False")
    print(" at the top of this script and re-run to fetch the full year.)")
