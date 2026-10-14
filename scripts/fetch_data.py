"""
Pull parcel, zoning, and State Land Use District data from Hawaii's statewide
GIS services (geodata.hawaii.gov) for all four counties, and save to
data/raw/ (gitignored — see data/README.md). This is the Challenge's required
"automated data acquisition process": no parcel data is committed to the repo.

Source: ParcelsZoning MapServer — see data/DATA_SOURCES.md for the layer map
and data/DATA_PROVENANCE.md for license/version/access-date records.

STATUS: stub. Layer IDs and query patterns are documented in
data/DATA_SOURCES.md from manual inspection of the service; this script still
needs the actual paginated ArcGIS REST GeoJSON pulls (query endpoint,
resultOffset paging, per-county loop) implemented and tested against each of
the four counties before it's wired into scripts/build_model.py.
"""

BASE_URL = "https://geodata.hawaii.gov/arcgis/rest/services/ParcelsZoning/MapServer"

LAYERS = {
    "parcels_statewide_tmk": 25,
    "parcels_hawaii_county": 5,
    "parcels_oahu": 11,
    "parcels_kauai": 9,
    "parcels_maui": 30,
    "zoning_hawaii_county": 2,
    "zoning_honolulu": 3,
    "zoning_kauai": 29,
    "zoning_maui": 33,
    "state_land_use_districts": 20,
}


def fetch_layer(layer_id: int, out_path: str) -> None:
    raise NotImplementedError(
        "Implement paginated ArcGIS REST query (f=geojson, resultOffset loop) "
        f"against {BASE_URL}/{layer_id}/query and write to {out_path}"
    )


if __name__ == "__main__":
    raise SystemExit(
        "fetch_data.py is a stub — see module docstring. "
        "Run `python scripts/fetch_data.py --layer <name>` once implemented."
    )
