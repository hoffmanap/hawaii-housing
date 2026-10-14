"""
Build the parcel-level-then-bucketed-rollup model described in
docs/MODEL_AND_ASSUMPTIONS.md.

Pipeline, per county:
  1. Load parcels, county zoning, State Land Use Districts, census tracts,
     and the statewide building-footprints extract from data/raw/
     (fetch_data.py populates this; run that first).
  2. Spatial-join each parcel (by centroid) to its zoning district, State
     Land Use District, and census tract.
  3. Spatial-join building footprints (by centroid) to the parcel they sit
     on, sum footprint area per parcel, and compute coverage_ratio and
     remaining_area.
  4. Bucket each parcel on lot-size band x coverage band x zoning x State
     Land Use District, and write the parcel-level table plus the
     tract-level rollup.
  5. Apply a first scenario (ADUs) against the bucketed table and report
     tract-level eligible-parcel counts and estimated unit capacity.

STATUS: wired up and verified end-to-end for Kauai only (cleanest tax-class
field, smallest parcel count -- see data/DATA_SOURCES.md). Honolulu,
Hawaii County, and Maui use the same functions below; the one thing that
must be supplied per county is `existing_residential_taxclasses` (or, for
Honolulu, a LEGDAT-row-count-based flag instead of a taxclass match --
not yet wired in here). See `COUNTY_CONFIG` below.

Everything here operates on parcel geometry only to assign buckets; no
output is reported below the tract level, per the Challenge's aggregation
requirement -- see docs/MODEL_AND_ASSUMPTIONS.md.
"""
import argparse
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
OUT_DIR = Path(__file__).parent.parent / "data" / "processed"

# Hawaii sits entirely in UTM Zone 4N; using one projected CRS statewide
# keeps every county's area math consistent and avoids the ~7% area
# distortion Web Mercator would introduce at this latitude.
PROJECTED_CRS = "EPSG:32604"
SQFT_PER_SQM = 10.7639104

LOT_SIZE_BANDS = [0, 5_000, 10_000, 20_000, float("inf")]
LOT_SIZE_LABELS = ["<5k sqft", "5-10k sqft", "10-20k sqft", "20k+ sqft"]
COVERAGE_BANDS = [0, 0.10, 0.25, 0.50, 0.75, float("inf")]
COVERAGE_LABELS = ["0-10%", "10-25%", "25-50%", "50-75%", "75%+"]

# Per-county config: which parcel layer/field combination tells us a
# parcel already has an existing residential structure (used to gate the
# ADU scenario -- an ADU is a *second* unit on an already-residential
# lot). Only Kauai is wired up; see module docstring.
COUNTY_CONFIG = {
    "kauai": {
        "parcels_file": "parcels_kauai.json",
        "zoning_file": "zoning_kauai.json",
        "parcel_id_field": "tmk_txt",
        "zoning_field": "zoning",
        "taxclass_field": "taxclass",
        "lot_area_field": "gisacres",  # acres; converted to sqft below
        "existing_residential_taxclasses": {
            "OWNER-OCCUPIED",
            "NON-OWNER-OCCUPIED RESIDENTIAL",
            "OWNER-OCCUPIED MIXED-USE",
            "LONG TERM AFFORDABLE RENTAL",
        },
    },
}

# PLACEHOLDER, unconfirmed -- Kauai County's actual ADU/detached-dwelling
# minimum lot size needs to come from ordinance text (see the open
# question logged in docs/MODEL_AND_ASSUMPTIONS.md under ADUs). 7,500 sqft
# is a common minimum-lot threshold across Hawaii county codes but has NOT
# been verified against Kauai's specific ordinance.
ADU_MIN_LOT_SQFT_PLACEHOLDER = 7_500

# PLACEHOLDER -- same status as the "TBD" absorption rate in
# docs/MODEL_AND_ASSUMPTIONS.md's Core Assumptions table. Not sourced from
# Hawaii permit-pace data yet; purely here so the pipeline produces a
# number to sanity-check, not a defensible estimate.
ABSORPTION_RATE_PLACEHOLDER = 0.08


def _load_geojson(path: Path) -> gpd.GeoDataFrame:
    data = json.loads(path.read_text())
    records = []
    for f in data["features"]:
        props = dict(f["properties"])
        props["geometry"] = shape(f["geometry"])
        records.append(props)
    return gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")


def _centroid_join(points_gdf: gpd.GeoDataFrame, polygons_gdf: gpd.GeoDataFrame, cols: list[str]) -> pd.DataFrame:
    """Assign each point (parcel/footprint centroid) the attributes of the
    polygon it falls within. Centroid-in-polygon, not full overlay -- the
    standard approximation for parcel-to-zone assignment, and the only
    tractable option at this feature count."""
    joined = gpd.sjoin(points_gdf, polygons_gdf[cols + ["geometry"]], how="left", predicate="within")
    # A centroid can land exactly on a shared edge between two adjacent
    # polygons (vertex/boundary coincidence), matching both -- keep the
    # first match per point so every parcel contributes exactly one row.
    id_col = points_gdf.columns[0]
    return joined.drop(columns="geometry").drop_duplicates(subset=id_col, keep="first")


