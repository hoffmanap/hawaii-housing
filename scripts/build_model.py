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
        # CORRECTED 2026-10-14, per direct user review of Kauai County
        # Code: ADUs are permitted ONLY on single-family-zoned land, not
        # the broader CN/CG/AG/O list an earlier pass pulled from the
        # "guest house" accessory-use provision (§8-1.4(i)) -- guest
        # houses and ADUs are evidently NOT the same eligible-zone set in
        # this code, and conflating them overstated eligibility. Single-
        # family-zoned land = the R-numbered density districts (R-1 to
        # R-6, R-10 to R-20 -- a NUMERIC RANGE, not an enumerated list: it
        # includes R-15 and excludes R-7/8/9, the gap between the two
        # ranges). is_adu_eligible_zone() checks the parsed district
        # number against this range; there is no non-R fallback for ADUs
        # any more -- CN/CG/AG/O parcels are not ADU-eligible.
        "adu_eligible_rdistrict_ranges": ((1, 6), (10, 20)),
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
    """Numeric-range check against single-family-zoned R-districts (see
    adu_eligible_rdistrict_ranges above). ADUs are single-family-only per
    Kauai County Code -- a non-R zone (AG, O, CN, CG, CON, ...) is never
    eligible, full stop."""
    if not isinstance(zoning, str):
        return False
    m = RDISTRICT_PATTERN.match(zoning)
    if not m:
        return False
    n = int(m.group(1))
    return any(lo <= n <= hi for lo, hi in cfg.get("adu_eligible_rdistrict_ranges", ()))

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
        allow_non_single_family_zones: bool = False,
    ):
        self.min_lot_sqft = min_lot_sqft
        self.absorption_rate = absorption_rate
        self.require_owner_occupancy = require_owner_occupancy
        self.require_parking = require_parking
        # The actual policy lever, not a data-quality fallback: current
        # Kauai code restricts ADUs to single-family-zoned (R-numbered)
        # land only -- a confirmed fact, not a placeholder (see
        # COUNTY_CONFIG and is_adu_eligible_zone()). Setting this True
        # models the REFORM of extending ADU eligibility to non-single-
        # family zones (AG, O, CN, CG), gated by min_lot_sqft there since
        # those zones have no density-per-acre number to check against.
        # False (the default) reproduces current law exactly.
        self.allow_non_single_family_zones = allow_non_single_family_zones

    @property
    def is_baseline(self) -> bool:
        """True iff every field reproduces current code exactly -- i.e.
        this is the Challenge's required baseline, not an intervention."""
        return (
            not self.require_owner_occupancy
            and not self.require_parking
            and not self.allow_non_single_family_zones
        )


ADU_NON_SINGLE_FAMILY_INTERVENTION_ZONING_PREFIXES = ("A", "O", "C-N", "C-G")


