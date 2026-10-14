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
import math
import re
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
        # Subset of the above that Kauai's own tax-class field already
        # flags as owner-occupied -- lets the owner-occupancy requirement
        # toggle actually do something for this county without any new
        # data. Other counties' taxclass/pittcode schemes don't make this
        # distinction (see data/DATA_SOURCES.md), so the toggle will need
        # a different data source wired in before it works there.
        "owner_occupied_taxclasses": {"OWNER-OCCUPIED", "OWNER-OCCUPIED MIXED-USE"},
        # Confirmed via Kauai County Code §8-1.4(i)/8-1.5 (eCode360),
        # 2026-10-14: additional dwelling units / guest houses are
        # permitted in R-1 to R-6, R-10 to R-20, CN, CG, AG, and O.
        # IMPORTANT: "R-1 to R-6, R-10 to R-20" is a NUMERIC RANGE, not an
        # enumerated list -- it includes R-15 (between 10 and 20) and
        # excludes R-7/R-8/R-9 (the gap between the two ranges). An
        # earlier version of this list used literal string prefixes
        # ("R-1","R-2","R-4","R-6","R-10","R-20") and wrongly excluded
        # R-15 parcels as a result -- see is_adu_eligible_zone() below,
        # which checks the parsed district number against these ranges
        # instead of matching strings.
        "adu_eligible_rdistrict_ranges": ((1, 6), (10, 20)),
        "adu_eligible_non_r_zoning_prefixes": ("C-N", "C-G", "A", "O"),
    },
}

# Confirmed via Kauai County Code §8-4.2(b), §8-4.6 (eCode360, 2026-10-14):
# "The number portion of each Residential Density District establishes the
# maximum number of dwelling units that may be permitted per acre of
# land." I.e. R-6 allows up to 6 units/acre, R-10 up to 10, etc. -- a real
# district-specific density rule, replacing the earlier flat min_lot_sqft
# placeholder for every R-numbered district. §8-4.6(f): round a fractional
# result up when it's 65%+ of a whole unit (ROUND_UP_THRESHOLD below).
# §8-4.6(b) excludes adjoining streets/common areas from the area used --
# NOT applied here; this pipeline uses gross parcel geometry from the
# county GIS layer, a known simplification worth flagging in
# docs/MODEL_AND_ASSUMPTIONS.md, not silently absorbed into the number.
RDISTRICT_PATTERN = re.compile(r"^R-(\d+)")
ROUND_UP_THRESHOLD = 0.65


def max_density_units_per_acre(zoning: str) -> float | None:
    """Parse the density straight from the district's own number (R-6 ->
    6.0 units/acre). Returns None for non-R-numbered zones (AG, O, CN,
    CG, CON, etc.), which this density rule doesn't apply to -- those
    fall back to the flat min_lot_sqft slider in run_adu_scenario()."""
    if not isinstance(zoning, str):
        return None
    m = RDISTRICT_PATTERN.match(zoning)
    return float(m.group(1)) if m else None


def max_units_for_parcel(parcel_area_sqft: float, density_per_acre: float) -> int:
    raw = (parcel_area_sqft / 43_560) * density_per_acre
    whole, frac = int(raw), raw - int(raw)
    return whole + 1 if frac >= ROUND_UP_THRESHOLD else whole


def is_adu_eligible_zone(zoning: str, cfg: dict) -> bool:
    """Numeric-range check for R-districts (see adu_eligible_rdistrict_ranges
    above), plus a literal-prefix check for the non-R zones that are also
    ADU-eligible but have no unit number to range-check."""
    if not isinstance(zoning, str):
        return False
    m = RDISTRICT_PATTERN.match(zoning)
    if m:
        n = int(m.group(1))
        return any(lo <= n <= hi for lo, hi in cfg.get("adu_eligible_rdistrict_ranges", ()))
    return zoning.startswith(tuple(cfg.get("adu_eligible_non_r_zoning_prefixes", ())))