def build_county(county: str) -> dict[str, Path]:
    cfg = COUNTY_CONFIG[county]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    parcels = _load_geojson(RAW_DIR / cfg["parcels_file"])
    zoning = _load_geojson(RAW_DIR / cfg["zoning_file"])
    slud = _load_geojson(RAW_DIR / "state_land_use_districts.json")
    tracts = _load_geojson(RAW_DIR / "census_tracts.json")
    footprints = _load_geojson(RAW_DIR / "building_footprints.json")

    parcels = parcels.to_crs(PROJECTED_CRS)
    zoning = zoning.to_crs(PROJECTED_CRS)
    slud = slud.to_crs(PROJECTED_CRS)
    tracts = tracts.to_crs(PROJECTED_CRS)
    footprints = footprints.to_crs(PROJECTED_CRS)

    parcels["parcel_area_sqft"] = parcels.geometry.area * SQFT_PER_SQM
    pid = cfg["parcel_id_field"]

    centroids = parcels.copy()
    centroids["geometry"] = centroids.geometry.centroid

    zoning_hit = _centroid_join(centroids[[pid, "geometry"]], zoning, [cfg["zoning_field"]])
    slud_hit = _centroid_join(centroids[[pid, "geometry"]], slud, ["ludcode"])
    tract_hit = _centroid_join(centroids[[pid, "geometry"]], tracts, ["GEOID"])

    parcels = (
        parcels.merge(zoning_hit[[pid, cfg["zoning_field"]]], on=pid, how="left", suffixes=("", "_zone"))
        .merge(slud_hit[[pid, "ludcode"]], on=pid, how="left")
        .merge(tract_hit[[pid, "GEOID"]], on=pid, how="left")
    )

    # Footprints: join by centroid-within-parcel, sum area per parcel.
    fp_centroids = footprints.copy()
    fp_centroids["geometry"] = fp_centroids.geometry.centroid
    fp_hit = gpd.sjoin(
        fp_centroids[["area_in_meters", "geometry"]],
        parcels[[pid, "geometry"]],
        how="inner",
        predicate="within",
    )
    # Same boundary-coincidence issue as _centroid_join: a footprint
    # centroid on a shared parcel edge must count once, not toward both.
    fp_hit = fp_hit[~fp_hit.index.duplicated(keep="first")]
    footprint_area_by_parcel = fp_hit.groupby(pid)["area_in_meters"].sum().rename("footprint_area_sqm")
    parcels = parcels.merge(footprint_area_by_parcel, on=pid, how="left")
    parcels["footprint_area_sqm"] = parcels["footprint_area_sqm"].fillna(0.0)
    parcels["footprint_area_sqft"] = parcels["footprint_area_sqm"] * SQFT_PER_SQM

    parcels["coverage_ratio"] = (
        parcels["footprint_area_sqft"] / parcels["parcel_area_sqft"]
    ).clip(upper=1.0)
    parcels["remaining_area_sqft"] = (
        parcels["parcel_area_sqft"] - parcels["footprint_area_sqft"]
    ).clip(lower=0.0)

    parcels["lot_size_band"] = pd.cut(
        parcels["parcel_area_sqft"], bins=LOT_SIZE_BANDS, labels=LOT_SIZE_LABELS, right=False
    )
    parcels["coverage_band"] = pd.cut(
        parcels["coverage_ratio"], bins=COVERAGE_BANDS, labels=COVERAGE_LABELS, right=False
    )

    parcels["has_existing_residential"] = parcels[cfg["taxclass_field"]].isin(
        cfg["existing_residential_taxclasses"]
    )

    parcel_cols = [
        pid, "GEOID", cfg["zoning_field"], "ludcode", cfg["taxclass_field"],
        "parcel_area_sqft", "footprint_area_sqft", "coverage_ratio", "remaining_area_sqft",
        "lot_size_band", "coverage_band", "has_existing_residential",
    ]
    parcel_table = parcels[parcel_cols].rename(columns={cfg["zoning_field"]: "zoning", cfg["taxclass_field"]: "taxclass"})

    bucket_rollup = (
        parcel_table.groupby(["GEOID", "zoning", "ludcode", "lot_size_band", "coverage_band"], observed=True)
        .agg(parcel_count=(pid, "count"), remaining_area_sqft_total=("remaining_area_sqft", "sum"))
        .reset_index()
    )

    adu_eligible = parcel_table[
        (parcel_table["ludcode"] == "U")
        & parcel_table["has_existing_residential"]
        & (parcel_table["parcel_area_sqft"] >= ADU_MIN_LOT_SQFT_PLACEHOLDER)
    ]
    adu_tract = (
        adu_eligible.groupby("GEOID", observed=True)
        .size()
        .rename("eligible_parcel_count")
        .reset_index()
    )
    adu_tract["estimated_new_units"] = (
        adu_tract["eligible_parcel_count"] * ABSORPTION_RATE_PLACEHOLDER
    ).round(1)

    parcel_out = OUT_DIR / f"{county}_parcel_level.csv"
    bucket_out = OUT_DIR / f"{county}_bucket_rollup_tract.csv"
    adu_out = OUT_DIR / f"{county}_adu_scenario_tract.csv"
    parcel_table.to_csv(parcel_out, index=False)
    bucket_rollup.to_csv(bucket_out, index=False)
    adu_tract.to_csv(adu_out, index=False)

    return {"parcel_level": parcel_out, "bucket_rollup": bucket_out, "adu_scenario": adu_out}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--county", choices=list(COUNTY_CONFIG), default="kauai", help="Which county to build (default: kauai)"
    )
    args = parser.parse_args()

    print(f"Building model for {args.county}...")
    outputs = build_county(args.county)
    for name, path in outputs.items():
        n_rows = sum(1 for _ in path.open()) - 1
        print(f"  {name}: {n_rows} rows -> {path}")


if __name__ == "__main__":
    main()
