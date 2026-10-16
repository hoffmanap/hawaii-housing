"""
Pull parcel, zoning, and State Land Use District data from Hawaii's statewide
GIS service (geodata.hawaii.gov/arcgis/rest/services/ParcelsZoning) for all
four counties, and save to data/raw/ (gitignored — see data/README.md). This
is the Challenge's required "automated data acquisition process": no parcel
data is committed to the repo.

OAHU BUILDING-CHARACTERISTICS GAP -- CLOSED 2026-10-14, read this before
writing eligibility logic:
The statewide ParcelsZoning layers (Oahu parcels especially) are cadastral/
boundary data only. The fix is a separate Honolulu BFS Real Property
Assessment Feature Service ("CadastralTables"), joined to parcels on
tmk/parid:
  - LEGDAT (layer 11): one row PER LEGAL UNIT on a parcel, not one row per
    parcel. `unitno` is a condo unit label ("508"), '0' for a non-condo
    parcel -- existing_units for a TMK = COUNT of LEGDAT rows sharing that
    tmk, not a field value. `totsqft` is lot/land area (confirmed against
    real single-family parcels: 6,285-9,332 sq ft range), analogous to
    targetlotarea in the El Paso build -- NOT building floor area.
  - ASMTPITT (layer 10): `buildingvalue` is an assessed-dollar improvement
    proxy (same role as Hawaii County/Maui's bldgvalue). `ovrclass` is a
    numeric tax-class code (e.g. "11" observed) -- NOT YET DECODED. No
    coded-value domain is attached to the field; hypothesis (unconfirmed)
    is that it maps to Hawaii's standard 16-category statewide land-use
    classification (Residential, Apartment, Commercial, Agricultural, etc.
    -- the same categories seen in the counties' published land-use-class
    record counts). Needed before ovrclass can gate eligibility by land use;
    until decoded, improvement presence (buildingvalue > 0) and unit count
    (LEGDAT row count) are the two usable signals for Oahu.
  - Statewide (non-Oahu) equivalent assessor sources for Hawaii, Kauai, and
    Maui counties' unit counts specifically are still NOT identified --
    pittcode on those counties' own parcel layers (see data/DATA_SOURCES.md)
    is the closest proxy there, same decode gap.
"""
import argparse
import csv
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

STATE_BASE_URL = "https://geodata.hawaii.gov/arcgis/rest/services/ParcelsZoning/MapServer"
HONOLULU_BFS_BASE_URL = (
    "https://services.arcgis.com/tNJpAOha4mODLkXz/arcgis/rest/services/CadastralTables/FeatureServer"
)
RAW_DIR = Path(__file__).parent.parent / "data" / "raw"

# Google/Microsoft Open Buildings (VIDA/Source Cooperative), combined, public,
# GeoParquet, partitioned by country. There is no Hawaii-only or state-level
# file -- the USA partition covers the whole country -- so this is queried
# with a bounding-box filter via DuckDB's spatial+httpfs extensions, which
# prunes GeoParquet row groups by their bbox statistics rather than
# downloading the full nationwide file. See data/DATA_SOURCES.md.
BUILDING_FOOTPRINTS_URL = (
    "https://data.source.coop/vida/google-microsoft-open-buildings/"
    "geoparquet/by_country/country_iso=USA/USA.parquet"
)
# Generous statewide bounding box covering all main Hawaiian islands
# (Niihau in the west to the Big Island in the east).
HAWAII_BBOX = {"xmin": -160.6, "ymin": 18.7, "xmax": -154.6, "ymax": 22.5}

# name: (base_url, layer_id, max records per page). Page sizes are each
# service's own confirmed maxRecordCount, checked via layer metadata query.
LAYERS = {
    "parcels_statewide_tmk": (STATE_BASE_URL, 25, 1000),
    "parcels_hawaii_county": (STATE_BASE_URL, 5, 1000),
    "parcels_oahu": (STATE_BASE_URL, 11, 1000),
    "parcels_kauai": (STATE_BASE_URL, 9, 1000),
    "parcels_maui": (STATE_BASE_URL, 30, 1000),
    "zoning_hawaii_county": (STATE_BASE_URL, 2, 1000),
    "zoning_honolulu": (STATE_BASE_URL, 3, 1000),
    "zoning_kauai": (STATE_BASE_URL, 29, 1000),
    "zoning_maui": (STATE_BASE_URL, 33, 1000),
    "state_land_use_districts": (STATE_BASE_URL, 20, 1000),
    "zoning_ohana_honolulu": (
        "https://services.arcgis.com/tNJpAOha4mODLkXz/arcgis/rest/services/Zoning_Reg/FeatureServer",
        3,
        2000,
    ),  # confirmed small (130 features) -- a specific ohana regulatory overlay, not citywide ADU eligibility
    "legdat_honolulu": (HONOLULU_BFS_BASE_URL, 11, 2000),  # unit-level legal data; join key: tmk/parid
    "asmtpitt_honolulu": (HONOLULU_BFS_BASE_URL, 10, 2000),  # tax-class + building value; join key: tmk/parid
}