# Default ADU eligibility rule -- a STARTING POINT meant to be overridden
# via build_county()'s keyword arguments (see ScenarioParams below), not a
# sourced conclusion. 7,500 sqft is a common minimum-lot threshold across
# Hawaii county codes but has NOT been verified against Kauai's specific
# ADU ordinance -- that ordinance-text research is still the open item
# logged in docs/MODEL_AND_ASSUMPTIONS.md under the ADU scenario mapping.
# The point of exposing this as a parameter (eventually a UI slider) is to
# let someone show how eligible-parcel counts change as the minimum moves,
# rather than asserting one number as correct.
DEFAULT_ADU_MIN_LOT_SQFT = 7_500

# County/island-specific empirical benchmarks for the absorption rate --
# the share of eligible parcels assumed to redevelop over a planning
# horizon (see docs/MODEL_AND_ASSUMPTIONS.md, "Market Absorption Rate").
# These are NOT the same quantity El Paso's 8% approximated (there's no
# historical eligible-parcel baseline for Hawaii either, same gap El
# Paso had before its own calibration) -- they're the best available
# empirical proxy: each county's average annual building-permit count
# over 2016-2025 as a share of its 2023 housing stock, scaled to a
# 10-year planning horizon. Source: FRED Building Permits Survey series
# (BPPRIV015001/3/7/9, HIBPPRIV) x Hawaii DBEDT 2023 housing-unit
# estimates (CO-EST2023-HU-15) -- full citations in
# data/reference/absorption_rate_benchmarks.csv, which this loads from
# rather than hardcoding, so updating the benchmark data doesn't require
# touching this file.
ABSORPTION_RATE_BENCHMARKS_FILE = (
    Path(__file__).parent.parent / "data" / "reference" / "absorption_rate_benchmarks.csv"
)
DEFAULT_ABSORPTION_RATE = 0.067  # statewide decade-equivalent benchmark, see CSV above

COUNTY_TO_BENCHMARK_AREA = {
    "honolulu": "honolulu_oahu",
    "kauai": "kauai_county",
    "hawaii": "hawaii_county_bigisland",
    "maui": "maui_county",
}


def load_absorption_rate_benchmarks() -> pd.DataFrame:
    return pd.read_csv(ABSORPTION_RATE_BENCHMARKS_FILE)


def absorption_rate_benchmark_for(county: str) -> float:
    """Decade-equivalent permit-to-stock ratio for one county/island, as a
    fraction (e.g. 0.063 for Kauai) -- a slider default, not a fixed value."""
    df = load_absorption_rate_benchmarks()
    area = COUNTY_TO_BENCHMARK_AREA[county]
    row = df.loc[df["area"] == area].iloc[0]
    return row["decade_equivalent_pct_10yr"] / 100.0


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
    owner_occ_classes = cfg.get("owner_occupied_taxclasses")
    parcels["is_owner_occupied"] = (
        parcels[cfg["taxclass_field"]].isin(owner_occ_classes) if owner_occ_classes else pd.NA
    )

    # Density-based max-unit rule (R-district number = units/acre) --
    # None/NaN for non-R-numbered zones, where it doesn't apply.
    parcels["density_per_acre"] = parcels[cfg["zoning_field"]].apply(max_density_units_per_acre)
    parcels["max_units_by_density"] = parcels.apply(
        lambda r: max_units_for_parcel(r["parcel_area_sqft"], r["density_per_acre"])
        if pd.notna(r["density_per_acre"])
        else pd.NA,
        axis=1,
    )
    # Approximation, same status as Oahu's LEGDAT-based existing_units
    # count elsewhere in this project: no direct existing-unit-count field
    # exists on Kauai's parcel layer, so this assumes one existing
    # dwelling per already-residential parcel. Will overcount available
    # capacity on any parcel that's actually already multi-unit (e.g. an
    # existing duplex on a R-6 lot) -- a real limitation, not yet solved.
    parcels["existing_units_estimate"] = parcels["has_existing_residential"].astype(int)

    parcel_cols = [
        pid, "GEOID", cfg["zoning_field"], "ludcode", cfg["taxclass_field"],
        "parcel_area_sqft", "footprint_area_sqft", "coverage_ratio", "remaining_area_sqft",
        "lot_size_band", "coverage_band", "has_existing_residential", "is_owner_occupied",
        "density_per_acre", "max_units_by_density", "existing_units_estimate",
    ]
    parcel_table = parcels[parcel_cols].rename(columns={cfg["zoning_field"]: "zoning", cfg["taxclass_field"]: "taxclass"})

    bucket_rollup = (
        parcel_table.groupby(["GEOID", "zoning", "ludcode", "lot_size_band", "coverage_band"], observed=True)
        .agg(parcel_count=(pid, "count"), remaining_area_sqft_total=("remaining_area_sqft", "sum"))
        .reset_index()
    )

    parcel_out = OUT_DIR / f"{county}_parcel_level.csv"
    bucket_out = OUT_DIR / f"{county}_bucket_rollup_tract.csv"
    parcel_table.to_csv(parcel_out, index=False)
    bucket_rollup.to_csv(bucket_out, index=False)

    return {"parcel_level": parcel_out, "bucket_rollup": bucket_out}


