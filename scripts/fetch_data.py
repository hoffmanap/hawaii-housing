"""
Pull parcel, zoning, and State Land Use District data from Hawaii's statewide
GIS service (geodata.hawaii.gov/arcgis/rest/services/ParcelsZoning) for all
four counties, and save to data/raw/ (gitignored — see data/README.md). This
is the Challenge's required "automated data acquisition process": no parcel
data is committed to the repo.

KNOWN GAP (read before building eligibility logic on top of this):
The layers below are CADASTRAL/BOUNDARY data only -- TMK id, acreage,
geometry, zoning district code. None of them carry building square footage,
existing unit counts, or an assessed land-use class, which is exactly the
data our eligibility screens (underutilization, existing-unit count,
vacant-lot detection) depend on -- see docs/MODEL_AND_ASSUMPTIONS.md and the
"Known Data Quality Issues" section carried over from the El Paso build's
README, which hit the same failure mode once already (every parcel looking
artificially vacant because the building-characteristics field wasn't
actually populated).

Candidates to join in once confirmed (NOT yet verified -- field schemas
unconfirmed as of 2026-10-14):
  - opendata.hawaii.gov "LEGDAT" table (legal data incl. unit number per TMK)
  - opendata.hawaii.gov "ASMTPITT" table (assessment tax-class / land-use
    class codes per TMK)
  - opendata.hawaii.gov "ASMTGIS" table (assessed value / exemption status)
  - Equivalent county assessor sources for Hawaii County, Kauai, and Maui
    have not yet been identified at all.
Do not assume these exist with usable fields -- confirm each one's actual
schema (and license) before wiring it into build_model.py, and update
docs/DATA_PROVENANCE.md accordingly.
"""
import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = "https://geodata.hawaii.gov/arcgis/rest/services/ParcelsZoning/MapServer"
RAW_DIR = Path(__file__).parent.parent / "data" / "raw"

# layer_id, max records per page (service-enforced cap, confirmed via
# layer metadata query on 2026-10-14)
LAYERS = {
    "parcels_statewide_tmk": (25, 1000),
    "parcels_hawaii_county": (5, 1000),
    "parcels_oahu": (11, 1000),
    "parcels_kauai": (9, 1000),
    "parcels_maui": (30, 1000),
    "zoning_hawaii_county": (2, 1000),
    "zoning_honolulu": (3, 1000),
    "zoning_kauai": (29, 1000),
    "zoning_maui": (33, 1000),
    "state_land_use_districts": (20, 1000),
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


def fetch_layer(layer_id: int, page_size: int, out_path: Path, sleep: float = 0.2) -> int:
    """
    Page through an ArcGIS REST layer with resultOffset/resultRecordCount
    and write all features as a single GeoJSON FeatureCollection.
    Returns the total feature count written.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    features = []
    offset = 0
    while True:
        params = {
            "where": "1=1",
            "outFields": "*",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": page_size,
            "returnGeometry": "true",
        }
        url = f"{BASE_URL}/{layer_id}/query?{urllib.parse.urlencode(params)}"
        payload = _get_json_with_retries(url)
        if "error" in payload:
            raise RuntimeError(f"ArcGIS error on layer {layer_id}: {payload['error']}")
        page_features = payload.get("features", [])
        features.extend(page_features)
        if len(page_features) < page_size:
            break
        offset += page_size
        time.sleep(sleep)  # be polite to a free state-run service

    out_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features})
    )
    return len(features)


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
    for name, (layer_id, page_size) in targets.items():
        out_path = RAW_DIR / f"{name}.geojson"
        print(f"Fetching {name} (layer {layer_id})...")
        count = fetch_layer(layer_id, page_size, out_path)
        print(f"  {count} features -> {out_path}")


if __name__ == "__main__":
    main()
