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


if __name__ == "__main__":
    main()
