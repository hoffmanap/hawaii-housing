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
    get_vars = ",".join(["NAME", *ACS_B08301_VARS, *ACS_B01003_VARS])
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
            ["GEOID", "name", "population", "commuters_excl_wfh", "pct_alt_mode_commute", "density_per_sqmi"]
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
            writer.writerow([geoid, record["NAME"], population, commuters, round(pct_alt_mode, 2), density])
            written += 1
    return written


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
        choices=list(LAYERS) + ["building_footprints", "census_tracts", "acs_commute_density", "all"],
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

    if args.layer not in ("building_footprints", "census_tracts"):
        targets = LAYERS if args.layer == "all" else {args.layer: LAYERS[args.layer]}
        for name, (base_url, layer_id, page_size) in targets.items():
            out_path = RAW_DIR / f"{name}.json"
            print(f"Fetching {name} (layer {layer_id})...")
            count = fetch_layer(base_url, layer_id, page_size, out_path)
            print(f"  {count} records -> {out_path}")


if __name__ == "__main__":
    main()