class ScenarioParams:
    """
    The "sliders" for the ADU scenario. Every field here is meant to be
    adjusted by a user (or swept across a range) rather than treated as a
    fixed assumption -- that's the whole point of exposing them
    separately from the expensive geometry/bucketing step in
    build_county(), which only needs to run once per county no matter how
    many times these are changed. Defaults are starting points, not
    sourced conclusions -- see the per-field notes.
    """

    def __init__(
        self,
        min_lot_sqft: float = DEFAULT_ADU_MIN_LOT_SQFT,
        absorption_rate: float | None = None,  # None -> county's own benchmark
        require_owner_occupancy: bool = False,
        require_parking: bool = False,
    ):
        self.min_lot_sqft = min_lot_sqft
        self.absorption_rate = absorption_rate
        self.require_owner_occupancy = require_owner_occupancy
        self.require_parking = require_parking


def run_adu_scenario(county: str, params: ScenarioParams | None = None) -> tuple[pd.DataFrame, dict]:
    """
    Cheap re-run against the already-bucketed parcel table (no geometry
    work) -- this is the function a slider UI would call on every drag.
    Returns (tract-level results, metadata about what was/wasn't applied).
    """
    params = params or ScenarioParams()
    parcel_path = OUT_DIR / f"{county}_parcel_level.csv"
    if not parcel_path.exists():
        raise FileNotFoundError(f"Run build_county('{county}') first -- {parcel_path} not found")
    parcel_table = pd.read_csv(parcel_path)

    absorption_rate = (
        params.absorption_rate if params.absorption_rate is not None else absorption_rate_benchmark_for(county)
    )

    cfg = COUNTY_CONFIG[county]
    if "adu_eligible_rdistrict_ranges" in cfg:
        zone_ok = parcel_table["zoning"].apply(lambda z: is_adu_eligible_zone(z, cfg))
    else:
        # Fallback for a county without a confirmed eligible-zone list yet
        # (see module docstring): use the Urban State Land Use District as
        # a coarser proxy, same as the original placeholder logic.
        zone_ok = parcel_table["ludcode"] == "U"

    # Two eligibility rules depending on whether the parcel sits in an
    # R-numbered density district:
    #   - R-districts: the confirmed density rule (Kauai County Code
    #     §8-4.2(b)/§8-4.6) -- a second unit is allowed if existing + 1
    #     doesn't exceed what the district's units/acre allows on this
    #     parcel's actual area. min_lot_sqft is NOT applied here; density
    #     is the real, sourced constraint, not lot size directly (a small
    #     lot in a high-density district can still qualify).
    #   - Non-R zones (AG, O, CN, CG -- also ADU-eligible per the
    #     ordinance, but with no units/acre number to apply): falls back
    #     to the min_lot_sqft slider, which remains a placeholder there.
    has_density_rule = parcel_table["density_per_acre"].notna()
    density_ok = has_density_rule & (
        parcel_table["max_units_by_density"] >= parcel_table["existing_units_estimate"] + 1
    )
    flat_lot_ok = ~has_density_rule & (parcel_table["parcel_area_sqft"] >= params.min_lot_sqft)

    eligible = zone_ok & parcel_table["has_existing_residential"] & (density_ok | flat_lot_ok)
    if params.require_owner_occupancy:
        eligible &= parcel_table["is_owner_occupied"].fillna(False)

    adu_eligible = parcel_table[eligible]
    adu_tract = (
        adu_eligible.groupby("GEOID", observed=True)
        .size()
        .rename("eligible_parcel_count")
        .reset_index()
    )
    adu_tract["estimated_new_units"] = (adu_tract["eligible_parcel_count"] * absorption_rate).round(1)

    metadata = {
        "min_lot_sqft": params.min_lot_sqft,
        "absorption_rate": absorption_rate,
        "absorption_rate_was_county_benchmark": params.absorption_rate is None,
        "require_owner_occupancy": params.require_owner_occupancy,
        "require_parking": params.require_parking,
    }
    if params.require_owner_occupancy and county not in {
        c for c, v in COUNTY_CONFIG.items() if v.get("owner_occupied_taxclasses")
    }:
        metadata["owner_occupancy_warning"] = (
            f"{county} has no owner-occupied/non-owner-occupied taxclass distinction -- "
            "this toggle had no effect."
        )
    if params.require_parking:
        metadata["parking_warning"] = (
            "NOT YET IMPLEMENTED -- no county parking-ratio ordinance data has been sourced "
            "(see docs/MODEL_AND_ASSUMPTIONS.md, Parking Requirement Reduction mapping). "
            "This toggle is recorded but currently changes nothing."
        )

    out_path = OUT_DIR / f"{county}_adu_scenario_tract.csv"
    adu_tract.to_csv(out_path, index=False)
    return adu_tract, metadata