# Census 2020 tract boundaries, needed to roll parcel-level output up to the
# Challenge's required tract-level aggregation. TIGERweb's Tracts_Blocks
# layer 4 ("Census Tracts"), filtered to Hawaii (STATE='15'). This one needs
# a WHERE clause rather than "1=1", so it's fetched by name directly in
# main() rather than through fetch_layer()'s generic LAYERS loop.
TIGERWEB_TRACTS_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Tracts_Blocks/MapServer"
)
TIGERWEB_TRACTS_LAYER = 4

# Census county FIPS codes for the four Hawaii counties (state FIPS 15).
# Honolulu County = city & county of Honolulu = Oahu; Hawaii County = the
# Big Island.
COUNTY_FIPS = {"honolulu": "003", "hawaii": "001", "kauai": "007", "maui": "009"}

# ACS 5-year detailed table B08301 (Means of Transportation to Work) variable
# codes actually used here, confirmed against the Census API's own variable
# listing 2026-10-14 (B08301_002E is "Car, truck, or van:" TOTAL, i.e.
# drove-alone + carpooled combined, NOT "drove alone" alone -- that's
# B08301_003E. An earlier pass mislabeled 002E as drove-alone; this matters
# because "non-car commute share" has to exclude ALL car/truck/van trips,
# carpooled included, not just solo drivers, to match what Sohoni & Lee 2025
# (see docs/MODEL_AND_ASSUMPTIONS.md) mean by "alternative modes.")
ACS_B08301_VARS = ["B08301_001E", "B08301_002E", "B08301_021E"]  # total, car/truck/van, worked from home
ACS_B01003_VARS = ["B01003_001E"]  # total population
ACS_HOUSING_VALUE_VARS = ["B25077_001E", "B25064_001E"]  # median home value, median gross rent
# Census's sentinel for "estimate cannot be computed" (insufficient sample) --
# appears literally as -666666666 in ACS detailed-table responses.
ACS_NULL_SENTINEL = -666666666


def fetch_acs_commute_density(county: str, out_path: Path) -> int:
    """
    Tract-level ACS 5-year commute-mode and population data for one Hawaii
    county, joined with tract land area (from the already-fetched TIGERweb
    census_tracts.json) to compute population density -- the two proxy
    variables Sohoni & Lee (2025) use to explain how much parking supply a
    city actually keeps after removing minimum parking requirements (see
    docs/MODEL_AND_ASSUMPTIONS.md, "Parking Requirement Reduction" section).
    Requires a free Census API key (https://api.census.gov/data/key_signup.html)
    in the CENSUS_API_KEY environment variable -- the Census API returns a
    "Missing Key" error page (HTTP 200, not JSON) for every vintage without
    one as of 2026-10-14, so this isn't optional the way the other ArcGIS
    pulls in this script are.
    """
    key = os.environ.get("CENSUS_API_KEY")
    if not key:
        raise RuntimeError(
            "CENSUS_API_KEY environment variable not set -- get a free key at "
            "https://api.census.gov/data/key_signup.html and set it in .env "
            "(gitignored) or the environment before running this layer."
        )
    county_fips = COUNTY_FIPS[county]
    get_vars = ",".join(["NAME", *ACS_B08301_VARS, *ACS_B01003_VARS, *ACS_HOUSING_VALUE_VARS])
    url = (
        "https://api.census.gov/data/2022/acs/acs5"
        f"?get={get_vars}&for=tract:*&in=state:15+county:{county_fips}&key={key}"
    )
    payload = _get_json_with_retries(url)
    if not isinstance(payload, list) or len(payload) < 2:
        raise RuntimeError(f"Unexpected Census API response for {county}: {str(payload)[:300]}")
    cols, rows = payload[0], payload[1:]

    tracts_path = out_path.parent / "census_tracts.json"
    if not tracts_path.exists():
        raise RuntimeError(
            f"{tracts_path} not found -- run `fetch_data.py --layer census_tracts` first "
            "(tract land area comes from there, not the Census API's own tract list)."
        )
    tract_features = json.loads(tracts_path.read_text())["features"]
    area_by_geoid = {f["properties"]["GEOID"]: f["properties"]["AREALAND"] for f in tract_features}
    sqm_per_sqmi = 2_589_988.11

    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "GEOID", "name", "population", "commuters_excl_wfh", "pct_alt_mode_commute",
                "density_per_sqmi", "median_home_value_usd", "median_gross_rent_usd",
            ]
        )
        for row in rows:
            record = dict(zip(cols, row))
            geoid = record["state"] + record["county"] + record["tract"]
            total = int(record["B08301_001E"])
            car_truck_van = int(record["B08301_002E"])
            worked_from_home = int(record["B08301_021E"])
            population = int(record["B01003_001E"])
            commuters = total - worked_from_home
            if commuters <= 0 or population <= 0:
                continue
            alt_mode = total - car_truck_van - worked_from_home
            pct_alt_mode = 100 * alt_mode / commuters
            aland_sqm = area_by_geoid.get(geoid)
            density = (population / (aland_sqm / sqm_per_sqmi)) if aland_sqm else None
            median_home_value = int(record["B25077_001E"])
            median_gross_rent = int(record["B25064_001E"])
            median_home_value = None if median_home_value == ACS_NULL_SENTINEL else median_home_value
            median_gross_rent = None if median_gross_rent == ACS_NULL_SENTINEL else median_gross_rent
            writer.writerow(
                [geoid, record["NAME"], population, commuters, round(pct_alt_mode, 2), density,
                 median_home_value, median_gross_rent]
            )
            written += 1
    return written


