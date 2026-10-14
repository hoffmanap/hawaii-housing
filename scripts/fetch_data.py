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
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

STATE_BASE_URL = "https://geodata.hawaii.gov/arcgis/rest/services/ParcelsZoning/MapServer"
HONOLULU_BFS_BASE_URL = (
    "https://services.arcgis.com/tNJpAOha4mODLkXz/arcgis/rest/services/CadastralTables/FeatureServer"
)
RAW_DIR = Path(__file__).parent.parent / "data" / "raw"

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--layer",
        choices=list(LAYERS) + ["all"],
        default="all",
        help="Which named layer to fetch (default: all)",
    )
    args = parser.parse_args()

    targets = LAYERS if args.layer == "all" else {args.layer: LAYERS[args.layer]}
    for name, (base_url, layer_id, page_size) in targets.items():
        out_path = RAW_DIR / f"{name}.json"
        print(f"Fetching {name} (layer {layer_id})...")
        count = fetch_layer(base_url, layer_id, page_size, out_path)
        print(f"  {count} records -> {out_path}")


if __name__ == "__main__":
    main()