MISSING_MIDDLE_MAX_UNITS = 6  # Challenge benchmark definition: 2-6 units, up to 3 stories


def run_missing_middle_scenario(county: str) -> tuple[pd.DataFrame, dict]:
    """
    Missing Middle / Gentle Density, for R-numbered districts only (AG/O/
    CN/CG have no units/acre figure to compute against -- see
    max_density_units_per_acre()). Capacity per parcel is
    min(max_units_by_density, 6) - existing_units_estimate, floored at 0:
    the Challenge's benchmark caps Missing Middle at 6 units regardless of
    what a district's density math alone would allow (e.g. R-20 on a
    larger lot could compute past 6).

    Deliberately NOT summed with the ADU scenario's capacity for the same
    parcel -- both draw on the same underlying district density allowance,
    so adding them would double-count physical capacity (the same
    "never count the same capacity twice" rule the El Paso reference
    project uses). A parcel's headline number should be the max of the two,
    not their sum; that reconciliation isn't wired in yet since only
    Kauai's two scenarios exist so far.
    """
    cfg = COUNTY_CONFIG[county]
    parcel_path = OUT_DIR / f"{county}_parcel_level.csv"
    if not parcel_path.exists():
        raise FileNotFoundError(f"Run build_county('{county}') first -- {parcel_path} not found")
    parcel_table = pd.read_csv(parcel_path)

    has_density_rule = parcel_table["density_per_acre"].notna()
    capacity = (parcel_table["max_units_by_density"].clip(upper=MISSING_MIDDLE_MAX_UNITS) -
                parcel_table["existing_units_estimate"]).clip(lower=0)
    eligible_mask = has_density_rule & (capacity > 0)

    eligible = parcel_table[eligible_mask].copy()
    eligible["new_unit_capacity"] = capacity[eligible_mask]

    tract_rollup = (
        eligible.groupby("GEOID", observed=True)
        .agg(eligible_parcel_count=(cfg["parcel_id_field"], "count"), gross_new_unit_capacity=("new_unit_capacity", "sum"))
        .reset_index()
    )
    absorption_rate = absorption_rate_benchmark_for(county)
    tract_rollup["estimated_new_units"] = (tract_rollup["gross_new_unit_capacity"] * absorption_rate).round(1)

    out_path = OUT_DIR / f"{county}_missing_middle_scenario_tract.csv"
    tract_rollup.to_csv(out_path, index=False)
    metadata = {
        "absorption_rate": absorption_rate,
        "max_units_cap": MISSING_MIDDLE_MAX_UNITS,
        "applies_to": "R-numbered density districts only (confirmed §8-4.2(b)/§8-4.6); "
        "AG/O/CN/CG parcels are excluded from this scenario, not just unscored -- "
        "they have no units/acre figure to compute a density-based capacity from.",
        "known_limitation": "existing_units_estimate assumes 1 unit per already-residential "
        "parcel (no direct unit-count field for Kauai) -- overstates capacity on any parcel "
        "that's already multi-unit.",
    }
    return tract_rollup, metadata