# ACS 5-year detailed table B19001 (Household Income in the Past 12 Months,
# in 2022 inflation-adjusted dollars) -- the 16 income brackets plus total,
# confirmed against the Census API's own variable listing 2026-10-15.
# Pulled for the affordability-constrained-demand gate the user asked for
# 2026-10-15: feasibility (kauai_development_fee_reduction_feasibility())
# only tests whether a developer's return pencils at the comps-implied
# sale price/rent; it says nothing about how many actual households in a
# tract could afford that price. A tract median (B19013, already
# available via B25077's sibling table) only answers that for the
# "typical" household -- the bracket-level distribution here lets
# tract_affordability_constrained_capacity() compute a real percentage of
# households clearing a required-income threshold, via linear
# interpolation within whichever bracket the threshold falls in, instead
# of a median-based yes/no.
ACS_B19001_BRACKETS = [
    ("B19001_002E", 0, 10_000),
    ("B19001_003E", 10_000, 15_000),
    ("B19001_004E", 15_000, 20_000),
    ("B19001_005E", 20_000, 25_000),
    ("B19001_006E", 25_000, 30_000),
    ("B19001_007E", 30_000, 35_000),
    ("B19001_008E", 35_000, 40_000),
    ("B19001_009E", 40_000, 45_000),
    ("B19001_010E", 45_000, 50_000),
    ("B19001_011E", 50_000, 60_000),
    ("B19001_012E", 60_000, 75_000),
    ("B19001_013E", 75_000, 100_000),
    ("B19001_014E", 100_000, 125_000),
    ("B19001_015E", 125_000, 150_000),
    ("B19001_016E", 150_000, 200_000),
    ("B19001_017E", 200_000, None),  # "$200,000 or more" -- open-ended top bracket
]
ACS_B19001_VARS = ["B19001_001E"] + [v for v, _, _ in ACS_B19001_BRACKETS]  # total + 16 brackets


def fetch_acs_income_distribution(county: str, out_path: Path) -> int:
    """
    Tract-level ACS 5-year household-income distribution (B19001) for one
    Hawaii county -- 16 income brackets plus the total household count.
    Same CENSUS_API_KEY requirement and vintage (2022 ACS 5-year) as
    fetch_acs_commute_density(), so these two layers' income-dollar figures
    are on the same inflation-adjustment basis.

    Written wide (one column per bracket, raw household counts) rather
    than pre-computed into a "% above $X" figure, since the required-
    income threshold in tract_affordability_constrained_capacity() changes
    by scenario/typology -- the interpolation has to happen downstream,
    against whatever threshold that scenario's sale price or rent implies.
    """
    key = os.environ.get("CENSUS_API_KEY")
    if not key:
        raise RuntimeError(
            "CENSUS_API_KEY environment variable not set -- get a free key at "
            "https://api.census.gov/data/key_signup.html and set it in .env "
            "(gitignored) or the environment before running this layer."
        )
    county_fips = COUNTY_FIPS[county]
    get_vars = ",".join(["NAME", *ACS_B19001_VARS])
    url = (
        "https://api.census.gov/data/2022/acs/acs5"
        f"?get={get_vars}&for=tract:*&in=state:15+county:{county_fips}&key={key}"
    )
    payload = _get_json_with_retries(url)
    if not isinstance(payload, list) or len(payload) < 2:
        raise RuntimeError(f"Unexpected Census API response for {county}: {str(payload)[:300]}")
    cols, rows = payload[0], payload[1:]

    bracket_cols = [f"hh_income_{lo}_{hi if hi is not None else 'plus'}" for _, lo, hi in ACS_B19001_BRACKETS]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["GEOID", "name", "total_households", *bracket_cols])
        for row in rows:
            record = dict(zip(cols, row))
            geoid = record["state"] + record["county"] + record["tract"]
            total = int(record["B19001_001E"])
            if total == ACS_NULL_SENTINEL or total <= 0:
                continue
            bracket_counts = []
            for var, _, _ in ACS_B19001_BRACKETS:
                val = int(record[var])
                bracket_counts.append(0 if val == ACS_NULL_SENTINEL else val)
            writer.writerow([geoid, record["NAME"], total, *bracket_counts])
            written += 1
    return written