def run_adu_scenario(county: str, params: ScenarioParams | None = None) -> tuple[pd.DataFrame, dict]:
    """
    Cheap re-run against the already-bucketed parcel table (no geometry
    work) -- this is the function a slider UI would call on every drag.
    Returns (tract-level results, metadata about what was/wasn't applied).

    ScenarioParams() with every field at its default IS the Challenge's
    required BASELINE: current Kauai County Code exactly as written
    (single-family-zoned land only, the real density-per-acre rule,
    no owner-occupancy requirement, parking required as coded). Any
    non-default field is a POLICY INTERVENTION -- a change beyond what
    current law allows. metadata["is_baseline"] says which one a given
    call represents, and run_adu_policy_impact() below computes the
    incremental difference a reform makes against this same baseline,
    which is the number the Challenge actually asks for.
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
    # Baseline eligibility: single-family-zoned (R-numbered) land only,
    # per current code -- is_adu_eligible_zone() has no non-R fallback.
    zone_ok = parcel_table["zoning"].apply(lambda z: is_adu_eligible_zone(z, cfg))
    if params.allow_non_single_family_zones:
        # INTERVENTION, not baseline: current code does not permit this.
        zone_ok |= parcel_table["zoning"].fillna("").str.startswith(
            ADU_NON_SINGLE_FAMILY_INTERVENTION_ZONING_PREFIXES
        )

    # The real density rule (Kauai County Code §8-4.2(b)/§8-4.6) governs
    # R-districts: a second unit is allowed if existing + 1 doesn't
    # exceed what the district's units/acre allows on this parcel's
    # actual area. The non-single-family intervention zones have no
    # units/acre number, so they're gated by min_lot_sqft instead when
    # that intervention is active.
    has_density_rule = parcel_table["density_per_acre"].notna()
    density_ok = has_density_rule & (
        parcel_table["max_units_by_density"] >= parcel_table["existing_units_estimate"] + 1
    )
    flat_lot_ok = ~has_density_rule & (parcel_table["parcel_area_sqft"] >= params.min_lot_sqft)

    eligible = zone_ok & parcel_table["has_existing_residential"] & (density_ok | flat_lot_ok)
    if params.require_owner_occupancy:
        # INTERVENTION: current code has no owner-occupancy requirement
        # (confirmed directly against the ordinance text) -- this models
        # a hypothetical future restriction, not anything real today.
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
        "is_baseline": params.is_baseline,
        "min_lot_sqft": params.min_lot_sqft,
        "absorption_rate": absorption_rate,
        "absorption_rate_was_county_benchmark": params.absorption_rate is None,
        "require_owner_occupancy": params.require_owner_occupancy,
        "require_parking": params.require_parking,
        "allow_non_single_family_zones": params.allow_non_single_family_zones,
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

    out_name = f"{county}_adu_baseline_tract.csv" if params.is_baseline else f"{county}_adu_intervention_tract.csv"
    adu_tract.to_csv(OUT_DIR / out_name, index=False)
    return adu_tract, metadata


def run_adu_policy_impact(county: str, intervention_params: ScenarioParams) -> tuple[pd.DataFrame, dict]:
    """
    What the Challenge actually asks for: not a standalone intervention
    number, but the INCREMENTAL impact of a policy change against the
    current-code baseline. Runs both and returns their tract-level
    difference. Raises if intervention_params doesn't actually represent
    a change (comparing the baseline to itself is never the right call).
    """
    if intervention_params.is_baseline:
        raise ValueError(
            "intervention_params matches the baseline exactly (no field differs from "
            "ScenarioParams() defaults) -- there's no policy change to measure the impact of."
        )
    baseline_df, baseline_meta = run_adu_scenario(county, ScenarioParams())
    intervention_df, intervention_meta = run_adu_scenario(county, intervention_params)

    impact = intervention_df.merge(
        baseline_df, on="GEOID", how="outer", suffixes=("_intervention", "_baseline")
    ).fillna(0)
    impact["additional_eligible_parcels"] = (
        impact["eligible_parcel_count_intervention"] - impact["eligible_parcel_count_baseline"]
    )
    impact["additional_estimated_units"] = (
        impact["estimated_new_units_intervention"] - impact["estimated_new_units_baseline"]
    )

    out_path = OUT_DIR / f"{county}_adu_policy_impact_tract.csv"
    impact.to_csv(out_path, index=False)
    metadata = {"baseline": baseline_meta, "intervention": intervention_meta}
    return impact, metadata


MISSING_MIDDLE_MAX_UNITS = 6  # Challenge benchmark definition: 2-6 units, up to 3 stories


def run_missing_middle_baseline(county: str) -> pd.DataFrame:
    """
    BASELINE capacity under current code, R-districts only: how many
    units a parcel could already legally hold today under its district's
    confirmed density-per-acre rule (§8-4.2(b)/§8-4.6), uncapped at 6 --
    some larger R-20 lots legally allow more than 6 units today, and that
    is NOT a Missing Middle reform, it's current law. Per-parcel
    baseline_capacity = max_units_by_density - existing_units_estimate,
    floored at 0.
    """
    parcel_path = OUT_DIR / f"{county}_parcel_level.csv"
    if not parcel_path.exists():
        raise FileNotFoundError(f"Run build_county('{county}') first -- {parcel_path} not found")
    parcel_table = pd.read_csv(parcel_path)
    has_density_rule = parcel_table["density_per_acre"].notna()
    parcel_table["baseline_capacity"] = (
        parcel_table["max_units_by_density"] - parcel_table["existing_units_estimate"]
    ).clip(lower=0)
    parcel_table.loc[~has_density_rule, "baseline_capacity"] = pd.NA
    return parcel_table


def run_missing_middle_scenario(county: str) -> tuple[pd.DataFrame, dict]:
    """
    Missing Middle / Gentle Density INTERVENTION: the reform only applies
    to parcels that are CURRENTLY single-family-restricted in practice --
    i.e. baseline_capacity == 0 (today's density math on this parcel's
    actual area allows exactly the existing unit and no more, same
    threshold run_adu_scenario() checks for a second unit). For those
    parcels, and only those, this models upzoning to the Challenge's
    benchmark ceiling of 6 units. A parcel a district's density math
    already lets build past its current unit count (most R-6+ parcels
    above a certain size) is NOT a Missing Middle candidate -- that's
    already-legal baseline capacity, reported separately by
    run_missing_middle_baseline(), and double-counting it here as
    "reform impact" would overstate what the policy actually changes.

    Not summed with the ADU scenario for the same parcel, for the same
    reason: both draw on the same underlying district density allowance.
    A parcel's combined headline number should take the max of the two
    interventions, not their sum; that reconciliation isn't wired in yet.
    """
    parcel_table = run_missing_middle_baseline(county)
    cfg = COUNTY_CONFIG[county]

    currently_single_family_restricted = parcel_table["baseline_capacity"] == 0
    reform_capacity = (MISSING_MIDDLE_MAX_UNITS - parcel_table["existing_units_estimate"]).clip(lower=0)
    additional_capacity = reform_capacity.where(currently_single_family_restricted, 0)

    eligible_mask = additional_capacity > 0
    eligible = parcel_table[eligible_mask].copy()
    eligible["additional_unit_capacity"] = additional_capacity[eligible_mask]

    tract_rollup = (
        eligible.groupby("GEOID", observed=True)
        .agg(
            eligible_parcel_count=(cfg["parcel_id_field"], "count"),
            additional_gross_capacity=("additional_unit_capacity", "sum"),
        )
        .reset_index()
    )
    absorption_rate = absorption_rate_benchmark_for(county)
    tract_rollup["additional_estimated_units"] = (
        tract_rollup["additional_gross_capacity"] * absorption_rate
    ).round(1)

    out_path = OUT_DIR / f"{county}_missing_middle_policy_impact_tract.csv"
    tract_rollup.to_csv(out_path, index=False)
    metadata = {
        "is_baseline": False,
        "absorption_rate": absorption_rate,
        "max_units_cap": MISSING_MIDDLE_MAX_UNITS,
        "applies_to": "Only parcels currently single-family-restricted in practice "
        "(baseline_capacity == 0 under the real density rule) -- a parcel a district's "
        "density math already lets build past its current unit count is baseline capacity, "
        "not a Missing Middle reform effect, and is excluded here to avoid double-counting it "
        "as policy impact. See run_missing_middle_baseline() for that separate number. "
        "AG/O/CN/CG parcels are excluded entirely -- no units/acre figure to compute against.",
        "known_limitation": "existing_units_estimate assumes 1 unit per already-residential "
        "parcel (no direct unit-count field for Kauai) -- affects both the baseline and the "
        "reform-eligibility threshold here.",
    }
    return tract_rollup, metadata


# Transit-Oriented Density -- "major transit stop" definition and proximity
# threshold, per the Challenge benchmark's own requirement to define and
# justify both.
#
# MAJOR TRANSIT STOP: Kauai has no rail or BRT. Per the user's explicit
# correction (2026-10-14) to an earlier draft that scoped this scenario to
# Honolulu's rail stations -- "there's nothing that says it has to be built
# around transit stations. If the counties have rapid transit we could base
# it around bus lines" -- this scenario is built on Kauai Bus's real,
# actively-maintained GTFS feed (MobilityDatabase, NTD ID 90237; see
# fetch_kauai_bus_major_stops() in fetch_data.py) instead. "Major" = served
# by 2+ distinct routes on a weekday: Kauai Bus's network is a hub-and-spoke
# of single-route shuttle loops feeding a handful of mainline/transfer
# stops, so route-count is a clean, real, data-driven line between a
# trunk/transfer stop (more destinations reachable, overlapping headways)
# and a stop that's only ever served by one shuttle's own loop. 58 of 126
# weekday-served stops qualify as of this feed.
#
# PROXIMITY THRESHOLD: half a mile, "about a ten-minute walk" -- the State
# of Hawaii TOD Program's own station-area standard
# (planning.hawaii.gov/lud/state-tod/), applied there to Honolulu's rail
# stations but stated in mode-agnostic terms ("transit facility," not
# "rail station"); this project extends it to bus stops on that same
# reading, consistent with the Challenge's own "major transit stops"
# language (also mode-agnostic).
HALF_MILE_METERS = 804.672

# REFORM DENSITY TARGET -- the one input here that is a genuine cross-
# geography extrapolation, flagged explicitly rather than presented as a
# Kauai-specific figure: ~50 units/gross acre is HHFDC/DAGS's adopted
# Master Plan density target for Honolulu's Iwilei-Kapalama TOD Pilot Area
# (OPSD TOD Infrastructure Finance Deliverable 1, Sec. on pilot-area master
# planning; 552 acres, 700+ properties, 27,400-unit target). That is Oahu
# rail-corridor redevelopment data, not a Kauai bus-corridor figure -- no
# Kauai-specific or generic-Hawaii bus-TOD density standard was found in
# the OPSD deliverables, the State TOD Program page, or elsewhere in this
# project's research. Used here as the only real, cited Hawaii TOD density
# figure available, with this caveat carried into every output that uses
# it (metadata["density_target_caveat"] below) rather than silently
# treating it as Kauai-appropriate.
TOD_REFORM_DENSITY_UNITS_PER_ACRE = 50
TOD_DENSITY_TARGET_CAVEAT = (
    "~50 units/gross acre is Honolulu's Iwilei-Kapalama rail-TOD-pilot Master Plan target "
    "(HHFDC/DAGS, OPSD TOD Infra Finance Deliverable 1), applied here to Kauai bus-corridor "
    "parcels as the only real, cited Hawaii TOD density figure found -- not a Kauai-specific "
    "or bus-corridor-specific standard. Treat the resulting capacity as an upper-bound "
    "estimate, not a validated Kauai target."
)


def kauai_tod_eligible_parcels(major_stops_csv: Path | None = None) -> pd.DataFrame:
    """
    Buffers Kauai Bus's major stops (2+ weekday routes; see
    fetch_kauai_bus_major_stops()) by the half-mile threshold above, and
    flags every Kauai parcel whose centroid falls inside the buffer.
    Returns one row per parcel (parcel_id_field, tod_eligible), meant to be
    merged onto the parcel_level.csv table the same way build_county()'s
    other per-parcel flags are.
    """
    stops_path = major_stops_csv or (RAW_DIR / "kauai_bus_major_stops.csv")
    if not stops_path.exists():
        raise RuntimeError(
            f"{stops_path} not found -- run `fetch_data.py --layer kauai_bus_major_stops` first."
        )
    stops = pd.read_csv(stops_path)
    major = stops[stops["is_major_stop"].astype(str) == "True"]
    if major.empty:
        raise RuntimeError(f"No major stops found in {stops_path}.")

    cfg = COUNTY_CONFIG["kauai"]
    pid = cfg["parcel_id_field"]
    parcels = _load_geojson(RAW_DIR / cfg["parcels_file"]).to_crs(PROJECTED_CRS)
    centroids = parcels[[pid, "geometry"]].copy()
    centroids["geometry"] = centroids.geometry.centroid

    stop_points = gpd.GeoDataFrame(
        major, geometry=gpd.points_from_xy(major["stop_lon"], major["stop_lat"]), crs="EPSG:4326"
    ).to_crs(PROJECTED_CRS)
    station_area = stop_points.geometry.buffer(HALF_MILE_METERS).union_all()

    centroids["tod_eligible"] = centroids.geometry.within(station_area)
    return centroids[[pid, "tod_eligible"]]


def run_tod_scenario(
    county: str = "kauai", density_per_acre: float = TOD_REFORM_DENSITY_UNITS_PER_ACRE
) -> tuple[pd.DataFrame, dict]:
    """
    Transit-Oriented Density INTERVENTION: for parcels within the major-
    transit-stop buffer (kauai_tod_eligible_parcels()), models the effect
    of raising allowable density to density_per_acre -- i.e. the capacity
    unlocked by MORE development rights near transit than current zoning
    allows, which is what the Challenge benchmark actually asks for
    ("model the effect of increasing allowable residential density near
    major transit stops"). Per the same baseline/intervention principle
    used everywhere else in this project: additional_estimated_units =
    max(0, reform_capacity - existing_units_estimate), not reform_capacity
    on its own, so this never reports a TOD-eligible parcel's full zoned
    capacity as if it were the reform's incremental effect.

    Parking: the benchmark lets teams "assume that parking requirements
    are reduced or eliminated" for this scenario -- not modeled as a
    separate constraint here since no parking-ratio ordinance data has
    been sourced yet (same gap noted in run_adu_scenario()'s
    parking_warning); this scenario's capacity number already assumes
    parking isn't the binding constraint, consistent with that allowance.

    Currently Kauai-only (the only county with a real transit GTFS feed
    identified so far -- see docs/DATA_SOURCES.md).
    """
    if county != "kauai":
        raise NotImplementedError(
            f"run_tod_scenario only supports Kauai so far -- no transit stop data "
            f"sourced yet for {county}."
        )
    cfg = COUNTY_CONFIG[county]
    pid = cfg["parcel_id_field"]
    parcel_path = OUT_DIR / f"{county}_parcel_level.csv"
    if not parcel_path.exists():
        raise FileNotFoundError(f"Run build_county('{county}') first -- {parcel_path} not found")
    parcel_table = pd.read_csv(parcel_path)

    eligibility = kauai_tod_eligible_parcels()
    parcel_table[pid] = parcel_table[pid].astype(str)
    eligibility[pid] = eligibility[pid].astype(str)
    parcel_table = parcel_table.merge(eligibility, on=pid, how="left")
    parcel_table["tod_eligible"] = parcel_table["tod_eligible"].fillna(False)

    reform_capacity = (
        (parcel_table["parcel_area_sqft"] / 43_560) * density_per_acre
    ).apply(math.floor)
    additional_capacity = (reform_capacity - parcel_table["existing_units_estimate"]).clip(lower=0)
    additional_capacity = additional_capacity.where(parcel_table["tod_eligible"], 0)

    eligible_mask = (parcel_table["tod_eligible"]) & (additional_capacity > 0)
    eligible = parcel_table[eligible_mask].copy()
    eligible["additional_unit_capacity"] = additional_capacity[eligible_mask]

    tract_rollup = (
        eligible.groupby("GEOID", observed=True)
        .agg(
            eligible_parcel_count=(pid, "count"),
            additional_gross_capacity=("additional_unit_capacity", "sum"),
        )
        .reset_index()
    )
    absorption_rate = absorption_rate_benchmark_for(county)
    tract_rollup["additional_estimated_units"] = (
        tract_rollup["additional_gross_capacity"] * absorption_rate
    ).round(1)

    out_path = OUT_DIR / f"{county}_tod_policy_impact_tract.csv"
    tract_rollup.to_csv(out_path, index=False)
    metadata = {
        "is_baseline": False,
        "major_transit_stop_definition": "Kauai Bus stop served by 2+ distinct weekday routes "
        "(58 of 126 weekday-served stops, per the GTFS feed fetched 2026-10-14).",
        "proximity_threshold": "Half-mile radius -- the State of Hawaii TOD Program's own "
        "station-area standard, extended here from rail to bus stops.",
        "density_per_acre": density_per_acre,
        "density_target_caveat": TOD_DENSITY_TARGET_CAVEAT,
        "absorption_rate": absorption_rate,
        "parking_assumption": "Reduced/eliminated per the benchmark's own allowance for this "
        "scenario -- not separately modeled as a constraint (no parking-ratio ordinance data "
        "sourced yet).",
        "total_tod_eligible_parcels": int(parcel_table["tod_eligible"].sum()),
    }
    return tract_rollup, metadata


# Real, digitized figures from Sohoni & Lee, "Parking the Minimum Parking
# Requirement: Evidence from Nine U.S. Cities," Journal of Planning
# Education and Research 46(1), 2025 (user-supplied PDF, 2026-10-14) --
# Table 3 (parking supply before/after as % of the former MPR, treatment
# group) and the qualitative density/car-dependency framing on p.98. These
# are the paper's OWN reported numbers, not a reduction ratio inferred by
# eyeballing Figure 4's unlabeled scatter points -- that chart is real
# (R^2 = 0.168 for % alt-mode commute share, R^2 = 0.317 for block-group
# density, both against pp reduction in parking supply) but its individual
# city coordinates aren't numerically labeled in the text, so this project
# isn't going to invent precision reading them off a plot.
PARKING_STUDY_CITY_BENCHMARKS = {
    # city: (pct_mpr_before, pct_mpr_after, context)
    "San Francisco, CA": (45.0, 30.3, "high density, low car dependency"),
    "Buffalo, NY": (107.9, 92.3, "moderate density/car dependency"),
    "Hartford, CT": (109.8, 103.0, "low density, high car dependency -- minimal effect"),
    "Washington, DC": (266.9, 239.0, "high density, low car dependency"),
    "Grand Rapids, MI": (127.2, 123.4, "low density, high car dependency -- minimal effect"),
    "Cincinnati, OH": (83.5, 25.0, "targeted downtown reform, large reduction"),
    "Champaign, IL": (117.1, 44.7, "targeted reform, largest reduction in the sample"),
    "Somerville, MA": (100.2, 32.4, "targeted TOD reform, large reduction"),
    "Honolulu, HI": (88.6, 75.0, "TOD-district reform, same state as this model, Oahu not Kauai"),
}
PARKING_STUDY_LOW_END_ALT_MODE_COMMUTE_CONTEXT = (
    "Grand Rapids and Hartford -- the two cities in the study showing minimal post-reform "
    "change -- are described by the authors as ranking 'very low in density and very high in "
    "car dependency' (p.98), and even they are U.S. small/mid-size cities with some transit "
    "and non-car commute share. Kauai has neither."
)


def kauai_parking_reform_context(acs_commute_density_csv: Path | None = None) -> dict:
    """
    Places Kauai on the same two axes (non-car commute share, population
    density) Sohoni & Lee (2025) use to explain why some cities' post-reform
    parking supply barely moved while others' fell sharply -- using REAL
    ACS data (fetch_data.py --layer acs_commute_density --county kauai),
    not an assumed number. Returns Kauai's own figures plus an explicit,
    non-numeric verdict: this project does NOT apply a quantitative
    post-reform parking-reduction ratio to Kauai, for two stated reasons:

    1. Every city in the study's sample is denser and less car-dependent
       than Kauai is countywide, including the two "minimal effect" cities
       (Grand Rapids, Hartford) the authors use as their own low-end anchor.
       Reading Kauai's number off the fitted line would be extrapolation
       past the regression's support, not interpolation within it.
    2. The study's x-axis is "mean BLOCK GROUP density/commute share WITHIN
       THE DEREGULATED ZONE," i.e. the already-built-up area being
       upzoned -- not a countywide average. Kauai's own eligible-parcel
       areas (the ADU/parking-scenario tracts) would be the fairer
       comparison, but even those don't approach the study sample's range
       (see the per-tract figures in the CSV this reads).

    Net: `run_parking_reduction_scenario()` keeps reporting land consumed
    under the current 1-space/unit requirement, not a reduced-but-nonzero
    post-reform figure -- the evidence supports "expect little to no
    reduction," not a specific number.
    """
    path = acs_commute_density_csv or (RAW_DIR / "acs_commute_density_kauai.csv")
    if not path.exists():
        return {
            "available": False,
            "reason": f"{path} not found -- run `fetch_data.py --layer acs_commute_density "
            "--county kauai` (requires a free Census API key) first.",
        }
    df = pd.read_csv(path)
    total_commuters = df["commuters_excl_wfh"].sum()
    total_pop = df["population"].sum()
    countywide_alt_mode_pct = (df["pct_alt_mode_commute"] * df["commuters_excl_wfh"]).sum() / total_commuters
    countywide_density = (df["density_per_sqmi"] * df["population"]).sum() / total_pop
    return {
        "available": True,
        "countywide_alt_mode_commute_pct": round(countywide_alt_mode_pct, 1),
        "countywide_density_per_sqmi": round(countywide_density, 1),
        "study_city_benchmarks": PARKING_STUDY_CITY_BENCHMARKS,
        "low_end_context": PARKING_STUDY_LOW_END_ALT_MODE_COMMUTE_CONTEXT,
        "verdict": "Kauai's countywide non-car commute share (~5%) and density (~675 people/sq "
        "mi) sit below the study's own 'minimal effect' anchor cities (Grand Rapids, Hartford) "
        "on both axes -- consistent with the Challenge's starting assumption that reducing "
        "parking requirements changes land use, not with a specific predicted reduction ratio. "
        "No numeric post-reform parking-supply ratio is applied to Kauai as a result.",
    }


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
    if county == "kauai":
        metadata["post_reform_parking_supply_context"] = kauai_parking_reform_context()

    out_path = OUT_DIR / f"{county}_parking_scenario_tract.csv"
    adu_tract.to_csv(out_path, index=False)
    return adu_tract, metadata


# Per the Official Rules: use this standardized figure for comparability
# across submissions rather than sourcing four different real county fee
# schedules (Honolulu's are documented, the outer counties' much less
# so -- see the Baseline table above). This is a Challenge-mandated
# assumption, not a researched Kauai-specific number.
STANDARD_DEVELOPMENT_FEE_PER_UNIT_USD = 50_000


def run_development_fee_reduction_scenario(
    scenario_tract_df: pd.DataFrame, estimated_units_col: str = "estimated_new_units"
) -> pd.DataFrame:
    """
    Development Fee Reduction, deliberately NOT modeled as a change in
    unit production. Reducing or eliminating a development fee changes
    project ECONOMICS, not zoning eligibility -- it doesn't make a parcel
    legally buildable that wasn't before, and translating "lower fee" into
    "more units get built" requires a fee-elasticity-of-housing-supply
    estimate. Real estimates of that elasticity exist in the literature
    (e.g. RAND's 2024 impact-fee study), but they're city-specific,
    methodology-dependent, and not something I could responsibly apply to
    Hawaii without real risk of misapplying someone else's market's
    number here. Rather than invent a plausible-looking multiplier, this
    reports the one honest number available without that leap: total
    dollar fee savings on housing that's ALREADY eligible under whichever
    scenario's tract-level output is passed in (ADU baseline, Missing
    Middle policy impact, etc.) -- a feasibility metric, not a production
    estimate. Pass any tract-level DataFrame with a unit-count column;
    this doesn't change which parcels are eligible or how many units are
    assumed, only prices the fee savings on top of that existing number.
    """
    result = scenario_tract_df.copy()
    result["fee_savings_usd"] = result[estimated_units_col] * STANDARD_DEVELOPMENT_FEE_PER_UNIT_USD
    return result


# Real, Kauai-specific development pro forma inputs, digitized 2026-10-14 from
# the "Real Estate Assumptions" table (Lihu'e Town Core / Kaua'i column) in
# the State of Hawaii OPSD's "Financial Analysis of TOD Infrastructure
# Funding and Financing Options" (HR&A Advisors et al., July/Sept. 2023,
# user-supplied PDF). This is what the Terner Center pro forma methodology
# (see "Development feasibility (pro forma) research" section above) was
# missing: real Hawaii hard-cost, land-value, financing, and sale-price
# inputs, instead of California submarket data. Sourced in the original
# table to Group Pacific/PBR Hawai'i (hard costs), HR&A (soft costs,
# developer profit), CBRE (financing terms), and CoStar/Zillow (sale
# prices) -- not this project's own estimate.
KAUAI_LIHUE_PRO_FORMA_INPUTS = {
    "townhome_for_sale": {
        "unit_size_gsf": 1_500,
        "hard_cost_per_gsf_usd": 420,
        "soft_cost_pct_of_hard": 0.15,
        "sale_price_per_unit_usd": {"market": 1_179_403, "affordable": 604_600},
    },
    "lowrise_for_sale": {
        "unit_size_gsf": 840,  # 700 GSF rental base x 1.20 for-sale size premium
        "hard_cost_per_gsf_usd": 594,
        "soft_cost_pct_of_hard": 0.15,
        "sale_price_per_unit_usd": {"market": 637_974, "affordable": 302_300},
    },
}
KAUAI_LIHUE_LAND_PRICE_PER_BUILDING_GSF_USD = 36  # "Average Price per SF, Mid- and High-Density Residential"
KAUAI_LIHUE_FINANCING = {
    "loan_to_cost": 0.60,
    "construction_interest_rate": 0.07,
    "construction_loan_fee_pct": 0.015,
    "permanent_loan_fee_pct": 0.010,
    "construction_period_months": 24,
}
KAUAI_LIHUE_DEVELOPER_PROFIT_MARGIN_PCT_OF_SALE = 0.15  # "Residential for sale: Profit Margin (%)"


def kauai_development_fee_reduction_feasibility(
    typology: str = "townhome_for_sale",
    affordability: str = "market",
    development_fee_usd: float = STANDARD_DEVELOPMENT_FEE_PER_UNIT_USD,
) -> dict:
    """
    A real, Kauai-specific development pro forma for the Lihu'e Town Core
    submarket, answering the question run_development_fee_reduction_scenario()
    explicitly declined to answer with a fabricated elasticity: does reducing
    the Challenge's standardized $50,000/unit development fee actually change
    whether a project pencils?

    Uses the "excess value" feasibility test from the same source report
    (market value minus total development cost minus the developer's
    required return -- see docs/MODEL_AND_ASSUMPTIONS.md): a project is
    feasible when excess_value >= 0, and a fee reduction adds dollar-for-
    dollar to excess_value without otherwise changing the pro forma.

    TWO EXPLICIT SIMPLIFICATIONS, not present in the source report's own
    (more detailed) model -- this is a transparent approximation, not a
    claim of matching HR&A/CBRE's full model exactly:
    1. Land cost is allocated as $/building-GSF (the source table's land
       price per sqft of *residential* building area), not a true site-area
       /FAR-based allocation -- Kauai's per-district FAR/density standard
       for Lihu'e isn't in this model's data yet (see the ADU min-lot-sqft
       gap flagged elsewhere in this doc).
    2. Financing cost uses a single average-outstanding-balance
       approximation (loan amount x rate x term x 0.5, plus origination
       fees) rather than the source's presumed full monthly draw schedule --
       a reasonable estimate, not a dollar-precise reproduction.
    """
    inputs = KAUAI_LIHUE_PRO_FORMA_INPUTS[typology]
    unit_size = inputs["unit_size_gsf"]
    hard_cost = unit_size * inputs["hard_cost_per_gsf_usd"]
    soft_cost = hard_cost * inputs["soft_cost_pct_of_hard"]
    land_cost = unit_size * KAUAI_LIHUE_LAND_PRICE_PER_BUILDING_GSF_USD
    cost_before_financing_and_fee = hard_cost + soft_cost + land_cost

    fin = KAUAI_LIHUE_FINANCING
    loan_amount = cost_before_financing_and_fee * fin["loan_to_cost"]
    construction_years = fin["construction_period_months"] / 12
    financing_interest = loan_amount * fin["construction_interest_rate"] * construction_years * 0.5
    financing_fees = loan_amount * (fin["construction_loan_fee_pct"] + fin["permanent_loan_fee_pct"])
    financing_cost = financing_interest + financing_fees

    total_development_cost = cost_before_financing_and_fee + financing_cost + development_fee_usd
    sale_price = inputs["sale_price_per_unit_usd"][affordability]
    required_developer_profit = sale_price * KAUAI_LIHUE_DEVELOPER_PROFIT_MARGIN_PCT_OF_SALE
    excess_value = sale_price - total_development_cost - required_developer_profit

    return {
        "typology": typology,
        "affordability": affordability,
        "development_fee_usd": development_fee_usd,
        "hard_cost_usd": round(hard_cost),
        "soft_cost_usd": round(soft_cost),
        "land_cost_usd": round(land_cost),
        "financing_cost_usd": round(financing_cost),
        "total_development_cost_usd": round(total_development_cost),
        "sale_price_usd": sale_price,
        "required_developer_profit_usd": round(required_developer_profit),
        "excess_value_usd": round(excess_value),
        "feasible": excess_value >= 0,
        "source": "HR&A Advisors et al., 'Financial Analysis of TOD Infrastructure Funding and "
        "Financing Options,' State of Hawaii OPSD, 2023 -- Real Estate Assumptions table, "
        "Lihu'e Town Core (Kaua'i) column.",
    }


# Kauai countywide median home value (ACS 2022 5-year, B25077), used ONLY as
# the denominator to scale the Lihu'e Town Core pro forma's reference sale
# price to each tract's own housing market -- see
# tract_scaled_development_fee_reduction_feasibility() below. This is a
# linear scaling heuristic, not a tract-specific appraisal: it assumes a
# tract twice as expensive as the county median would sell new product at
# roughly twice Lihu'e's reference price, holding the COST side of the pro
# forma (hard cost, land cost, financing) fixed at the Lihu'e table's
# figures, since no tract-level cost data exists. Verified live via Census
# API, 2026-10-14.
KAUAI_COUNTYWIDE_MEDIAN_HOME_VALUE_USD = 742_900


def tract_scaled_development_fee_reduction_feasibility(
    acs_commute_density_csv: Path | None = None,
    typology: str = "townhome_for_sale",
    affordability: str = "market",
    development_fee_usd: float = STANDARD_DEVELOPMENT_FEE_PER_UNIT_USD,
) -> pd.DataFrame:
    """
    Runs kauai_development_fee_reduction_feasibility() once per tract,
    scaling the Lihu'e reference sale price by each tract's own ACS median
    home value relative to the countywide median -- rather than applying
    one submarket's feasibility verdict to the whole county. This is what
    lets a scenario's tract-level unit estimate be gated by a feasible
    column (the toggle the user asked for 2026-10-14), without claiming
    tract-specific cost data this project doesn't have.

    EXPLICIT LIMITATIONS (same honesty standard as the single-submarket
    version this builds on):
    - Scales REVENUE (sale price) by relative home value; cost inputs
      (hard cost, land cost, financing) stay fixed at the Lihu'e table's
      figures for every tract. A tract's land cost almost certainly
      differs from Lihu'e's too, but no tract-level land-cost source is
      in this project yet -- scaling only one side of the ledger is a
      real asymmetry, stated here rather than hidden.
    - Tracts with no reliable ACS home-value estimate (Census's
      "cannot be computed" sentinel, already converted to null by
      fetch_data.py) are left out, not filled with a guess.
    """
    path = acs_commute_density_csv or (RAW_DIR / "acs_commute_density_kauai.csv")
    if not path.exists():
        raise RuntimeError(
            f"{path} not found -- run `fetch_data.py --layer acs_commute_density --county kauai` first."
        )
    acs = pd.read_csv(path)
    acs = acs.dropna(subset=["median_home_value_usd"]).copy()

    base_inputs = KAUAI_LIHUE_PRO_FORMA_INPUTS[typology]
    reference_sale_price = base_inputs["sale_price_per_unit_usd"][affordability]

    rows = []
    for _, tract in acs.iterrows():
        scale = tract["median_home_value_usd"] / KAUAI_COUNTYWIDE_MEDIAN_HOME_VALUE_USD
        scaled_sale_price = reference_sale_price * scale
        scaled_inputs = dict(KAUAI_LIHUE_PRO_FORMA_INPUTS[typology])
        scaled_inputs["sale_price_per_unit_usd"] = {affordability: scaled_sale_price}
        # Temporarily substitute the scaled inputs rather than duplicating
        # the whole pro forma calculation here.
        original = KAUAI_LIHUE_PRO_FORMA_INPUTS[typology]
        KAUAI_LIHUE_PRO_FORMA_INPUTS[typology] = scaled_inputs
        try:
            result = kauai_development_fee_reduction_feasibility(typology, affordability, development_fee_usd)
        finally:
            KAUAI_LIHUE_PRO_FORMA_INPUTS[typology] = original
        rows.append(
            {
                "GEOID": str(tract["GEOID"]),
                "median_home_value_usd": tract["median_home_value_usd"],
                "home_value_scale_vs_countywide": round(scale, 3),
                "scaled_sale_price_usd": round(scaled_sale_price),
                "excess_value_usd": result["excess_value_usd"],
                "feasible": result["feasible"],
            }
        )
    return pd.DataFrame(rows)


def apply_feasibility_gate(
    scenario_tract_df: pd.DataFrame,
    feasibility_df: pd.DataFrame,
    estimated_units_col: str = "estimated_new_units",
) -> pd.DataFrame:
    """
    The toggle itself: joins a scenario's tract-level unit estimates
    (ADU, Missing Middle, etc.) to tract_scaled_development_fee_reduction_
    feasibility()'s per-tract verdict, and adds a
    `feasibility_adjusted_units` column -- the same unit estimate where
    `feasible` is True, zero where it isn't. Both columns stay in the
    output so a consumer (or the dashboard) can show "zoning capacity"
    and "feasibility-adjusted" side by side rather than only the gated
    number.
    """
    scenario_tract_df = scenario_tract_df.copy()
    scenario_tract_df["GEOID"] = scenario_tract_df["GEOID"].astype(str)
    feasibility_df = feasibility_df.copy()
    feasibility_df["GEOID"] = feasibility_df["GEOID"].astype(str)
    merged = scenario_tract_df.merge(
        feasibility_df[["GEOID", "feasible", "excess_value_usd"]], on="GEOID", how="left"
    )
    merged["feasible"] = merged["feasible"].fillna(False)
    merged["feasibility_adjusted_units"] = merged[estimated_units_col].where(merged["feasible"], 0.0)
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--county", choices=list(COUNTY_CONFIG), default="kauai", help="Which county to build (default: kauai)"
    )
    parser.add_argument("--min-lot-sqft", type=float, default=DEFAULT_ADU_MIN_LOT_SQFT)
    parser.add_argument("--absorption-rate", type=float, default=None, help="Overrides the county benchmark")
    parser.add_argument("--require-owner-occupancy", action="store_true")
    parser.add_argument("--require-parking", action="store_true")
    parser.add_argument("--allow-non-single-family-zones", action="store_true")
    args = parser.parse_args()

    print(f"Building model for {args.county}...")
    outputs = build_county(args.county)
    for name, path in outputs.items():
        n_rows = sum(1 for _ in path.open()) - 1
        print(f"  {name}: {n_rows} rows -> {path}")

    print("\n-- ADU: baseline (current code) --")
    baseline_df, baseline_meta = run_adu_scenario(args.county, ScenarioParams())
    print(f"  eligible parcels: {baseline_df['eligible_parcel_count'].sum()}, "
          f"estimated units: {baseline_df['estimated_new_units'].sum()}")
    print(f"  {baseline_meta}")

    params = ScenarioParams(
        min_lot_sqft=args.min_lot_sqft,
        absorption_rate=args.absorption_rate,
        require_owner_occupancy=args.require_owner_occupancy,
        require_parking=args.require_parking,
        allow_non_single_family_zones=args.allow_non_single_family_zones,
    )
    if not params.is_baseline:
        print("\n-- ADU: policy impact (requested params vs. baseline) --")
        impact_df, impact_meta = run_adu_policy_impact(args.county, params)
        print(f"  additional eligible parcels: {impact_df['additional_eligible_parcels'].sum()}, "
              f"additional estimated units: {impact_df['additional_estimated_units'].sum()}")

    print("\n-- Missing Middle: baseline (already-legal capacity under current code) --")
    mm_baseline = run_missing_middle_baseline(args.county)
    print(f"  parcels with existing legal capacity beyond current units: "
          f"{(mm_baseline['baseline_capacity'] > 0).sum()}, "
          f"total baseline capacity: {mm_baseline['baseline_capacity'].sum()}")

    print("\n-- Missing Middle: policy impact (upzoning currently single-family-restricted parcels) --")
    mm_df, mm_meta = run_missing_middle_scenario(args.county)
    print(f"  eligible parcels: {mm_df['eligible_parcel_count'].sum()}, "
          f"additional estimated units: {mm_df['additional_estimated_units'].sum()}")

    print("\n-- Development Fee Reduction: fee savings, not a production estimate --")
    adu_fee_df = run_development_fee_reduction_scenario(baseline_df, "estimated_new_units")
    mm_fee_df = run_development_fee_reduction_scenario(mm_df, "additional_estimated_units")
    print(f"  ADU baseline fee savings: ${adu_fee_df['fee_savings_usd'].sum():,.0f}")
    print(f"  Missing Middle intervention fee savings: ${mm_fee_df['fee_savings_usd'].sum():,.0f}")


if __name__ == "__main__":
    main()