def run_parking_reduction_scenario(county: str, params: ScenarioParams | None = None) -> tuple[pd.DataFrame, dict]:
    """
    Parking Requirement Reduction, built on the same ADU-eligible parcel
    set as run_adu_scenario() -- same physical capacity question (can an
    ADU go here), but comparing two parking regimes: Kauai's real
    confirmed requirement of 1 off-street space per ADU (County Code
    Chapter 8, Article 15/§8-1.4(i), see docs/MODEL_AND_ASSUMPTIONS.md) vs.
    that requirement eliminated.

    This reports the SAME eligible-parcel count either way -- parking
    doesn't gate whether an ADU is legally allowed on Kauai, it gates how
    much of the remaining buildable area that parking consumes. What
    changes between regimes is land available for the unit itself:
    roughly 200 sqft/space (standard surface parking stall + maneuvering
    allowance -- a planning-literature figure, not Kauai-specific, same
    status as the 850 sqft/unit figure flagged in the El Paso build this
    project inherited the pattern from).
    """
    params = params or ScenarioParams()
    adu_tract, metadata = run_adu_scenario(county, params)

    sqft_per_parking_space = 200  # planning-literature placeholder, not Kauai-sourced
    adu_tract = adu_tract.copy()
    adu_tract["parking_land_consumed_sqft_if_required"] = (
        adu_tract["eligible_parcel_count"] * sqft_per_parking_space
    )
    metadata["parking_spaces_required_per_unit_current_code"] = 1
    metadata["sqft_per_parking_space_assumption"] = sqft_per_parking_space
    metadata["note"] = (
        "Parking does not change eligibility on Kauai (ADUs require 1 space either way "
        "under current code) -- this scenario reports land consumed by that requirement, "
        "not a change in which parcels qualify. Reducing the requirement to 0 removes this "
        "land cost but produces the same eligible_parcel_count as the ADU scenario above."
    )

    out_path = OUT_DIR / f"{county}_parking_scenario_tract.csv"
    adu_tract.to_csv(out_path, index=False)
    return adu_tract, metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--county", choices=list(COUNTY_CONFIG), default="kauai", help="Which county to build (default: kauai)"
    )
    parser.add_argument("--min-lot-sqft", type=float, default=DEFAULT_ADU_MIN_LOT_SQFT)
    parser.add_argument("--absorption-rate", type=float, default=None, help="Overrides the county benchmark")
    parser.add_argument("--require-owner-occupancy", action="store_true")
    parser.add_argument("--require-parking", action="store_true")
    args = parser.parse_args()

    print(f"Building model for {args.county}...")
    outputs = build_county(args.county)
    for name, path in outputs.items():
        n_rows = sum(1 for _ in path.open()) - 1
        print(f"  {name}: {n_rows} rows -> {path}")

    params = ScenarioParams(
        min_lot_sqft=args.min_lot_sqft,
        absorption_rate=args.absorption_rate,
        require_owner_occupancy=args.require_owner_occupancy,
        require_parking=args.require_parking,
    )
    adu_tract, metadata = run_adu_scenario(args.county, params)
    print(f"  adu_scenario: {len(adu_tract)} rows -> {OUT_DIR / f'{args.county}_adu_scenario_tract.csv'}")
    print(f"  scenario params: {metadata}")


if __name__ == "__main__":
    main()