HDOT_HPMS_ROADS_LAYER_URL = "https://geodata.hawaii.gov/arcgis/rest/services/Transportation/MapServer/12"
ARTERIAL_F_SYSTEM_VALUES = ("Principal Arterial", "Minor Arterial")


def fetch_kauai_arterial_roads(out_path: Path) -> int:
    """
    Kauai's Principal- and Minor-Arterial road segments (HDOT's Hawaii
    Performance Monitoring System Roads layer -- FHWA federal functional
    classification, geodata.hawaii.gov/.../Transportation/MapServer/12),
    confirmed via the layer's own f_system_t field (no fudge factor: these
    are the two tiers labeled "Principal Arterial" and "Minor Arterial" in
    the service's own renderer/legend, applied uniformly statewide, not a
    threshold this project invented).

    Used for the Transit-Oriented Density scenario's corridor criterion:
    per the user's 2026-10-14 correction, major-stop proximity alone was
    too broad (half the county's bus stops sit on some road, which made
    ~40% of Kauai's parcels "near transit") -- a parcel must ALSO be near
    one of these arterial segments, so upzoning concentrates along the
    corridors Kauai Bus's mainline routes actually run (Kuhio Hwy,
    Kaumualii Hwy), not every shuttle-loop street.
    """
    params = {
        "where": "island='Kauai' AND (" + " OR ".join(f"f_system_t='{v}'" for v in ARTERIAL_F_SYSTEM_VALUES) + ")",
        "outFields": "route_name,f_system_t,island",
        "f": "geojson",
        "returnGeometry": "true",
        "resultRecordCount": 2000,
    }
    url = f"{HDOT_HPMS_ROADS_LAYER_URL}/query?{urllib.parse.urlencode(params)}"
    payload = _get_json_with_retries(url)
    if "error" in payload:
        raise RuntimeError(f"ArcGIS error fetching HDOT arterial roads: {payload['error']}")
    features = payload.get("features", [])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return len(features)


KAUAI_BUS_GTFS_URL = (
    "https://files.mobilitydatabase.org/ntd-90237/ntd-90237-202610120135/"
    "ntd-90237-202610120135.zip"
)

# Honolulu's combined GTFS feed (MobilityDatabase mdb-2350, discovered
# 2026-10-15 via thebus.org's producer URL / Pacific-Honolulu timezone
# match) carries BOTH TheBus (agency_id "TheBus") and HART's Skyline rail
# (agency_id "Skyline", route_id 181, route_type 1 = subway/metro) in one
# feed -- real, live schedule data for both modes, not two separately-
# sourced datasets. Skyline's 13 scheduled stops match the 13 "open"
# stations already confirmed via the ArcGIS station-footprint layer
# (data/reference/hart_station_centroids.csv), to within ~0.001 degrees --
# i.e. this is the stations that actually have trains running today, not
# the full 21-station planned alignment.
HONOLULU_TRANSIT_GTFS_URL = (
    "https://files.mobilitydatabase.org/mdb-2350/mdb-2350-202610100134/"
    "mdb-2350-202610100134.zip"
)


def fetch_kauai_bus_major_stops(out_path: Path) -> int:
    """
    Downloads Kauai Bus's real GTFS feed (MobilityDatabase, NTD ID 90237;
    daily-refreshed, zero validation errors as of 2026-10-14) and derives a
    "major transit stop" list for the Transit-Oriented Density scenario --
    directly answering the Challenge's "define major transit stops and
    justify your transit proximity threshold" requirement with real
    connectivity data instead of an assumption.

    Definition used: a stop is "major" if 2+ distinct weekday routes serve
    it. Rationale: Kauai Bus has no rail or BRT -- its route network is a
    hub-and-spoke of shuttle loops feeding a handful of mainline/transfer
    corridors (e.g. Kukui Grove - First Hawaiian Bank: 9 routes, 109 weekday
    trips). A stop multiple routes converge on carries materially more
    transit access (more destinations reachable, shorter effective wait via
    overlapping headways) than a stop served by a single shuttle's own loop
    -- the same distinction "major transit stop" standards elsewhere draw
    between trunk/transfer stops and local coverage stops, but computed here
    from this county's own real schedule data rather than borrowed from a
    rail-specific threshold. As of this feed, 58 of 126 weekday-served stops
    qualify; 68 are single-route stops.

    This stop list, not a rail-station list, is what the TOD scenario
    buffers around for Kauai -- see run_tod_scenario() in build_model.py and
    the "Transit-Oriented Density" section of docs/MODEL_AND_ASSUMPTIONS.md
    for why bus corridors, not just rail, count as "major transit stops"
    here.
    """
    import io
    import zipfile

    req = urllib.request.Request(KAUAI_BUS_GTFS_URL, headers={"User-Agent": "hawaii-housing-model/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        content = resp.read()
    zf = zipfile.ZipFile(io.BytesIO(content))

    def read_csv_rows(name: str) -> list[dict]:
        with zf.open(name) as fh:
            return list(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")))

    stops = {r["stop_id"]: r for r in read_csv_rows("stops.txt")}
    calendar = read_csv_rows("calendar.txt")
    weekday_service_ids = {
        r["service_id"] for r in calendar
        if r["monday"] == "1" and r["friday"] == "1"
    }
    trips = read_csv_rows("trips.txt")
    weekday_trip_ids = {r["trip_id"] for r in trips if r["service_id"] in weekday_service_ids}
    stop_times = read_csv_rows("stop_times.txt")
    trip_route = {r["trip_id"]: r["route_id"] for r in trips}

    routes_by_stop: dict[str, set] = {}
    trips_by_stop: dict[str, int] = {}
    for st in stop_times:
        if st["trip_id"] not in weekday_trip_ids:
            continue
        sid = st["stop_id"]
        routes_by_stop.setdefault(sid, set()).add(trip_route.get(st["trip_id"]))
        trips_by_stop[sid] = trips_by_stop.get(sid, 0) + 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["stop_id", "stop_name", "stop_lat", "stop_lon", "weekday_trips", "n_routes", "is_major_stop"]
        )
        for stop_id, n_routes in sorted(routes_by_stop.items(), key=lambda kv: -trips_by_stop.get(kv[0], 0)):
            stop = stops.get(stop_id)
            if not stop or not stop.get("stop_lat") or not stop.get("stop_lon"):
                continue
            n = len(n_routes)
            writer.writerow(
                [
                    stop_id, stop["stop_name"], stop["stop_lat"], stop["stop_lon"],
                    trips_by_stop.get(stop_id, 0), n, n >= 2,
                ]
            )
            written += 1
    return written


def fetch_honolulu_major_transit_stops(out_path: Path) -> int:
    """
    Downloads Honolulu's combined GTFS feed (MobilityDatabase mdb-2350,
    thebus.org) and derives "major transit stop" lists for BOTH modes it
    contains -- TheBus (agency_id "TheBus") and HART's Skyline rail
    (agency_id "Skyline") -- using the SAME methodology as Kauai's
    fetch_kauai_bus_major_stops() (2+ distinct weekday routes = major),
    so eligibility is computed identically regardless of mode or county.
    Per the user's 2026-10-15 correction: TOD eligibility must be
    implemented consistently across modes, with a toggle choosing which
    stop dataset to buffer around, not two different ad hoc mechanisms.

    Each output row carries a "mode" column ("bus" or "rail") so
    build_model.tod_eligible_parcels() can filter by the mode a user
    selects. Rail gets its own n_routes/is_major_stop columns too, even
    though Skyline is a single line (route_id 181) -- every Skyline stop
    trivially has n_routes=1, so "is_major_stop" for rail is NOT the
    eligibility test (every served Skyline station matters equally); the
    real rail-specific distinction (LPA-eligible for the 6.0/7.0 FAR
    tiers) is applied in build_model.py, not here.
    """
    import io
    import zipfile

    req = urllib.request.Request(HONOLULU_TRANSIT_GTFS_URL, headers={"User-Agent": "hawaii-housing-model/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        content = resp.read()
    zf = zipfile.ZipFile(io.BytesIO(content))

    def read_csv_rows(name: str) -> list[dict]:
        with zf.open(name) as fh:
            return list(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")))

    stops = {r["stop_id"]: r for r in read_csv_rows("stops.txt")}
    routes = {r["route_id"]: r for r in read_csv_rows("routes.txt")}
    calendar = read_csv_rows("calendar.txt")
    weekday_service_ids = {
        r["service_id"] for r in calendar
        if r["monday"] == "1" and r["friday"] == "1"
    }
    trips = read_csv_rows("trips.txt")
    weekday_trip_ids = {r["trip_id"] for r in trips if r["service_id"] in weekday_service_ids}
    stop_times = read_csv_rows("stop_times.txt")
    trip_route = {r["trip_id"]: r["route_id"] for r in trips}

    routes_by_stop: dict[str, set] = {}
    trips_by_stop: dict[str, int] = {}
    mode_by_stop: dict[str, str] = {}
    for st in stop_times:
        if st["trip_id"] not in weekday_trip_ids:
            continue
        sid = st["stop_id"]
        route_id = trip_route.get(st["trip_id"])
        routes_by_stop.setdefault(sid, set()).add(route_id)
        trips_by_stop[sid] = trips_by_stop.get(sid, 0) + 1
        agency_id = routes.get(route_id, {}).get("agency_id")
        mode_by_stop[sid] = "rail" if agency_id == "Skyline" else "bus"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["stop_id", "stop_name", "stop_lat", "stop_lon", "mode", "weekday_trips", "n_routes", "is_major_stop"]
        )
        for stop_id, n_routes in sorted(routes_by_stop.items(), key=lambda kv: -trips_by_stop.get(kv[0], 0)):
            stop = stops.get(stop_id)
            if not stop or not stop.get("stop_lat") or not stop.get("stop_lon"):
                continue
            n = len(n_routes)
            mode = mode_by_stop.get(stop_id, "bus")
            writer.writerow(
                [
                    stop_id, stop["stop_name"], stop["stop_lat"], stop["stop_lon"],
                    mode, trips_by_stop.get(stop_id, 0), n, n >= 2,
                ]
            )
            written += 1
    return written


TIGERWEB_PLACES_URL = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Places_CouSub_ConCity_SubMCD/MapServer"
TIGERWEB_CDP_LAYER = 5  # Census Designated Places -- Hawaii has no incorporated cities, only CDPs


# Zillow Research public data -- no key/login, see data/DATA_SOURCES.md's
# ZORI section for the ZIP-level-coverage-is-zero-for-Kauai finding that
# led to using the county-level series here. These URLs are Zillow's own
# published, versioned filenames (confirmed live 2026-10-15); Zillow
# changes the filename when it changes the underlying smoothing/seasonal-
# adjustment methodology, so a 404 here means Zillow renamed the file, not
# that the county stopped reporting.
ZILLOW_ZORI_COUNTY_URL = "https://files.zillowstatic.com/research/public_csvs/zori/County_zori_uc_sfrcondomfr_sm_month.csv"
ZILLOW_ZHVI_COUNTY_URL = "https://files.zillowstatic.com/research/public_csvs/zhvi/County_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv"


def fetch_zillow_county_timeseries(county_name: str, out_path: Path) -> int:
    """
    Full monthly history (not just the latest value) of Zillow's county-
    level ZORI (rent index) and ZHVI (home value index) for one Hawaii
    county, written as one long (date, series, value) CSV -- real data
    over time, for the Existing Conditions "over time" section (per the
    user's 2026-10-15 request: "we should be looking also at the data
    over time... it better reflects the urgency of the call to action").

    `county_name` matches Zillow's own RegionName column exactly, e.g.
    "Kauai County". ZIP-level Zillow data has zero coverage for Kauai (see
    data/DATA_SOURCES.md); county-level is the finest grain available,
    same decision already made for the single-latest-value figure this
    supplements, not a new one.
    """
    import csv as csv_module
    import io

    rows_out = []
    for series_name, url in [("zori", ZILLOW_ZORI_COUNTY_URL), ("zhvi", ZILLOW_ZHVI_COUNTY_URL)]:
        with urllib.request.urlopen(url, timeout=60) as resp:
            text = resp.read().decode("utf-8")
        reader = csv_module.DictReader(io.StringIO(text))
        date_cols = [c for c in reader.fieldnames if c[:2] == "20"]
        for record in reader:
            if record.get("RegionName") != county_name:
                continue
            for date_col in date_cols:
                val = record.get(date_col)
                if val:
                    rows_out.append({"series": series_name, "date": date_col, "value": float(val)})
            break  # one matching county row per file

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as fh:
        writer = csv_module.DictWriter(fh, fieldnames=["series", "date", "value"])
        writer.writeheader()
        writer.writerows(rows_out)
    return len(rows_out)


def fetch_hawaii_census_places(out_path: Path) -> int:
    """
    Census-Designated-Place (CDP) boundaries for Hawaii -- what most people
    actually mean by a place name (Lihu'e, Kapa'a, Koloa, Hanalei, ...).
    Hawaii has NO incorporated cities (the entire state is unincorporated
    outside county government), so CDPs (TIGERweb layer 5, not layer 4's
    "Incorporated Places", which is empty for Hawaii) are the only real
    place-name geography that exists here. Used by build_dashboard_data.py
    to aggregate tract-level stats up to place names for the dashboard's
    tract/municipality toggle -- real CDP boundaries, not hand-typed
    tract-to-place guesses.
    """
    params = {
        "where": "STATE='15'",
        "outFields": "GEOID,NAME,BASENAME",
        "f": "geojson",
        "returnGeometry": "true",
    }
    url = f"{TIGERWEB_PLACES_URL}/{TIGERWEB_CDP_LAYER}/query?{urllib.parse.urlencode(params)}"
    payload = _get_json_with_retries(url)
    if "error" in payload:
        raise RuntimeError(f"TIGERweb error: {payload['error']}")
    features = payload.get("features", [])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return len(features)


def fetch_hawaii_census_tracts(out_path: Path) -> int:
    params = {
        "where": "STATE='15'",
        "outFields": "GEOID,STATE,COUNTY,TRACT,NAME,AREALAND,AREAWATER",
        "f": "geojson",
        "returnGeometry": "true",
    }
    url = f"{TIGERWEB_TRACTS_URL}/{TIGERWEB_TRACTS_LAYER}/query?{urllib.parse.urlencode(params)}"
    payload = _get_json_with_retries(url)
    if "error" in payload:
        raise RuntimeError(f"TIGERweb error: {payload['error']}")
    features = payload.get("features", [])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return len(features)


def _get_json_with_retries(url: str, attempts: int = 4, base_delay: float = 2.0) -> dict:
    """GET with retry/backoff -- this service is reachable only through an
    outbound network proxy in some environments, which occasionally drops a
    connection mid-transfer (observed: ConnectionResetError / URLError on an
    otherwise-correct request). A single dropped page shouldn't abort an
    entire multi-thousand-feature pull."""
    last_err = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, ConnectionResetError, TimeoutError) as e:
            last_err = e
            if attempt < attempts - 1:
                delay = base_delay * (2**attempt)
                print(f"  request failed ({e}); retrying in {delay:.0f}s...")
                time.sleep(delay)
    raise RuntimeError(f"Giving up after {attempts} attempts on {url}") from last_err


def fetch_layer(
    base_url: str, layer_id: int, page_size: int, out_path: Path, sleep: float = 0.2
) -> int:
    """
    Page through an ArcGIS REST layer OR table with resultOffset/
    resultRecordCount. Spatial layers are written as a GeoJSON
    FeatureCollection; non-spatial tables (LEGDAT, ASMTPITT -- confirmed to
    have no geometry, f=geojson is invalid on them) are written as a plain
    JSON array of attribute records. Returns the total record count written.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Tables (no geometry) must be queried with f=json, not f=geojson.
    probe_url = f"{base_url}/{layer_id}?f=pjson"
    probe = _get_json_with_retries(probe_url)
    is_table = probe.get("type") == "Table" or probe.get("geometryType") is None

    records = []
    offset = 0
    while True:
        params = {
            "where": "1=1",
            "outFields": "*",
            "f": "json" if is_table else "geojson",
            "resultOffset": offset,
            "resultRecordCount": page_size,
            "returnGeometry": "false" if is_table else "true",
        }
        url = f"{base_url}/{layer_id}/query?{urllib.parse.urlencode(params)}"
        payload = _get_json_with_retries(url)
        if "error" in payload:
            raise RuntimeError(f"ArcGIS error on layer {layer_id}: {payload['error']}")
        if is_table:
            page = [f["attributes"] for f in payload.get("features", [])]
        else:
            page = payload.get("features", [])
        records.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
        time.sleep(sleep)  # be polite to free state/county services

    if is_table:
        out_path.write_text(json.dumps(records))
    else:
        out_path.write_text(json.dumps({"type": "FeatureCollection", "features": records}))
    return len(records)


def fetch_building_footprints(out_path: Path) -> int:
    """
    Pull Hawaii building footprints from the combined Google/Microsoft Open
    Buildings GeoParquet dataset, filtered to a statewide bounding box, and
    write GeoJSON. Used as the buildable-area input (parcel area minus
    existing footprint) for density-increase scenarios -- see
    docs/MODEL_AND_ASSUMPTIONS.md. Requires the `duckdb` package (spatial +
    httpfs extensions, auto-installed on first run).
    """
    import duckdb

    out_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
    b = HAWAII_BBOX
    query = f"""
        SELECT
            bf_source, confidence, area_in_meters, s2_id, geohash,
            ST_AsGeoJSON(geometry) AS geometry_json
        FROM read_parquet('{BUILDING_FOOTPRINTS_URL}')
        WHERE bbox.xmin BETWEEN {b['xmin']} AND {b['xmax']}
          AND bbox.ymin BETWEEN {b['ymin']} AND {b['ymax']}
    """
    rows = con.execute(query).fetchall()
    cols = [d[0] for d in con.description]
    features = []
    for row in rows:
        rec = dict(zip(cols, row))
        geom = json.loads(rec.pop("geometry_json"))
        features.append({"type": "Feature", "geometry": geom, "properties": rec})
    out_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return len(features)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--layer",
        choices=list(LAYERS)
        + [
            "building_footprints", "census_tracts", "census_places", "acs_commute_density",
            "acs_income_distribution", "zillow_county_timeseries",
            "kauai_bus_major_stops", "kauai_arterial_roads", "honolulu_major_transit_stops", "all",
        ],
        default="all",
        help="Which named layer to fetch (default: all)",
    )
    parser.add_argument(
        "--county",
        choices=list(COUNTY_FIPS),
        default="kauai",
        help="County for --layer acs_commute_density (default: kauai)",
    )
    args = parser.parse_args()

    if args.layer == "acs_commute_density":
        out_path = RAW_DIR / f"acs_commute_density_{args.county}.csv"
        print(f"Fetching acs_commute_density for {args.county} (Census API)...")
        count = fetch_acs_commute_density(args.county, out_path)
        print(f"  {count} tracts -> {out_path}")
        return

    if args.layer == "acs_income_distribution":
        out_path = RAW_DIR / f"acs_income_distribution_{args.county}.csv"
        print(f"Fetching acs_income_distribution for {args.county} (Census API)...")
        count = fetch_acs_income_distribution(args.county, out_path)
        print(f"  {count} tracts -> {out_path}")
        return

    if args.layer == "kauai_bus_major_stops":
        out_path = RAW_DIR / "kauai_bus_major_stops.csv"
        print("Fetching kauai_bus_major_stops (Kauai Bus GTFS, MobilityDatabase)...")
        count = fetch_kauai_bus_major_stops(out_path)
        print(f"  {count} stops -> {out_path}")
        return

    if args.layer == "kauai_arterial_roads":
        out_path = RAW_DIR / "kauai_arterial_roads.json"
        print("Fetching kauai_arterial_roads (HDOT HPMS Roads, Principal/Minor Arterial)...")
        count = fetch_kauai_arterial_roads(out_path)
        print(f"  {count} segments -> {out_path}")
        return

    if args.layer == "honolulu_major_transit_stops":
        out_path = RAW_DIR / "honolulu_major_transit_stops.csv"
        print("Fetching honolulu_major_transit_stops (TheBus + Skyline GTFS, MobilityDatabase)...")
        count = fetch_honolulu_major_transit_stops(out_path)
        print(f"  {count} stops -> {out_path}")
        return

    if args.layer in ("building_footprints", "all"):
        out_path = RAW_DIR / "building_footprints.json"
        print("Fetching building_footprints (Google/Microsoft Open Buildings, HI bbox)...")
        count = fetch_building_footprints(out_path)
        print(f"  {count} records -> {out_path}")

    if args.layer in ("census_tracts", "all"):
        out_path = RAW_DIR / "census_tracts.json"
        print("Fetching census_tracts (TIGERweb, Hawaii)...")
        count = fetch_hawaii_census_tracts(out_path)
        print(f"  {count} records -> {out_path}")

    if args.layer in ("census_places", "all"):
        out_path = RAW_DIR / "census_places.json"
        print("Fetching census_places (TIGERweb CDPs, Hawaii)...")
        count = fetch_hawaii_census_places(out_path)
        print(f"  {count} records -> {out_path}")

    if args.layer in ("zillow_county_timeseries", "all"):
        out_path = RAW_DIR / "zillow_kauai_county_timeseries.csv"
        print("Fetching zillow_county_timeseries for Kauai County...")
        count = fetch_zillow_county_timeseries("Kauai County", out_path)
        print(f"  {count} rows -> {out_path}")

    if args.layer not in ("building_footprints", "census_tracts", "census_places", "zillow_county_timeseries"):
        targets = LAYERS if args.layer == "all" else {args.layer: LAYERS[args.layer]}
        for name, (base_url, layer_id, page_size) in targets.items():
            out_path = RAW_DIR / f"{name}.json"
            print(f"Fetching {name} (layer {layer_id})...")
            count = fetch_layer(base_url, layer_id, page_size, out_path)
            print(f"  {count} records -> {out_path}")


if __name__ == "__main__":
    main()
