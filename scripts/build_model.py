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
        "use_rdistrict_density_rule": True,
    },
    "honolulu": {
        "parcels_file": "parcels_oahu_augmented.json",  # see augment_oahu_parcels.py
        "zoning_file": "zoning_honolulu.json",
        "parcel_id_field": "tmk",
        "zoning_field": "zone_class",
        "taxclass_field": "taxclass",
        "lot_area_field": "gisacres",
        "existing_residential_taxclasses": {"RESIDENTIAL"},
        # No owner-occupied/non-owner-occupied distinction exists in this
        # proxy (buildingvalue > 0 only says "has an improvement", not who
        # lives there) -- the owner-occupancy toggle has no effect here,
        # same as any county without this key (see run_adu_scenario()'s
        # owner_occupancy_warning).
        "owner_occupied_taxclasses": None,
        # Real unit count per TMK from legdat_honolulu.json (via
        # augment_oahu_parcels.py), NOT the has-existing-residential ->
        # assume-exactly-1-unit placeholder Kauai's parcel layer forces.
        "existing_units_field": "existing_units",
        # Honolulu's zone strings (R-5, R-3.5, Country, A-1, AMX-1, ...)
        # are NOT a units-per-acre density rule like Kauai's -- Honolulu
        # caps ADUs at exactly one per zoning lot regardless of lot size
        # or density (ROH 21-5.50-3(a)(E)), so the R-number-as-density
        # parser must be disabled for this county or it would wrongly
        # read "R-5" as "5 units/acre".
        "use_rdistrict_density_rule": False,
        # CONFIRMED 2026-10-15 against ROH Table 21-5.1 (Table of Allowed
        # Uses), per direct user-supplied ordinance excerpt: ADU is a
        # permitted use (P*) only in these districts -- explicitly NOT
        # permitted in AG-1/AG-2/P-2 (agricultural/preservation),
        # A-1/A-2/A-3 (apartment), AMX/Resort, B-1/B-2/BMX (business), or
        # I-1/I-2/I-3/IMX (industrial).
        "adu_eligible_districts": {"Country", "R-20", "R-10", "R-7.5", "R-5", "R-3.5"},
        # CONFIRMED 2026-10-15, ROH 21-5.50-3(a): no numeric minimum lot
        # size stated for ADU eligibility (unlike Kauai, where no general
        # ADU ordinance text exists below the R-district density rule) --
        # 0 here means "don't gate on lot size", not "assumed small".
        "adu_min_lot_sqft": 0,
        # CONFIRMED 2026-10-15, ROH 21-5.50-3(a)(B): floor area capped at
        # 500 sqft (lot < 4,999 sqft) or 1,000 sqft (lot >= 5,000 sqft).
        # Not yet wired into a feasibility/pro-forma gate for Honolulu
        # (no Honolulu-specific pro forma exists yet -- see
        # docs/MODEL_AND_ASSUMPTIONS.md's Known Limitations) -- recorded
        # here so that gate can read it once built, not silently ignored.
        "adu_floor_area_cap_sqft": {(0, 4_999): 500, (5_000, float("inf")): 1_000},
        # CONFIRMED 2026-10-15, ROH Table 21-6.1 / 21-6.30(b): +1
        # off-street stall per ADU, waived within 0.5mi of a rail station,
        # or within 800ft of a city bus stop inside the Primary Urban
        # Center/Ewa development plan areas (and not already waived for
        # an ohana unit on the same lot). NOT YET APPLIED as a gate --
        # this project has not yet fetched HART rail-station or TheBus
        # stop-location geometry to test parcels against these distances.
        # require_parking=True currently still falls through to the
        # generic "not yet implemented" warning in run_adu_scenario().
        "adu_parking_added_stalls": 1,
    },
    "hawaii": {
        "parcels_file": "parcels_hawaii_county.json",
        "zoning_file": "zoning_hawaii_county.json",
        "parcel_id_field": "tmk_txt",
        # NOTE: the parcel layer's own "zone" field is a TMK administrative
        # zone number (e.g. "8"), NOT a land-use zoning code -- confirmed
        # 2026-10-24 by inspecting both raw layers directly. The real
        # zoning code lives on the separate zoning_hawaii_county.json
        # layer's own "zone" field (e.g. "RS-10", "A-20a"); the join below
        # (_centroid_join against the zoning layer) is what actually
        # supplies it, so this key points at the zoning layer's "zone",
        # not the parcel layer's same-named field.
        "zoning_field": "zone",
        "taxclass_field": "pittcode",
        "lot_area_field": "gisacres",
        # CONFIRMED 2026-10-26 against Hawai'i County's own authoritative
        # source -- the item description on the county's official ArcGIS
        # layer "County of Hawai'i Properties as of April 2025"
        # (arcgis.com item 9072608159f64005bfdf6aa1fee24197, Real Property
        # Tax Office / Planning Department): "PittCode (Long Integer)
        # Possession, Interest, Time, and Title (PITT) Code used to
        # identify the tax rate applied to the property. For Hawai'i
        # County, PITT Codes are as follows: Residential (100), Apartment
        # (200), Commercial (300), Industrial (400), Agricultural or
        # Native Forests (500), Conservation (600), Hotel and Resort
        # (700), Homeowner (900), Multiple codes for a single TMK (999),
        # or Unknown (0). For tax rate purposes in Hawai'i County, a PITT
        # Code of 0 is used for Affordable Rental Housing." This replaces
        # the earlier "unconfirmed working hypothesis" flag (the /100
        # decode is now a cited fact, not a guess) and corrects one part
        # of it: 0 is NOT a sentinel/unclassified code as previously
        # assumed -- it specifically means Affordable Rental Housing,
        # itself a residential use, so it's now included below (999,
        # "multiple codes for a single TMK," stays excluded -- genuinely
        # ambiguous, not residential-specific).
        "existing_residential_taxclasses": {0, 100, 200, 900},
        # Hawaii County's pittcode scheme makes no owner-occupied /
        # non-owner-occupied distinction (same situation as Honolulu) --
        # the raw "homeowner" Y/N field exists but has not been confirmed
        # to mean owner-OCCUPIED (vs. a homeowner tax exemption, which is
        # a different legal concept with a different eligibility test) --
        # left unwired rather than guessed.
        "owner_occupied_taxclasses": None,
        # CONFIRMED 2026-10-27 against a clean, directly-fetched copy of
        # the primary ordinance text itself (ORD_2024-070_2022-2024.pdf,
        # user-supplied, a legislative redline of Ordinance No. 24-70 /
        # Bill 123 Draft 5 amending Hawai'i County Code Chapter 25,
        # Article 6, Division 3 -- confirmed by rasterizing and visually
        # reading the actual page, not just text-extracting it, given
        # this corrects an earlier claim made in this same codebase).
        # Section 25-6-31(a) permits ADUs on Residential (RS), Duplex
        # (RD), Residential-Agricultural (RA), Family Agricultural (FA),
        # and Agricultural (A) districts -- confirming the district set
        # already used here. Section 25-6-36 ("Density limit") reads:
        # "Accessory dwelling units shall be restricted to a maximum of
        # three per building site, except, where a building site is
        # permitted to have more than one primary dwelling, one accessory
        # dwelling unit shall be allowed for each primary dwelling
        # authorized by the applicable zoning regulations." I.e. the
        # GENERAL rule is up to 3 ADUs per building site; "one per
        # primary dwelling" is only the exception for sites that already
        # permit multiple primary dwellings. An EARLIER version of this
        # comment, based on a different (apparently incomplete or
        # misread) fetch of this same ordinance, stated the reverse --
        # that the 1-per-primary-dwelling exception was the general rule
        # and that news coverage's "up to 3" figure was wrong. That
        # earlier claim is now itself corrected back, on the strength of
        # this cleaner source: the original "up to 3" figure was right
        # all along. (This project's model only computes binary
        # per-parcel eligibility, not a per-parcel ADU count, so neither
        # version of this correction changes any computed figure -- both
        # only affect a secondhand claim stated in commentary/docs.) No lot
        # size minimum gates eligibility (Section 25-6-35's old 10,000
        # sqft minimum building-site-area rule was explicitly repealed by
        # this same ordinance), so adu_min_lot_sqft=0 means "don't gate,"
        # not "assumed small," same convention as Honolulu. The set below
        # is every real zoning code actually present in
        # zoning_hawaii_county.json that starts with one of the five
        # confirmed district prefixes (RS-7.5/10/15/20/22, RA-.5a/1a/2a,
        # RD-3.75, A-1a/2a/3a/5a/10a/20a/40a, FA-1a/2a/3a) -- NOT the full
        # set of codes that could theoretically exist under those
        # prefixes statewide, since Commercial (CV/CN/CG), Industrial
        # (ML/MG), Multi-family (RM), Village (V), and special districts
        # (PD, CDH, NP, FR, OPEN) are outside the ordinance's own
        # district list.
        "adu_eligible_districts": {
            "RS-7.5", "RS-10", "RS-15", "RS-20", "RS-22",
            "RA-.5a", "RA-1a", "RA-2a",
            "RD-3.75",
            "A-1a", "A-2a", "A-3a", "A-5a", "A-10a", "A-20a", "A-40a",
            "FA-1a", "FA-2a", "FA-3a",
        },
        "adu_min_lot_sqft": 0,
        # CONFIRMED 2026-10-26 against the primary ordinance text, Section
        # 25-6-34: "An accessory dwelling unit shall not exceed one
        # thousand two hundred and fifty square feet in living area,
        # exclusive of any lanai, garage, or carport" -- flat (not banded
        # by lot size like Honolulu's), and also capped at the zoning
        # district's own height limit. Not yet wired into any
        # feasibility/pro-forma gate, same caveat as Honolulu's floor-area
        # cap above.
        "adu_floor_area_cap_sqft": {(0, float("inf")): 1_250},
        # Hawaii County's RS/RA/A/FA zone labels end in an acreage/sqft
        # suffix (RS-7.5, A-20a) that is a MINIMUM LOT SIZE for that
        # district, not a units-per-acre density figure the way Kauai's
        # R-numbers are -- the existing max_density_units_per_acre()
        # regex parser is for Kauai's scheme specifically and would
        # misread these, so it stays disabled here.
        "use_rdistrict_density_rule": False,
    },
    "maui": {
        "parcels_file": "parcels_maui.json",
        "zoning_file": "zoning_maui.json",
        "parcel_id_field": "tmk_txt",
        "zoning_field": "zone_dist",
        "taxclass_field": "pittcode",
        "lot_area_field": "gisacres",
        # Same unconfirmed pittcode x100 hypothesis as Hawaii County above
        # (same raw field, same apparent scheme) -- value-count
        # distribution inspected 2026-10-24: 900 (23,727), 100 (12,896),
        # 500 (7,156), 1200 (2,646), 600 (1,281), 300 (1,045), 400 (923),
        # 1100 (467), 999 (465), 700 (209), 200 (209), 1000 (136), 0 (79),
        # 666 (6). Codes above 900 (1000/1100/1200) are not part of the
        # documented 1-9 base scheme and are NOT included below -- treated
        # as unknown/unclassified rather than guessed at, consistent with
        # this project's standard for undocumented codes.
        "existing_residential_taxclasses": {100, 200, 900},
        "owner_occupied_taxclasses": None,
        # CONFIRMED 2026-10-24 via direct WebFetch of Maui County
        # Ordinance 4936 (mauicounty.gov/DocumentCenter/View/115314/Ord-4936),
        # effective July 8, 2026: amends MCC 19.35.010(C) so ADUs are
        # permitted in "any zoning district that allows accessory
        # dwellings" -- cross-referenced to the Residential districts
        # (MCC 19.08.020) and the County's Rural districts (MCC
        # 19.29.030: RU-0.5, RU-1, RU-2, RU-5, RU-10), explicitly
        # excluding Agricultural, Commercial, and Industrial districts
        # (corroborated by Hawaii News Now's summary coverage). The set
        # below is every real zone_dist value actually present in
        # zoning_maui.json that is unambiguously one of these Residential
        # or Rural districts (R-0/1/2/3 Residential; RU-0.5/1 Rural --
        # RU-2/5/10 exist in the county code per the ordinance but no
        # parcel in this dataset currently carries one of those labels).
        # Ambiguous labels NOT included, on purpose, for lack of a
        # confirming source: "R Rural" (42 parcels -- doesn't match the
        # "RU-#" pattern the ordinance actually names), "D-1"/"D-2 Duplex"
        # (9 parcels combined -- a duplex district, not explicitly named
        # as "Residential" in the ordinance text fetched), and "PD Project
        # District" (102 parcels -- a planned-district overlay, not a
        # base residential/rural zone).
        "adu_eligible_districts": {
            "R-0 Residential", "R-1 Residential", "R-2 Residential", "R-3 Residential",
            "RU-0.5 Rural - 1/2 Acre", "RU-1 Rural 1 Acre",
        },
        # CONFIRMED 2026-10-24, Ord. 4936: no minimum lot size gates ADU
        # ELIGIBILITY itself -- a 7,500 sqft threshold instead controls
        # how MANY ADUs a lot may have (2 ADUs on lots >= 7,500 sqft, 1 on
        # smaller lots; Molokai/Lanai allow 1 on lots >= 7,500 sqft). This
        # model only computes binary eligibility, not ADU count per
        # parcel, so that per-lot-size "how many" nuance is NOT applied --
        # a known, documented simplification that makes this a
        # conservative (slightly under-counting, not over-counting) proxy
        # for large-lot capacity specifically.
        "adu_min_lot_sqft": 0,
        # CONFIRMED 2026-10-24, Ord. 4936 Section 19.35.020: max floor
        # area by lot size -- <=7,499 sqft: 500; 7,500-9,999: 600;
        # 10,000-21,779: 720; 21,780-43,559: 840; 43,560-87,119: 960;
        # >=87,120: 1,200 sqft. Not yet wired into any feasibility gate,
        # same caveat as the other two counties' floor-area caps.
        "adu_floor_area_cap_sqft": {
            (0, 7_499): 500, (7_500, 9_999): 600, (10_000, 21_779): 720,
            (21_780, 43_559): 840, (43_560, 87_119): 960, (87_120, float("inf")): 1_200,
        },
        # Maui's R-number (R-1, R-2, R-3) has not been confirmed to mean
        # units-per-acre the way Kauai's does -- same conservative
        # treatment as Honolulu: disabled rather than assumed.
        "use_rdistrict_density_rule": False,
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
    """Two different real ordinance shapes, picked by which cfg key is
    present -- never both:
    - adu_eligible_districts (Honolulu): a flat set of district labels
      confirmed directly against ROH Table 21-5.1, e.g. {"R-5", ...}.
      Membership is a literal string match, not a numeric-range parse --
      Honolulu's ADU cap is one-per-lot regardless of district number,
      so there is no density math to recover from the label.
    - adu_eligible_rdistrict_ranges (Kauai): numeric-range check against
      single-family-zoned R-districts -- see that key's own comment.
    A county config must set exactly one of these two keys."""
    if not isinstance(zoning, str):
        return False
    districts = cfg.get("adu_eligible_districts")
    if districts is not None:
        return zoning in districts
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


def absorption_rate_benchmark_detail(county: str) -> dict:
    """Full benchmark row for one county, for the Assumptions tab's
    absorption-rate chart -- the blended 10yr/2011-25 FRED-derived rates the
    slider defaults to, alongside the genuine pre-COVID (2019-only) rate
    added by scripts/process_building_permits.py from the real Census BPS
    monthly export, so the chart can show both rather than only the single
    number the slider uses."""
    df = load_absorption_rate_benchmarks()
    area = COUNTY_TO_BENCHMARK_AREA[county]
    row = df.loc[df["area"] == area].iloc[0]
    decade_equivalent_pct_2011_25 = (
        100.0 * 10.0 * float(row["avg_annual_permits_2011_2025"]) / float(row["housing_units_2023"])
    )
    return {
        "area": area,
        "decade_equivalent_pct_10yr": float(row["decade_equivalent_pct_10yr"]),
        "decade_equivalent_pct_2011_25": round(decade_equivalent_pct_2011_25, 1),
        "decade_equivalent_pct_2019_precovid": (
            float(row["decade_equivalent_pct_2019_precovid"])
            if "decade_equivalent_pct_2019_precovid" in row and pd.notna(row["decade_equivalent_pct_2019_precovid"])
            else None
        ),
        "avg_annual_permits_2016_2025": float(row["avg_annual_permits_2016_2025"]),
        "avg_annual_permits_2019_precovid": (
            float(row["avg_annual_permits_2019_precovid"])
            if "avg_annual_permits_2019_precovid" in row and pd.notna(row["avg_annual_permits_2019_precovid"])
            else None
        ),
        "housing_units_2023": float(row["housing_units_2023"]),
    }


# Honolulu's real permit-level ADU/Ohana data (data.honolulu.gov Socrata,
# see fetch_honolulu_adu_permits() in fetch_data.py) covers only through
# mid-2025 as pulled 2026-10-23 -- 2025 is a partial year in this export
# (Jan-Jul only), so the "complete years" average below excludes it rather
# than understating the true annual rate with a half-year total.
HONOLULU_ADU_PERMITS_COMPLETE_YEARS = range(2016, 2025)  # 2016-2024 inclusive


def honolulu_real_adu_absorption_rate() -> dict:
    """
    A REAL, scenario-specific absorption rate for Honolulu's ADU scenario,
    calibrated against actual historical ADU/Ohana permit issuance --
    replacing the generic all-housing BPS county benchmark
    (absorption_rate_benchmark_for()) for this one scenario/county
    combination, where real data now supports something better.

    Why this is a meaningfully different (and better) number: the generic
    benchmark is avg_annual_ALL_HOUSING_permits / TOTAL_HOUSING_STOCK --
    a reasonable proxy when no scenario-specific data exists, but it
    answers a different question than "what share of ADU-ELIGIBLE parcels
    actually get an ADU permit in a given year." This function answers
    that narrower question directly: avg_annual_REAL_ADU_permits /
    CURRENT_ADU-ELIGIBLE_parcel_count (from run_adu_scenario("honolulu")'s
    own baseline eligibility rule), scaled to the same 10-year
    decade-equivalent convention used everywhere else in this model.

    **This is Honolulu-only, not a new uniform methodology.** No equivalent
    permit-level, API-accessible dataset exists for Kaua'i (confirmed by
    search 2026-10-23 -- same gap already documented for Kaua'i's sales
    data, which required a manual user-supplied export). Kaua'i's ADU
    scenario keeps using the generic county benchmark. This asymmetry is
    disclosed deliberately rather than smoothed over by forcing Honolulu
    back onto the generic number for consistency's sake -- see
    docs/DATA_PROVENANCE.md's "Permit-data quality varies by county"
    entry. The real fix, if it ever becomes available, is a Kaua'i permit
    dataset of comparable quality, not discarding Honolulu's.
    """
    permits_path = RAW_DIR / "honolulu_adu_permits.json"
    if not permits_path.exists():
        raise FileNotFoundError(
            f"{permits_path} not found -- run `fetch_data.py --layer honolulu_adu_permits` first."
        )
    permits = pd.read_json(permits_path)
    issued = permits[permits["issuedate"].notna()].copy()
    issued["issue_year"] = pd.to_datetime(issued["issuedate"]).dt.year

    complete_years = list(HONOLULU_ADU_PERMITS_COMPLETE_YEARS)
    by_year = issued[issued["issue_year"].isin(complete_years)].groupby("issue_year").size()
    avg_annual_real_adu_permits = by_year.reindex(complete_years, fill_value=0).mean()

    # absorption_rate=0 is a throwaway value here -- this call only needs
    # eligible_parcel_count (purely a zoning/density computation,
    # independent of any absorption rate). Passing an explicit override
    # also sidesteps run_adu_scenario()'s own default-rate lookup, which
    # (once wired up below) calls back into this function -- 0 breaks
    # that recursion rather than relying on call-order luck.
    adu_tract, _ = run_adu_scenario("honolulu", ScenarioParams(absorption_rate=0))
    eligible_parcel_count = int(adu_tract["eligible_parcel_count"].sum())

    decade_equivalent_pct = 100.0 * 10.0 * avg_annual_real_adu_permits / eligible_parcel_count

    return {
        "avg_annual_real_adu_permits": round(float(avg_annual_real_adu_permits), 1),
        "complete_years": f"{complete_years[0]}-{complete_years[-1]}",
        "eligible_parcel_count": eligible_parcel_count,
        "decade_equivalent_pct": round(decade_equivalent_pct, 2),
        "absorption_rate": decade_equivalent_pct / 100.0,
        "generic_benchmark_pct_for_comparison": round(absorption_rate_benchmark_for("honolulu") * 100.0, 1),
        "source": "data.honolulu.gov Socrata building permits (4vab-c87q), ohana='Y' OR accessorydwellingunitadu='Y'",
    }


def hawaii_county_historical_ohana_permit_summary() -> dict:
    """
    Real, record-level historical Ohana Dwelling permit data for Hawai'i
    County (data.hawaiicounty.gov/gis "Ohana Permits" FeatureServer,
    layer 16, service id 507149e846904456bd114bb136688c07 -- confirmed
    public, fetched directly 2026-10-26, 4,057 records, user-supplied
    link). Unlike honolulu_real_adu_absorption_rate() above, this
    function DELIBERATELY DOES NOT return an absorption_rate for
    run_adu_scenario() to use -- the data is real, but using it to
    calibrate a FUTURE rate would be wrong, for a specific, checkable
    reason laid out below, not a data-quality excuse.

    This dataset tracks permit applications under the OLD "Ohana
    Dwelling" permit process (Hawai'i County Code former section
    25-6-30 through 25-6-39.7, repealed by Ordinance 24-70 / Bill 123
    Draft 5, effective October 7, 2024 -- see COUNTY_CONFIG["hawaii"]'s
    adu_eligible_districts comment for the primary-source ordinance
    citation). That old process was its own separate permit application
    track with real administrative friction the new ordinance explicitly
    removed: a $25 filing fee, a notice-to-neighbors requirement, only
    one active Ohana permit application per applicant at a time, a
    10,000 sqft minimum building-site area, and a cap of exactly one
    Ohana dwelling per site (Section 25-6-33, now repealed).

    The annual permit-application count collapsed from the hundreds per
    year in the late 1980s/early 1990s (peak: 555 in 1989) to single
    digits per year for every year from 1996 through 2023 (the dataset's
    last complete year) -- e.g. 8, 7, 5, 7, 6, 4, 2 for 2017-2023. This
    is NOT evidence that real demand for ADUs in Hawai'i County is
    negligible; it's much more consistent with the OLD permit process
    itself becoming a rarely-used administrative dead end (the same kind
    of friction the new ordinance's own legislative history -- see the
    primary ordinance text -- was written to fix: removing the lot-size
    minimum, the one-active-application rule, and the separate
    application track entirely). Computing a "recent-years average" rate
    from this data the same way honolulu_real_adu_absorption_rate() does
    from Honolulu's data would be calibrating against a process this
    same county just abolished for being too restrictive -- not a
    defensible stand-in for how the NEW ADU rules will perform, and
    almost certainly a severe understatement if used that way.

    Returned for disclosure/documentation purposes only (see
    docs/DATA_PROVENANCE.md) -- run_adu_scenario("hawaii") continues to
    use the generic county benchmark, same as before this data was
    found.
    """
    permits_path = RAW_DIR / "hawaii_county_ohana_permits.json"
    if not permits_path.exists():
        raise FileNotFoundError(
            f"{permits_path} not found -- fetch via the Ohana Permits FeatureServer "
            "(services1.arcgis.com/C2LPusZs5OXNGFDn/arcgis/rest/services/ohanapermit/FeatureServer/16) first."
        )
    permits = pd.read_json(permits_path)
    permits["permit_date"] = pd.to_datetime(permits["DATE_"], format="%m/%d/%Y", errors="coerce")
    permits = permits[permits["permit_date"].notna()].copy()
    permits["year"] = permits["permit_date"].dt.year
    # One record (OHD_NO 86-0094) has a plainly erroneous source date of
    # "09/23/0986" -- almost certainly a typo for 1986 given its own
    # permit number prefix, but reported as-is in the raw data rather than
    # silently corrected. Excluded here only from the date_range/min-year
    # display below so a single data-entry error doesn't make the whole
    # dataset look like it starts in the year 986; it's not in any of the
    # 2014-2023 years used for the actual average, so it has zero effect
    # on any computed figure.
    display_years = permits[permits["year"] >= 1970]
    approved = permits[permits["STATUS"].str.strip().str.lower() == "approved"]

    pre_regime_change_years = range(2014, 2024)  # last 10 complete years before Ord. 24-70 (Oct 2024)
    by_year = approved[approved["year"].isin(pre_regime_change_years)].groupby("year").size()
    recent_avg_annual_approved = by_year.reindex(pre_regime_change_years, fill_value=0).mean()

    peak_year_row = approved.groupby("year").size().idxmax(), int(approved.groupby("year").size().max())

    return {
        "total_records": len(permits),
        "total_approved": int(len(approved)),
        "date_range": f"{int(display_years['year'].min())}-{int(display_years['year'].max())}",
        "peak_year": {"year": int(peak_year_row[0]), "approved_permits": peak_year_row[1]},
        "recent_avg_annual_approved_2014_2023": round(float(recent_avg_annual_approved), 1),
        "recent_avg_as_decade_equivalent_pct_for_reference_only": round(
            100.0 * 10.0 * recent_avg_annual_approved
            / int(run_adu_scenario("hawaii", ScenarioParams(absorption_rate=0))[0]["eligible_parcel_count"].sum()),
            3,
        ),
        "used_for_scenario_calibration": False,
        "why_not_used": (
            "This dataset tracks the OLD Ohana Dwelling permit process (repealed by Ordinance "
            "24-70 / Bill 123 Draft 5, effective 2024-10-07), which had real administrative "
            "friction the new ADU ordinance removed (a separate application track, $25 fee, "
            "one-active-application rule, 10,000 sqft minimum lot). Annual permit volume "
            "collapsed from the hundreds/year in the late 1980s to single digits/year from 1996 "
            "onward -- almost certainly reflecting that process becoming a rarely-used "
            "administrative dead end, not real ADU demand. Using its recent-years rate to "
            "calibrate the NEW ordinance's future absorption rate would be calibrating against "
            "a process this county just abolished for being too restrictive. Hawai'i County's "
            "ADU scenario continues to use the generic county benchmark instead."
        ),
        "source": (
            "Hawai'i County GIS 'Ohana Permits' FeatureServer (services1.arcgis.com/"
            "C2LPusZs5OXNGFDn/arcgis/rest/services/ohanapermit/FeatureServer/16), "
            "confirmed public, fetched 2026-10-26"
        ),
    }


def maui_real_adu_absorption_rate() -> dict:
    """
    A REAL, scenario-specific absorption rate for Maui's ADU scenario,
    calibrated against actual issued building permits -- the same
    methodology as honolulu_real_adu_absorption_rate() (avg_annual_real_
    ADU_permits / CURRENT_ADU-eligible_parcel_count, scaled to the
    10-year decade-equivalent convention), applied to a different source:
    Maui County's monthly "Building Permits Issued" DSA reports, parsed
    by scripts/process_maui_permit_reports.py into
    data/processed/maui_permits_parsed.csv (see that script's and
    docs/DATA_PROVENANCE.md's docstrings for the source, the parser's
    cross-checks against each report's own printed totals, and known
    caveats).

    THIS DIFFERS FROM HONOLULU'S CALIBRATION IN TWO WAYS, one of them now
    largely closed, making this a still-slightly-weaker but much
    stronger-than-before estimate relative to Honolulu's:

    1. Spans most of three calendar years, not several complete ones. As
       of 2026-10-27, 30 months are in hand: 10 of 12 months of 2024
       (July and November excluded -- both source PDFs have NO
       ToUnicode CMap at all, so pdftotext extracts nothing and no
       permit-level records exist for either month; see
       process_maui_permit_reports.py's MONTHLY_GROUND_TRUTH comments),
       all of 2025 except February (that month's source PDF has a
       DIFFERENT failure mode, a corrupt-but-present CMap, also
       unrecoverable; re-pulled 2026-10-27 and confirmed to be the same
       underlying export defect), plus January-September 2026. This
       averages permits/month across 30 months spanning 3 different
       years (versus the original 9-month, single-year basis), which
       meaningfully smooths out single-month and single-year anomalies
       -- e.g. the active post-Lahaina-fire ADU rebuild wave that shows
       up in 2026 is now balanced against two mostly-pre-rebuild-wave
       years (2024 and most of 2025). It is still extrapolation (avg
       monthly rate x 12) rather than an average of complete observed
       annual totals like Honolulu's calibration, since none of 2024
       (missing 2 months), 2025 (missing 1 month), or 2026 (missing
       October-December, still in progress) is itself a complete year.
    2. Free-text identification, not a permit-type field. Maui's report
       has no dedicated ADU/Ohana permit-type column (unlike Honolulu's
       ohana/accessorydwellingunitadu fields or Hawai'i County's
       dedicated Ohana Permits layer) -- a permit counts as ADU-related
       here only if its free-text Name/Description contains "ACCESSORY
       DWELLING", "ADU", or "OHANA" (is_likely_adu in
       process_maui_permit_reports.py). This is a conservative lower
       bound: a real ADU permit described in other words (e.g. "2nd
       farm dwelling," "main dwelling w/ att garage" where the "main"
       unit is actually the second unit on the parcel) would be missed.
       So this function's rate likely UNDERSTATES true ADU permitting
       activity, in contrast to Honolulu's rate, which comes from an
       authoritative permit-type field.
    """
    permits_path = OUT_DIR / "maui_permits_parsed.csv"
    if not permits_path.exists():
        raise FileNotFoundError(
            f"{permits_path} not found -- run `process_maui_permit_reports.py` first "
            "(needs report files in data/raw/permit_reports/maui/)."
        )
    permits = pd.read_csv(permits_path)
    adu_permits = permits[permits["is_likely_adu"].astype(bool)]

    complete_months = sorted(permits["month"].unique())
    avg_monthly_real_adu_permits = len(adu_permits) / len(complete_months)
    avg_annual_real_adu_permits = avg_monthly_real_adu_permits * 12.0

    adu_tract, _ = run_adu_scenario("maui", ScenarioParams(absorption_rate=0))
    eligible_parcel_count = int(adu_tract["eligible_parcel_count"].sum())

    decade_equivalent_pct = 100.0 * 10.0 * avg_annual_real_adu_permits / eligible_parcel_count

    return {
        "months_of_data": complete_months,
        "total_adu_permits_observed": int(len(adu_permits)),
        "avg_monthly_real_adu_permits": round(avg_monthly_real_adu_permits, 2),
        "avg_annual_real_adu_permits_extrapolated": round(avg_annual_real_adu_permits, 1),
        "eligible_parcel_count": eligible_parcel_count,
        "decade_equivalent_pct": round(decade_equivalent_pct, 2),
        "absorption_rate": decade_equivalent_pct / 100.0,
        "generic_benchmark_pct_for_comparison": round(absorption_rate_benchmark_for("maui") * 100.0, 1),
        "caveats": (
            "Extrapolated (avg monthly rate x 12) from 30 months spanning 3 calendar years "
            "(10 of 12 months of 2024, all of 2025 except February, plus January-September "
            "2026 -- the missing months are excluded due to unrecoverable broken source-PDF "
            "text layers, not estimated), not averaged complete-year totals like Honolulu's "
            "equivalent calibration; and is_likely_adu is a free-text keyword match on the "
            "permit description, not an authoritative permit-type field, so the true rate is "
            "likely higher than this. 2026 includes active post-Lahaina-fire ADU rebuild "
            "permitting, now balanced against two mostly pre-rebuild-wave years (2024 and most "
            "of 2025) in this average."
        ),
        "source": (
            "Maui County DSA 'Building Permits Issued' monthly reports "
            "(mauicounty.gov/DocumentCenter/Index/7188), user-supplied, parsed by "
            "process_maui_permit_reports.py"
        ),
    }


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

    # Hawaii County's raw parcel layer happens to carry its own field also
    # named "zone" (a TMK administrative zone number, e.g. "8" -- NOT a
    # land-use zoning code; confirmed 2026-10-24 by inspecting the raw
    # layer directly). Left un-dropped, this collides on name with the
    # zoning layer's own cfg["zoning_field"] ("zone") in the merge below:
    # pandas' suffixes=("", "_zone") keeps the LEFT (parcel layer's own,
    # wrong) column under the bare name and renames the right (correct,
    # zoning-layer) one to "zone_zone" instead -- silently feeding the
    # wrong field into every zoning-dependent computation downstream. Drop
    # any parcel-layer column that shares cfg["zoning_field"]'s name
    # (other than the join key itself) before merging, so the zoning
    # layer's value always wins -- a no-op for every other county, where
    # no such collision exists.
    if cfg["zoning_field"] in parcels.columns and cfg["zoning_field"] != pid:
        parcels = parcels.drop(columns=[cfg["zoning_field"]])

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
    # Kauai-only (see use_rdistrict_density_rule). Disabled per-county
    # because some counties' district labels also happen to match the
    # "R-<number>" pattern (Honolulu's R-5, R-3.5, ...) without that
    # number meaning units/acre there -- applying Kauai's parser anyway
    # would silently fabricate a density rule that doesn't exist in that
    # county's actual code.
    if cfg.get("use_rdistrict_density_rule", True):
        parcels["density_per_acre"] = parcels[cfg["zoning_field"]].apply(max_density_units_per_acre)
        parcels["max_units_by_density"] = parcels.apply(
            lambda r: max_units_for_parcel(r["parcel_area_sqft"], r["density_per_acre"])
            if pd.notna(r["density_per_acre"])
            else pd.NA,
            axis=1,
        )
    else:
        parcels["density_per_acre"] = pd.NA
        parcels["max_units_by_density"] = pd.NA

    existing_units_field = cfg.get("existing_units_field")
    if existing_units_field:
        # Real per-parcel unit count (e.g. Honolulu's LEGDAT row count via
        # augment_oahu_parcels.py), not an assumed flat 1.
        parcels["existing_units_estimate"] = parcels[existing_units_field].fillna(0).astype(int)
    else:
        # Approximation: no direct existing-unit-count field exists on
        # this county's parcel layer, so this assumes one existing
        # dwelling per already-residential parcel. Will overcount
        # available capacity on any parcel that's actually already
        # multi-unit (e.g. an existing duplex) -- a real limitation, not
        # yet solved for counties without their own unit-count source.
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
        min_lot_sqft: float | None = None,  # None -> county's own confirmed default
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

    # Honolulu gets a real, ADU-specific absorption rate (calibrated against
    # its own ADU/Ohana permit history -- see honolulu_real_adu_absorption_
    # rate()'s docstring) instead of the generic all-housing county
    # benchmark every other county/scenario combination uses. This is a
    # deliberate asymmetry, not an oversight -- Kaua'i has no equivalent
    # permit-level dataset, so it keeps the generic benchmark. The 0 passed
    # to the lookup call inside that function prevents infinite recursion;
    # don't remove it.
    absorption_rate_source = "county_generic_benchmark"
    if params.absorption_rate is not None:
        absorption_rate = params.absorption_rate
    elif county == "honolulu" and (RAW_DIR / "honolulu_adu_permits.json").exists():
        absorption_rate = honolulu_real_adu_absorption_rate()["absorption_rate"]
        absorption_rate_source = "honolulu_real_adu_permits"
    elif county == "maui" and (OUT_DIR / "maui_permits_parsed.csv").exists():
        # Weaker basis than Honolulu's (30 months spanning 3 calendar years,
        # free-text ADU identification -- see maui_real_adu_absorption_rate()'s docstring)
        # but real, and a meaningfully better-grounded number than the
        # generic all-housing county benchmark for this one scenario.
        absorption_rate = maui_real_adu_absorption_rate()["absorption_rate"]
        absorption_rate_source = "maui_real_adu_permits"
    else:
        absorption_rate = absorption_rate_benchmark_for(county)

    cfg = COUNTY_CONFIG[county]
    # Each county's own confirmed default, not one figure applied
    # everywhere: Kauai's 7,500 sqft is a common-threshold placeholder
    # (see DEFAULT_ADU_MIN_LOT_SQFT), Honolulu's 0 is a CONFIRMED "no
    # minimum stated" reading of ROH 21-5.50-3(a) -- using Kauai's number
    # for Honolulu would fabricate a restriction that isn't in that code.
    min_lot_sqft = (
        params.min_lot_sqft
        if params.min_lot_sqft is not None
        else cfg.get("adu_min_lot_sqft", DEFAULT_ADU_MIN_LOT_SQFT)
    )
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
    flat_lot_ok = ~has_density_rule & (parcel_table["parcel_area_sqft"] >= min_lot_sqft)

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
        "min_lot_sqft": min_lot_sqft,
        "absorption_rate": absorption_rate,
        "absorption_rate_was_county_benchmark": params.absorption_rate is None,
        "absorption_rate_source": absorption_rate_source,
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
    BASELINE capacity under current code: how many units a parcel could
    already legally hold today, before any Missing Middle reform.

    Kauai: R-districts only, via the confirmed density-per-acre rule
    (§8-4.2(b)/§8-4.6), uncapped at 6 -- some larger R-20 lots legally
    allow more than 6 units today, and that is NOT a Missing Middle
    reform, it's current law. Per-parcel baseline_capacity =
    max_units_by_density - existing_units_estimate, floored at 0.

    Honolulu: has no sourced units-per-acre rule for its R-districts (see
    use_rdistrict_density_rule=False in COUNTY_CONFIG), so this uses a
    different, real zoning-eligibility fact instead of a density figure:
    adu_eligible_districts (ROH Table 21-5.1-confirmed, 2026-10-15) is
    exactly Honolulu's set of single-family-zoned districts (Country,
    R-20, R-10, R-7.5, R-5, R-3.5) -- the same confirmed label already
    used to scope ADU eligibility. A parcel in one of those districts is
    treated as baseline_capacity == 0 (today's zoning holds it to what's
    already built, no legal headroom beyond the existing unit count) --
    everywhere else (A-1+, B, I, AG, etc.) is pd.NA, same "doesn't apply"
    treatment Kauai gives non-R zones. This is a coarser baseline than
    Kauai's -- it rests on the confirmed single-family ZONING LABEL, not
    on a re-verified read of Table 21-5.1's Duplex-/Two-Unit-Dwelling
    permission rows (which may allow more than 1 unit by right in some of
    these districts) -- stated here as a real simplification, not
    smoothed over, because that finer distinction hasn't been
    re-confirmed against the actual table since the user supplied it.
    """
    parcel_path = OUT_DIR / f"{county}_parcel_level.csv"
    if not parcel_path.exists():
        raise FileNotFoundError(f"Run build_county('{county}') first -- {parcel_path} not found")
    parcel_table = pd.read_csv(parcel_path)
    cfg = COUNTY_CONFIG[county]

    if cfg.get("use_rdistrict_density_rule", True):
        has_density_rule = parcel_table["density_per_acre"].notna()
        parcel_table["baseline_capacity"] = (
            parcel_table["max_units_by_density"] - parcel_table["existing_units_estimate"]
        ).clip(lower=0)
        parcel_table.loc[~has_density_rule, "baseline_capacity"] = pd.NA
    else:
        single_family_districts = cfg.get("adu_eligible_districts", set())
        # build_county() normalizes every county's own zoning column name
        # (cfg["zoning_field"]) to the single output column "zoning" --
        # read that normalized name, not the raw per-county field key.
        in_single_family_zone = parcel_table["zoning"].isin(single_family_districts)
        parcel_table["baseline_capacity"] = pd.NA
        parcel_table.loc[in_single_family_zone, "baseline_capacity"] = 0
    return parcel_table


def run_missing_middle_baseline_tract(county: str) -> pd.DataFrame:
    """
    Rolls run_missing_middle_baseline()'s parcel-level `baseline_capacity`
    up to the tract level, added 2026-10-16 per the user's direct
    instruction: "we need to develop both the baseline scenario and the
    scenarios with the zoning interventions ... we haven't developed the
    baseline scenarios based on existing zoned capacity."

    This number existed in the model since the Missing Middle buildable-
    area work (it's what run_missing_middle_scenario() uses to decide
    which parcels are even eligible for the reform), but it was never
    itself rolled up and reported -- only used internally as a == 0
    filter. The actual MAGNITUDE of baseline_capacity on parcels where
    it's positive (current zoning already permits more units than exist
    today, with no reform needed) was being computed and then discarded.
    This function is what lets the dashboard report "baseline: what
    today's zoning already allows" as its own real number, alongside
    "reform: what Missing Middle adds beyond that" -- the two halves of
    the baseline/policy-intervention split this project has applied
    everywhere else, now applied to the zoning-capacity side of Missing
    Middle too, not just the feasibility/affordability gates.

    Returns one row per tract: `baseline_capacity_gross` (sum of
    parcel-level baseline_capacity, NA treated as 0 -- a parcel outside
    the density-rule/single-family-district scope simply contributes
    zero headroom, not a missing value upstream) and
    `baseline_capacity_estimated_units` (scaled by the same absorption
    rate every other scenario uses, since this is still capacity that
    needs to actually get built, not a stock that materializes at once).
    """
    parcel_table = run_missing_middle_baseline(county)
    cfg = COUNTY_CONFIG[county]
    parcel_table["baseline_capacity"] = parcel_table["baseline_capacity"].fillna(0)

    tract_rollup = (
        parcel_table.groupby("GEOID", observed=True)
        .agg(
            baseline_eligible_parcel_count=(cfg["parcel_id_field"], lambda s: (parcel_table.loc[s.index, "baseline_capacity"] > 0).sum()),
            baseline_capacity_gross=("baseline_capacity", "sum"),
        )
        .reset_index()
    )
    absorption_rate = absorption_rate_benchmark_for(county)
    tract_rollup["baseline_capacity_estimated_units"] = (
        tract_rollup["baseline_capacity_gross"] * absorption_rate
    ).round(1)
    return tract_rollup


def existing_zoned_capacity_tract(county: str) -> pd.DataFrame:
    """
    Existing Conditions, reframed 2026-10-22 per direct instruction
    ("existing conditions should be the actual existing conditions based
    on the current zoned capacity") away from market data (rents, sales,
    income -- now on the Assumptions tab as evidentiary grounding
    instead) and onto what a reform-free reading of this project's own
    pipeline already computes: how many units are already built on
    residential parcels today, versus how many current zoning (with NO
    reform) already legally allows on top of that.

    Reuses run_missing_middle_baseline()'s parcel-level fields rather
    than recomputing anything -- this is a rollup, not a new model. One
    row per tract:
      - residential_parcel_count: parcels this model treats as already
        holding a residential unit (has_existing_residential_unit).
      - existing_units_built: sum of existing_units_estimate -- the
        model's stated 1-unit-per-residential-parcel simplification (no
        county publishes a direct per-parcel unit count), NOT a measured
        census of actual units.
      - zoned_capacity_known_parcel_count: parcels where this model has
        an actual basis for a zoned-capacity number (Kauai: a confirmed
        density-per-acre rule exists; Honolulu: the parcel's zoning
        label is confirmed single-family, so capacity is known to equal
        what's built -- see run_missing_middle_baseline()'s docstring
        for exactly why Honolulu's figure is coarser than Kauai's).
      - zoned_capacity_total: existing_units_built + mm_headroom + adu_headroom,
        i.e. what's built now plus whatever legal headroom today's
        zoning (not a reform) already allows -- 0 additional headroom
        for a tract with no confirmed density rule, a real stated data
        gap, not an assumption that no headroom exists.

    ADDED 2026-10-22, per direct follow-up ("have you also considered
    existing ADU/Ohana home permissions"): Act 39/SB3202's statewide
    2-ADU-per-lot mandate is ALREADY ENACTED LAW, not a reform --
    run_adu_scenario()'s own docstring says its default-params call "IS
    the Challenge's required BASELINE." That capacity was being computed
    (every ADU scenario run) but never folded into THIS function's
    zoned-capacity total, which only had Missing Middle's headroom. Now
    adu_headroom (one eligible parcel = one additional unit under the
    mandate, not scaled by absorption -- this is gross legal capacity,
    the same convention mm_headroom already uses) is added in too.
    Deliberately NOT included here: Honolulu's TOD FAR overlay
    (HB1409/Act 159), also enacted law, but its eligibility footprint is
    the model's own documented "honest, unresolved finding" (implausibly
    large -- a distance buffer standing in for real, narrower adopted
    TOD district boundaries this project doesn't have yet). Folding an
    unresolved number into this tab's headline total would present it
    with more confidence than the model itself has in it -- see
    honolulu_tod_overlay_capacity_tract() below, surfaced separately and
    flagged, not summed in here.
    """
    baseline_tract = run_missing_middle_baseline_tract(county)
    adu_tract, _ = run_adu_scenario(county, ScenarioParams())  # defaults == current-law baseline
    parcel_table = run_missing_middle_baseline(county)
    tract_rollup = (
        parcel_table.groupby("GEOID", observed=True)
        .agg(
            total_parcel_count=("GEOID", "size"),
            residential_parcel_count=("has_existing_residential", "sum"),
            existing_units_built=("existing_units_estimate", "sum"),
            zoned_capacity_known_parcel_count=("baseline_capacity", lambda s: s.notna().sum()),
        )
        .reset_index()
    )
    tract_rollup = tract_rollup.merge(
        baseline_tract[["GEOID", "baseline_eligible_parcel_count", "baseline_capacity_gross"]]
        .rename(columns={"baseline_capacity_gross": "mm_headroom"}),
        on="GEOID", how="left",
    )
    tract_rollup = tract_rollup.merge(
        adu_tract[["GEOID", "eligible_parcel_count"]].rename(columns={"eligible_parcel_count": "adu_headroom"}),
        on="GEOID", how="left",
    )
    tract_rollup["mm_headroom"] = tract_rollup["mm_headroom"].fillna(0)
    tract_rollup["adu_headroom"] = tract_rollup["adu_headroom"].fillna(0)
    tract_rollup["baseline_capacity_gross"] = tract_rollup["mm_headroom"] + tract_rollup["adu_headroom"]
    tract_rollup["zoned_capacity_total"] = (
        tract_rollup["existing_units_built"] + tract_rollup["baseline_capacity_gross"]
    )
    return tract_rollup


# Honolulu's REAL, adopted TOD Neighborhood Plan rezoning (City Council
# ordinances reclassifying parcels near rail stations into TOD-specific
# base zoning districts) -- sourced 2026-10-22 from Honolulu's own hosted
# "TOD_Special_District_and_Zone_Change" feature service (layer 11, "TOD
# Zoning - Adopted", 186 polygons, see fetch_data.py's
# TOD_ZONE_CHANGE_BASE_URL / tod_zoning_adopted_honolulu.json), found via
# the public "TOD Special District and Zone Change" ArcGIS Hub item. This
# REPLACES the old distance-buffer eligibility estimate: a parcel is now
# TOD-overlay-eligible only if it falls inside one of these real, enacted
# rezoning polygons, not merely within a given radius of a station.
#
# FAR values are the Revised Ordinances of Honolulu's own Land Use
# Ordinance, Chapter 21, Article 3 -- directly confirmed 2026-10-22
# against the rendered tables (not just extracted text, which first came
# back misaligned and was corrected against a screenshot of the actual
# table):
#   Table 21-3.3 "Apartment and Apartment Mixed-Use Zoning District
#   Development Standards" (ROH 21-3.90-1) -- AMX districts' NON-COMMERCIAL
#   FAR row (the residential-density allowance; each AMX code also permits
#   additional COMMERCIAL floor area under 21-3.90-1(c), which is
#   deliberately excluded here since that's retail/office area, not
#   housing capacity):
#     AMX-1: 2.0  AMX-2: 3.0  AMX-3: 4.0
#   Table 21-3.4 "Resort, Business and Business Mixed-Use Zoning District
#   Development Standards" (ROH 21-3.120-2) -- these districts set ONE
#   combined max-density FAR (no non-commercial/commercial split), with a
#   higher cap "Max FAR" available only via the open space bonus
#   (21-3.120-2(c)); this project uses the BASE (non-bonus) figure, the
#   same conservative convention already used for the resort/open-space
#   bonus the project doesn't model elsewhere:
#     BMX-3: 2.5 (bonus up to 3.5)  BMX-4: 4.0 (bonus up to 7.5)
#   IMX-1 (industrial-commercial mixed use, ROH 21-3.140-1(c)(1), per
#   Ord. 17-55): FAR scales 1.5/2.0/2.5 with the required minimum
#   non-commercial floor-area share -- this project uses the LOWEST
#   (1.5) tier, the conservative floor that requires no minimum
#   non-commercial share, rather than assuming every IMX-1 parcel hits
#   the higher tiers' residential-share requirements.
# Only these TOD-specific codes get a FAR here -- a polygon whose
# to_zoning is a generic, non-TOD-specific code (R-5, A-2, B-1, B-2, P-2,
# AG-1/2, I-2, F-1, etc. -- meaning that parcel's TOD-area rezoning didn't
# actually reclassify it into a denser TOD mixed-use district) gets none,
# since no TOD-specific density bonus applies to it.
TOD_ADOPTED_ZONE_FAR = {
    "AMX-1": 2.0,
    "AMX-2": 3.0,
    "AMX-3": 4.0,
    "BMX-3": 2.5,
    "BMX-4": 4.0,
    "IMX-1": 1.5,
}


def honolulu_tod_adopted_zoning_parcels() -> pd.DataFrame:
    """
    Centroid-joins Honolulu parcels to the real, adopted TOD rezoning
    polygons (see TOD_ADOPTED_ZONE_FAR above) to find which parcels were
    actually reclassified into a TOD-specific zoning district, and what
    that district's real zoned FAR is. Returns one row per matched
    parcel: tmk, to_zoning, tod_far, station, neighborhood_area, ord_num.
    Parcels not falling inside any adopted-rezoning polygon, or whose
    to_zoning isn't one of the TOD-specific codes in TOD_ADOPTED_ZONE_FAR,
    are simply absent from the result (not TOD-overlay-eligible).
    """
    cfg = COUNTY_CONFIG["honolulu"]
    pid = cfg["parcel_id_field"]
    parcels = _load_geojson(RAW_DIR / cfg["parcels_file"])
    tod_zones = _load_geojson(RAW_DIR / "tod_zoning_adopted_honolulu.json")

    parcels = parcels.to_crs(PROJECTED_CRS)
    tod_zones = tod_zones.to_crs(PROJECTED_CRS)

    centroids = parcels[[pid, "geometry"]].copy()
    centroids["geometry"] = centroids.geometry.centroid

    hit = _centroid_join(
        centroids, tod_zones, ["to_zoning", "station", "neighborhood_area", "ord_num"]
    )
    hit["tod_far"] = hit["to_zoning"].map(TOD_ADOPTED_ZONE_FAR)
    return hit[hit["tod_far"].notna()].copy()


def honolulu_tod_overlay_capacity_tract() -> pd.DataFrame:
    """
    Honolulu's TOD overlay capacity, REBUILT 2026-10-22 on real, adopted
    rezoning data rather than the prior distance-buffer estimate. Direct
    follow-up to a question of what it would take to actually resolve
    (not just flag) the prior buffer-based number -- the user then found
    the real source data (Honolulu's own "TOD Special District and Zone
    Change" ArcGIS Hub item / web app, plus the ROH's actual Chapter 21
    FAR tables via amlegal.com, confirmed against rendered-table
    screenshots) that this function now uses directly.

    What changed from the prior buffer-based version:
      - Eligibility: a real, enacted rezoning polygon the parcel actually
        falls inside (honolulu_tod_adopted_zoning_parcels()), not a
        1/2mi or 1/4mi radius around a station.
      - Density: the TOD zoning code's own ROH-tabled FAR
        (TOD_ADOPTED_ZONE_FAR), not an assumed 6.0/7.0 HB1409 tier.
      - Still follows this project's baseline/intervention principle:
        additional_capacity = max(0, reform_capacity - true_baseline),
        true_baseline = max(existing_units_estimate, max_units_by_density)
        -- same subtraction run_tod_scenario() applies, so this never
        reports a rezoned parcel's full zoned capacity as if it were the
        incremental effect of the rezoning.

    Still surfaced SEPARATELY from existing_zoned_capacity_tract()'s
    headline total, not folded in -- not because of remaining footprint
    uncertainty (that's now resolved: this is the real adopted footprint),
    but because this is Honolulu's 2016-2017 City Council TOD Neighborhood
    Plan rezoning, a distinct enacted mechanism from Act 39's ADU mandate
    already folded into the total, and keeping it as its own flagged line
    item is more legible than quietly merging two different legal
    mechanisms into one number.
    """
    cfg = COUNTY_CONFIG["honolulu"]
    pid = cfg["parcel_id_field"]
    parcel_path = OUT_DIR / "honolulu_parcel_level.csv"
    parcel_table = pd.read_csv(parcel_path)
    parcel_table[pid] = parcel_table[pid].astype(str)

    matched = honolulu_tod_adopted_zoning_parcels()
    matched[pid] = matched[pid].astype(str)

    merged = matched.merge(
        parcel_table[[pid, "GEOID", "parcel_area_sqft", "existing_units_estimate", "max_units_by_density"]],
        on=pid, how="inner",
    )
    reform_capacity = (
        (merged["parcel_area_sqft"] * merged["tod_far"]) / TOD_UNIT_SIZE_GSF
    ).apply(lambda v: math.floor(v) if pd.notna(v) else 0)
    true_baseline = pd.concat(
        [merged["existing_units_estimate"], merged["max_units_by_density"].fillna(0)], axis=1
    ).max(axis=1)
    merged["additional_capacity"] = (reform_capacity - true_baseline).clip(lower=0)

    tract_rollup = (
        merged[merged["additional_capacity"] > 0]
        .groupby("GEOID", observed=True)
        .agg(
            eligible_parcel_count=(pid, "count"),
            tod_overlay_capacity_gross=("additional_capacity", "sum"),
        )
        .reset_index()
    )
    return tract_rollup


def run_missing_middle_scenario(county: str) -> tuple[pd.DataFrame, dict]:
    """
    Missing Middle / Gentle Density INTERVENTION: the reform only applies
    to parcels that are CURRENTLY single-family-restricted in practice --
    i.e. baseline_capacity == 0 (today's zoning on this parcel allows
    exactly the existing unit and no more -- for Kauai that's the density
    math, for Honolulu that's the confirmed single-family district label;
    see run_missing_middle_baseline()). For those parcels, and only those,
    this models upzoning to the Challenge's benchmark ceiling of 6 units.
    A parcel a district already lets build past its current unit count is
    NOT a Missing Middle candidate -- that's already-legal baseline
    capacity, reported separately by run_missing_middle_baseline(), and
    double-counting it here as "reform impact" would overstate what the
    policy actually changes.

    BUILDABLE-AREA CHECK, added 2026-10-15 per direct user correction:
    the legal cap of 6 units used to be the ONLY constraint here --
    every eligible parcel got credited with up to 6 units regardless of
    lot size, with nothing checking whether 6 units' worth of floor area
    actually fits. Now also capped at floor(parcel_area_sqft /
    MULTIFAMILY_RENTAL_UNIT_SIZE_GSF) -- a real, cited rental-apartment
    average (RentCafe/Yardi Matrix, Oahu, 50+-unit buildings, 815 sqft
    blended; see that constant's own comment), not the for-sale townhome
    figure this project used to borrow for every floor-area conversion.
    STATED SIMPLIFICATION: this check is single-story-equivalent --
    parcel_area_sqft / unit_size, with no allowance for the Challenge's
    own "up to 3 stories" headroom, no setback/coverage/parking-footprint
    deduction. It is therefore a CONSERVATIVE floor-area cap, not a
    precise one -- true buildable capacity on a lot big enough for a
    multi-story walk-up is higher than this check allows, stated here
    rather than inventing a stories/coverage multiplier this project has
    no cited source for.

    Not summed with the ADU scenario for the same parcel, for the same
    reason: both draw on the same underlying district allowance. A
    parcel's combined headline number should take the max of the two
    interventions, not their sum; that reconciliation isn't wired in yet.
    """
    parcel_table = run_missing_middle_baseline(county)
    cfg = COUNTY_CONFIG[county]

    currently_single_family_restricted = parcel_table["baseline_capacity"] == 0
    floor_area_capacity_units = (
        parcel_table["parcel_area_sqft"] / MULTIFAMILY_RENTAL_UNIT_SIZE_GSF
    ).apply(math.floor)
    reform_capacity_cap = pd.Series(
        [min(MISSING_MIDDLE_MAX_UNITS, fc) for fc in floor_area_capacity_units], index=parcel_table.index
    )
    reform_capacity = (reform_capacity_cap - parcel_table["existing_units_estimate"]).clip(lower=0)
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
    if county == "kauai":
        applies_to = (
            "Only parcels currently single-family-restricted in practice (baseline_capacity "
            "== 0 under the real density rule) -- a parcel a district's density math already "
            "lets build past its current unit count is baseline capacity, not a Missing Middle "
            "reform effect, and is excluded here to avoid double-counting it as policy impact. "
            "See run_missing_middle_baseline() for that separate number. AG/O/CN/CG parcels are "
            "excluded entirely -- no units/acre figure to compute against."
        )
        known_limitation = (
            "existing_units_estimate assumes 1 unit per already-residential parcel (no direct "
            "unit-count field for Kauai) -- affects both the baseline and the reform-eligibility "
            "threshold here."
        )
    else:
        applies_to = (
            "Only parcels zoned Country/R-20/R-10/R-7.5/R-5/R-3.5 -- Honolulu's confirmed "
            "single-family districts (ROH Table 21-5.1, same set used for ADU eligibility). "
            "A-1+/B/I/AG/etc. parcels are excluded entirely, same treatment as Kauai's non-R "
            "zones. See run_missing_middle_baseline() for the baseline_capacity == 0 logic "
            "this test uses in place of Kauai's density-per-acre math."
        )
        known_limitation = (
            "Baseline rests on the confirmed single-family ZONING LABEL, not a re-verified "
            "read of Table 21-5.1's Duplex-/Two-Unit-Dwelling permission rows -- some of these "
            "districts may already permit more than 1 unit by right, which would overstate this "
            "scenario's eligible population if so. existing_units_estimate uses Honolulu's real "
            "LEGDAT-derived unit count (not a 1-unit assumption), unlike Kauai."
        )
    metadata = {
        "is_baseline": False,
        "absorption_rate": absorption_rate,
        "max_units_cap": MISSING_MIDDLE_MAX_UNITS,
        "unit_size_gsf": MULTIFAMILY_RENTAL_UNIT_SIZE_GSF,
        "buildable_area_check": (
            "Added 2026-10-15: a parcel's reform capacity is now capped at "
            "floor(parcel_area_sqft / 815 sqft) as well as the flat 6-unit legal "
            "ceiling -- whichever is lower. 815 sqft is RentCafe/Yardi Matrix's real "
            "blended average unit size for Oahu apartment buildings with 50+ units "
            "(rentcafe.com/average-rent-market-trends/us/hi/oahu/, pulled 2026-10-15), "
            "not a borrowed for-sale townhome figure. This check is single-story-"
            "equivalent (no credit for the Challenge's own 'up to 3 stories' "
            "allowance), so it is a conservative floor-area cap, not a precise one -- "
            "stated here rather than inventing a stories/coverage multiplier this "
            "project has no cited source for. No comparable 50+-unit survey exists for "
            "Kauai, so the same Oahu-sourced 815 sqft figure is applied to both "
            "counties (see MULTIFAMILY_RENTAL_UNIT_SIZE_GSF's own comment)."
        ),
        "applies_to": applies_to,
        "known_limitation": known_limitation,
    }
    return tract_rollup, metadata


# Transit-Oriented Density -- REBUILT 2026-10-15 on the actual statutory
# eligibility text, per the user's correction: the prior version used two
# different ad hoc mechanisms per county (Kauai: bus-stop-AND-arterial
# buffer at 3/4 mile; Honolulu: rail-only buffer at 3/4 mile) and a
# user-set FAR slider disconnected from real distance. Neither matches
# what HB1409 (2025)/Act 159, HRS 206E-246(j) actually says, and the two
# counties weren't even using the same rule.
#
# WHAT THE STATUTE ACTUALLY SAYS (fetched directly from the bill text and
# HRS 226-63(d), which 206E-246(j) incorporates by reference):
#   - The statute is MODE-AGNOSTIC. It never says "rail" or "bus" --
#     only "public transit systems," "transit hub or transit station."
#   - BASE tier (FAR >= 4.0): applies within a "county-designated TOD
#     area," which 226-63(d) itself defines, absent a county's own
#     designation, as "lands within a one-half-mile radius of a transit
#     hub or transit station" -- ANY mode, ANY stop.
#   - HIGHER tiers (FAR >= 6.0 within 1/2 mile, >= 7.0 within 1/4 mile):
#     apply only near "a station of a LOCALLY PREFERRED ALTERNATIVE for
#     a mass transit project." "Locally preferred alternative" (LPA) is
#     an FTA planning term for a formally adopted, funded capital transit
#     project -- in Hawaii today, that is HART's Skyline rail project and
#     nothing else. A regular TheBus or Kauai Bus stop is a real transit
#     station (so it reaches the BASE tier), but it is not part of an
#     adopted LPA project, so it cannot reach the 6.0/7.0 tiers under a
#     plain reading of the statute.
#
# ONE eligibility mechanism, mode-selectable, not two different ones:
# tod_eligible_parcels(county, mode) buffers around whichever real GTFS-
# derived stop dataset matches `mode` ("bus" or "rail") for that county,
# using the SAME half-mile/quarter-mile radii regardless of mode or
# county. "rail" is only available where an LPA project actually exists
# (Honolulu's Skyline) and reaches the 6.0/7.0 tiers; "bus" is available
# everywhere TheBus/Kauai Bus GTFS data exists and is capped at the 4.0
# base tier, per the LPA restriction above.
HALF_MILE_METERS = 804.672
QUARTER_MILE_METERS = 402.336

TOD_FAR_BASE = 4.0
TOD_FAR_HALF_MILE = 6.0
TOD_FAR_QUARTER_MILE = 7.0

# REPLACED 2026-10-15, per direct user correction: the previous unit-size
# assumption (840 sqft) came from KAUAI_LIHUE_PRO_FORMA_INPUTS's townhome
# FOR-SALE product -- not a comparable reference for TOD or Missing
# Middle, both of which model smaller RENTAL apartment units, not
# townhomes. Replaced with a real, cited multifamily rental average:
#
# RentCafe/Yardi Matrix, Oahu apartment market (rentcafe.com/average-rent
# -market-trends/us/hi/oahu/, pulled 2026-10-15) -- "all Oahu apartment
# buildings with 50 or more units." Real reported average unit sizes:
#   Studio: 366 sqft | 1BR: 590 sqft | 2BR: 843 sqft | 3BR: 1,225 sqft
#   All rentals (blended): 815 sqft
# This project uses the 815 sqft BLENDED average, not one bedroom count,
# since neither TOD nor Missing Middle assumes a specific unit mix.
#
# STATED LIMITATION: Yardi Matrix's methodology covers buildings with 50+
# units, which concentrates its sample in dense Honolulu/Oahu product --
# no comparable large-multifamily survey exists for Kauai (which has few
# or no buildings that size), so this is the best available real figure
# and is applied to BOTH counties rather than inventing a Kauai-specific
# number. This is a real methodological compromise, stated rather than
# hidden, same as this project's practice elsewhere (e.g. the development
# -fee bound using Honolulu DPP figures as the statewide bound).
MULTIFAMILY_RENTAL_UNIT_SIZE_GSF = 815
TOD_UNIT_SIZE_GSF = MULTIFAMILY_RENTAL_UNIT_SIZE_GSF
TOD_DENSITY_SOURCE_NOTE = (
    "FAR-based, per HB1409 (2025)/Act 159, HRS 206E-246(j) and the TOD-area definition it "
    "incorporates from HRS 226-63(d) -- an ENACTED Hawaii statute's 'transit-supportive "
    "density' standard, not a borrowed figure. Mode-agnostic by the statute's own text: FAR "
    "4.0 within 1/2 mile of ANY real transit station (bus or rail); FAR 6.0/7.0 within 1/2 "
    "mile/1/4 mile ONLY of a station on an adopted 'locally preferred alternative' (LPA) "
    "project -- in Hawaii today, that's HART's Skyline rail and nothing else, so 'rail' mode "
    "can reach 6.0/7.0 but 'bus' mode is capped at the 4.0 base tier everywhere. Units = "
    "parcel_area_sqft * FAR / 815 sqft -- RentCafe/Yardi Matrix's real Oahu apartment-rental "
    "average (50+ unit buildings), not a for-sale townhome figure (see "
    "MULTIFAMILY_RENTAL_UNIT_SIZE_GSF above for the bedroom-mix breakdown and stated "
    "limitations)."
)

# Which transit modes exist per county, and whether that mode is an
# adopted LPA project (so it can reach the 6.0/7.0 tiers). Both counties'
# bus data comes from a real GTFS feed; Kauai has no rail at all.
TOD_TRANSIT_MODES_BY_COUNTY = {
    "kauai": {"bus": {"is_lpa": False, "stops_csv": "kauai_bus_major_stops.csv", "stop_col": "mode_implicit_bus"}},
    "honolulu": {
        "bus": {"is_lpa": False, "stops_csv": "honolulu_major_transit_stops.csv", "stop_col": "mode"},
        "rail": {"is_lpa": True, "stops_csv": "honolulu_major_transit_stops.csv", "stop_col": "mode"},
    },
}


# Same mapping as fetch_data.py's COUNTY_FIPS, duplicated rather than
# imported to avoid a cross-module dependency for one small dict.
COUNTY_FIPS = {"honolulu": "003", "hawaii": "001", "kauai": "007", "maui": "009"}
COUNTY_RURAL_LABEL = {
    "kauai": "Rural Kaua'i (unincorporated)",
    "honolulu": "Rural O'ahu (unincorporated)",
    "hawaii": "Rural Hawai'i County (unincorporated)",
    "maui": "Rural Maui County (unincorporated)",
}


def tract_to_place_crosswalk(
    tracts_geojson: Path | None = None, places_geojson: Path | None = None, county: str = "kauai"
) -> pd.DataFrame:
    """
    Real Census tract -> place-name crosswalk, for the dashboard's tract/
    municipality toggle (per the user's 2026-10-15 point: "no one
    associates a place by tract number, but they do associate with the
    names of places"). Hawaii has NO incorporated cities -- the whole
    state is unincorporated outside county government -- so Census
    Designated Places (CDPs: Lihu'e, Kapa'a, Koloa, ...), not
    "incorporated places," are the only real place-name geography that
    exists here (fetch_data.py --layer census_places, TIGERweb layer 5).

    Assigns each tract to whichever CDP covers the LARGEST SHARE of its
    land area (not just whichever CDP contains the tract's centroid,
    which can misassign an irregularly-shaped tract) -- a real spatial
    join, not a hand-typed guess at which town a tract number belongs to.
    A tract with no CDP overlap at all (true rural/unincorporated land
    outside any named place) is labeled "Rural Kaua'i (unincorporated)"
    rather than forced into a nearby town's name it doesn't actually
    cover.
    """
    tracts_path = tracts_geojson or (RAW_DIR / "census_tracts.json")
    places_path = places_geojson or (RAW_DIR / "census_places.json")
    if not tracts_path.exists() or not places_path.exists():
        raise RuntimeError(
            f"Need both {tracts_path} and {places_path} -- run "
            "`fetch_data.py --layer census_tracts` and `--layer census_places` first."
        )
    tracts = gpd.read_file(tracts_path)
    places = gpd.read_file(places_path)
    county_fips_prefix = "15" + COUNTY_FIPS[county]
    tracts = tracts[tracts["GEOID"].astype(str).str.startswith(county_fips_prefix)].copy()
    places["place_name"] = places["NAME"].str.replace(" CDP", "", regex=False)
    rural_label = COUNTY_RURAL_LABEL.get(county, f"Rural {county.title()} (unincorporated)")

    rows = []
    for _, tract in tracts.iterrows():
        tract_geom = tract.geometry
        tract_area = tract_geom.area
        best_name, best_frac = None, 0.0
        for _, place in places.iterrows():
            overlap = tract_geom.intersection(place.geometry).area
            if tract_area > 0 and overlap / tract_area > best_frac:
                best_frac = overlap / tract_area
                best_name = place["place_name"]
        rows.append(
            {
                "GEOID": tract["GEOID"],
                "place_name": best_name if best_frac >= 0.1 else rural_label,
                "place_overlap_frac": round(best_frac, 2),
            }
        )
    return pd.DataFrame(rows)


def tod_eligible_parcels(county: str, mode: str = "bus") -> pd.DataFrame:
    """
    ONE eligibility mechanism for every county/mode combination -- see the
    "Transit-Oriented Density" comment block above for why this replaced
    the two different ad hoc mechanisms this project used to have.

    For each parcel centroid, finds the nearest real transit stop of the
    requested `mode` (GTFS-derived, 2+ weekday routes = "major" for bus;
    every scheduled Skyline stop counts for rail -- see
    fetch_kauai_bus_major_stops()/fetch_honolulu_major_transit_stops() in
    fetch_data.py) and assigns a FAR tier purely from real distance, per
    HRS 206E-246(j)/226-63(d):
      - mode's stops are an adopted LPA project (today, only Honolulu
        "rail" / Skyline): FAR 7.0 within 1/4 mile, FAR 6.0 within 1/2
        mile, not eligible beyond that.
      - mode's stops are NOT an LPA project (every "bus" case): FAR 4.0
        within 1/2 mile (the statute's base TOD-area definition, which
        applies to ANY real transit station regardless of mode), not
        eligible beyond that -- capped here, never reaching 6.0/7.0,
        because those tiers are explicitly gated on LPA status.
    """
    cfg = COUNTY_CONFIG[county]
    modes = TOD_TRANSIT_MODES_BY_COUNTY.get(county, {})
    if mode not in modes:
        available = list(modes) or ["none"]
        raise NotImplementedError(
            f"No '{mode}' transit-stop data for {county} -- available mode(s): {available}."
        )
    mode_cfg = modes[mode]
    stops_path = RAW_DIR / mode_cfg["stops_csv"]
    if not stops_path.exists():
        raise RuntimeError(f"{stops_path} not found -- run `fetch_data.py --layer ...` first.")
    stops = pd.read_csv(stops_path)
    if mode_cfg["stop_col"] == "mode":
        stops = stops[stops["mode"] == mode]
    # "Major" (2+ weekday routes) only filters bus stops -- a rail line's
    # own single-route stations all count equally (see tod_eligible_
    # parcels()'s docstring: no is_major_stop filter for rail).
    if mode != "rail" and "is_major_stop" in stops.columns:
        stops = stops[stops["is_major_stop"].astype(str) == "True"]
    if stops.empty:
        raise RuntimeError(f"No '{mode}' stops found in {stops_path} after filtering.")

    pid = cfg["parcel_id_field"]
    parcels = _load_geojson(RAW_DIR / cfg["parcels_file"]).to_crs(PROJECTED_CRS)
    centroids = parcels[[pid, "geometry"]].copy()
    centroids["geometry"] = centroids.geometry.centroid

    stop_points = gpd.GeoDataFrame(
        stops, geometry=gpd.points_from_xy(stops["stop_lon"], stops["stop_lat"]), crs="EPSG:4326"
    ).to_crs(PROJECTED_CRS)

    nearest = gpd.sjoin_nearest(centroids, stop_points[["geometry"]], distance_col="dist_to_stop_m")
    nearest = nearest.drop_duplicates(subset=pid, keep="first")  # ties -> first nearest only

    if mode_cfg["is_lpa"]:
        far = pd.Series(pd.NA, index=nearest.index, dtype="Float64")
        far = far.mask(nearest["dist_to_stop_m"] <= HALF_MILE_METERS, TOD_FAR_HALF_MILE)
        far = far.mask(nearest["dist_to_stop_m"] <= QUARTER_MILE_METERS, TOD_FAR_QUARTER_MILE)
    else:
        far = pd.Series(pd.NA, index=nearest.index, dtype="Float64")
        far = far.mask(nearest["dist_to_stop_m"] <= HALF_MILE_METERS, TOD_FAR_BASE)

    out = nearest[[pid, "dist_to_stop_m"]].copy()
    out["tod_far"] = far
    out["tod_eligible"] = far.notna()
    return out


def run_tod_scenario(
    county: str = "kauai", mode: str = "bus", unit_size_gsf: float = TOD_UNIT_SIZE_GSF
) -> tuple[pd.DataFrame, dict]:
    """
    Transit-Oriented Density INTERVENTION: for parcels within real
    distance of a `mode` transit stop (tod_eligible_parcels()), models
    the effect of allowing the statute's own per-parcel FAR tier --
    i.e. the capacity unlocked by MORE development rights near transit
    than current zoning allows, which is what the Challenge benchmark
    actually asks for ("model the effect of increasing allowable
    residential density near major transit stops ... through higher
    building heights and/or increased floor area ratios"). Per the same
    baseline/intervention principle used everywhere else in this
    project: additional_estimated_units = max(0, reform_capacity -
    true_baseline), not reform_capacity on its own, so this never
    reports a TOD-eligible parcel's full zoned capacity as if it were
    the reform's incremental effect.

    **Baseline fixed 2026-10-16:** `true_baseline` is
    max(existing_units_estimate, max_units_by_density) -- what's
    actually built OR what today's non-TOD zoning already legally
    allows, whichever is larger -- not existing_units_estimate alone.
    The prior version credited TOD with a parcel's unused CURRENT legal
    headroom (capacity the parcel's base zoning already permits but
    nobody has built yet) as if the TOD reform had unlocked it, which
    overstates the reform the same way reporting a reform's total
    capacity as its incremental impact would. See
    run_missing_middle_baseline_tract() for the equivalent fix applied
    to Missing Middle's baseline reporting.

    `mode` selects which real transit-stop dataset this buffers around
    ("bus": TheBus/Kauai Bus, available in both counties, capped at FAR
    4.0; "rail": Skyline, Honolulu only, reaches FAR 6.0/7.0 -- see
    TOD_TRANSIT_MODES_BY_COUNTY). FAR is no longer a user-set slider
    value disconnected from distance -- it is the real per-parcel tier
    the statute assigns based on how close that parcel actually is to a
    stop of the requested mode.

    Applies to EVERY TOD-eligible parcel regardless of its current
    zoning district -- the statute's "for all uses permitted ... under
    underlying county zoning" wording doesn't carve out non-R zones the
    way this project's own density_per_acre rule does, so this scenario
    (unlike ADU/Missing Middle) does NOT exclude AG/O/CN/CG parcels from
    TOD capacity.

    Parking: the benchmark lets teams "assume that parking requirements
    are reduced or eliminated" for this scenario, so no parking
    constraint is applied to the capacity number here -- but see
    kauai_parking_reform_context() / the Parking Requirement Reduction
    scenario's documented finding on what removing parking actually
    changes in practice (meaningful land-use effect mainly on parcels
    large enough for multi-unit development, modest elsewhere): the same
    realism caveat applies to a TOD parcel actually being built without a
    parking minimum, not just the ADU scenario.
    """
    # Validate the county/mode combination before any file I/O -- a county
    # or mode this project hasn't sourced real transit data for should fail
    # with NotImplementedError regardless of whether build outputs happen
    # to exist yet, not get masked by a FileNotFoundError about a step that
    # was never going to succeed anyway. (tod_eligible_parcels() re-checks
    # this itself, but by then it's too late to pre-empt the file check
    # below.)
    available_modes = TOD_TRANSIT_MODES_BY_COUNTY.get(county, {})
    if mode not in available_modes:
        raise NotImplementedError(
            f"No '{mode}' transit-stop data for {county} -- available mode(s): {list(available_modes) or ['none']}."
        )

    cfg = COUNTY_CONFIG[county]
    pid = cfg["parcel_id_field"]
    parcel_path = OUT_DIR / f"{county}_parcel_level.csv"
    if not parcel_path.exists():
        raise FileNotFoundError(f"Run build_county('{county}') first -- {parcel_path} not found")
    parcel_table = pd.read_csv(parcel_path)

    eligibility = tod_eligible_parcels(county, mode)
    parcel_table[pid] = parcel_table[pid].astype(str)
    eligibility[pid] = eligibility[pid].astype(str)
    parcel_table = parcel_table.merge(eligibility, on=pid, how="left")
    parcel_table["tod_eligible"] = parcel_table["tod_eligible"].fillna(False)

    reform_capacity = (
        (parcel_table["parcel_area_sqft"] * parcel_table["tod_far"].astype("Float64")) / unit_size_gsf
    ).apply(lambda v: math.floor(v) if pd.notna(v) else 0)
    # BASELINE fix, added 2026-10-16 per the user's direct instruction to
    # develop "baseline scenarios based on existing zoned capacity," not
    # just the reform numbers: this used to subtract only
    # existing_units_estimate (what's physically BUILT on the parcel
    # today), which understates the real baseline on any parcel whose
    # CURRENT (non-TOD) zoning already permits more units than are built
    # -- that unused legal headroom is baseline capacity under today's
    # law, not something TOD's FAR bonus unlocks, and crediting it to TOD
    # would overstate the reform's incremental effect exactly the way
    # this project's core principle (baseline vs. policy intervention,
    # never conflated) says not to. The real baseline is now whichever is
    # larger: what's actually built (existing_units_estimate) or what
    # today's zoning already legally allows (max_units_by_density, the
    # same field Missing Middle's baseline uses) -- a parcel gets credit
    # against TOD only for capacity beyond BOTH.
    zoned_baseline = parcel_table["max_units_by_density"].fillna(0)
    true_baseline = pd.concat([parcel_table["existing_units_estimate"], zoned_baseline], axis=1).max(axis=1)
    additional_capacity = (reform_capacity - true_baseline).clip(lower=0)
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

    out_path = OUT_DIR / f"{county}_tod_{mode}_policy_impact_tract.csv"
    tract_rollup.to_csv(out_path, index=False)
    mode_cfg = TOD_TRANSIT_MODES_BY_COUNTY[county][mode]
    metadata = {
        "is_baseline": False,
        "mode": mode,
        "is_lpa_mode": mode_cfg["is_lpa"],
        "major_transit_stop_definition": (
            "Every scheduled Skyline rail stop (GTFS, MobilityDatabase mdb-2350) -- an LPA "
            "project, so it reaches the statute's 6.0/7.0 FAR tiers."
            if mode == "rail" else
            f"{'TheBus' if county == 'honolulu' else 'Kauai Bus'} stop served by 2+ distinct "
            "weekday routes (GTFS, MobilityDatabase) -- not an LPA project, so capped at the "
            "statute's 4.0 base FAR tier regardless of distance."
        ),
        "proximity_tiers": "FAR 7.0 within 1/4 mile + FAR 6.0 within 1/2 mile of an LPA station"
        if mode_cfg["is_lpa"] else "FAR 4.0 within 1/2 mile of a transit station (base TOD-area "
        "tier -- real distance to the nearest major stop, not a buffer-boolean or a user-set "
        "slider).",
        "unit_size_gsf": unit_size_gsf,
        "density_mechanism_note": TOD_DENSITY_SOURCE_NOTE,
        "parking_assumption": "Removed per the benchmark's own allowance for this scenario -- "
        "but see run_parking_reduction_scenario()/kauai_parking_reform_context() for the "
        "documented realism caveat on what removing parking actually changes in practice "
        "(land-use effect concentrated on parcels large enough for multi-unit development).",
        "absorption_rate": absorption_rate,
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
    # Quantified low-end estimate, added 2026-10-16 per direct request
    # ("say how much the model suggests parking demand could be reduced
    # from the baseline"): the two cities the study's own text labels
    # "low density, high car dependency -- minimal effect" are Kauai's
    # closest qualitative match in the sample (no numeric density/commute
    # figures for the comparison cities themselves are sourced in this
    # project, so this stays a range drawn from THEIR own observed
    # outcomes, not a regression against unsourced inputs).
    minimal_effect_cities = {
        city: vals for city, vals in PARKING_STUDY_CITY_BENCHMARKS.items()
        if "minimal effect" in vals[2]
    }
    minimal_effect_reductions = sorted(
        round(100 * (1 - after / before), 1) for before, after, _ in minimal_effect_cities.values()
    )
    return {
        "available": True,
        "countywide_alt_mode_commute_pct": round(countywide_alt_mode_pct, 1),
        "countywide_density_per_sqmi": round(countywide_density, 1),
        "study_city_benchmarks": PARKING_STUDY_CITY_BENCHMARKS,
        "low_end_context": PARKING_STUDY_LOW_END_ALT_MODE_COMMUTE_CONTEXT,
        "low_end_reduction_pct_range": minimal_effect_reductions,
        "low_end_reduction_cities": list(minimal_effect_cities.keys()),
        "verdict": "Kauai's countywide non-car commute share (~5%) and density (~675 people/sq "
        "mi) sit below the study's own 'minimal effect' anchor cities (Grand Rapids, Hartford) "
        "on both axes -- consistent with the Challenge's starting assumption that reducing "
        "parking requirements changes land use, not with a specific predicted reduction ratio. "
        f"Those two cities' OWN observed post-reform parking-supply reduction "
        f"({minimal_effect_reductions[0]}%-{minimal_effect_reductions[1]}%) is the best-supported "
        "quantified estimate for Kauai -- likely at or below this low end, not a Cincinnati/"
        "Champaign/Somerville-scale reduction.",
    }


def honolulu_parking_reform_context(acs_commute_density_csv: Path | None = None) -> dict:
    """
    Honolulu analog of kauai_parking_reform_context(), with a materially
    different, more defensible conclusion: Sohoni & Lee (2025)'s own
    study sample INCLUDES a real Honolulu, HI data point
    (PARKING_STUDY_CITY_BENCHMARKS["Honolulu, HI"]: parking supply fell
    from 88.6% to 75.0% of the former minimum after a TOD-district
    reform) -- unlike Kauai, this isn't extrapolation past the study's
    support, it's the study's own observed outcome for this exact state
    (though a TOD-district reform specifically, not a blanket citywide
    one -- see the caveat in the verdict below). Honolulu's real
    population-weighted density (~12,633/sq mi) and non-car commute share
    (~15.2%) also sit much closer to the study's denser cities than
    Kauai's do, independently supporting that this city belongs in the
    same range the paper studied rather than being a case apart from it.
    """
    path = acs_commute_density_csv or (RAW_DIR / "acs_commute_density_honolulu.csv")
    if not path.exists():
        return {
            "available": False,
            "reason": f"{path} not found -- run `fetch_data.py --layer acs_commute_density "
            "--county honolulu` (requires a free Census API key) first.",
        }
    df = pd.read_csv(path)
    total_commuters = df["commuters_excl_wfh"].sum()
    total_pop = df["population"].sum()
    countywide_alt_mode_pct = (df["pct_alt_mode_commute"] * df["commuters_excl_wfh"]).sum() / total_commuters
    countywide_density = (df["density_per_sqmi"] * df["population"]).sum() / total_pop
    honolulu_benchmark = PARKING_STUDY_CITY_BENCHMARKS["Honolulu, HI"]
    pct_mpr_before, pct_mpr_after, _ = honolulu_benchmark
    implied_reduction_pct = round(100 * (1 - pct_mpr_after / pct_mpr_before), 1)
    return {
        "available": True,
        "countywide_alt_mode_commute_pct": round(countywide_alt_mode_pct, 1),
        "countywide_density_per_sqmi": round(countywide_density, 1),
        "study_city_benchmarks": PARKING_STUDY_CITY_BENCHMARKS,
        "honolulu_study_datapoint": {
            "pct_of_former_mpr_before": pct_mpr_before,
            "pct_of_former_mpr_after": pct_mpr_after,
            "implied_parking_supply_reduction_pct": implied_reduction_pct,
            "context": honolulu_benchmark[2],
        },
        "verdict": f"Unlike Kauai, Honolulu is directly IN this study's sample, not an "
        f"extrapolation past it: Sohoni & Lee's own Oahu TOD-district reform saw post-reform "
        f"parking supply fall to {pct_mpr_after}% of the former minimum from {pct_mpr_before}% "
        f"-- an observed ~{implied_reduction_pct}% reduction in parking supply. Honolulu's "
        f"countywide density (~12,633/sq mi) and non-car commute share (~15.2%) also sit far "
        f"closer to this study's denser cities than Kauai's do. CAVEAT: the study's own "
        f"Honolulu data point was a TOD-DISTRICT reform specifically, not a blanket citywide "
        f"change -- applying this ratio outside TOD-eligible areas is still an extrapolation, "
        f"just a much smaller one than applying it to Kauai at all.",
    }


def hawaii_parking_reform_context(acs_commute_density_csv: Path | None = None) -> dict:
    """
    Hawai'i County analog of kauai_parking_reform_context(). Same
    not-in-sample caveat as Kauai: no Hawai'i County/Big Island data point
    exists in Sohoni & Lee (2025)'s 9-city sample, so this does not apply a
    quantitative post-reform reduction ratio -- it places Hawai'i County on
    the study's own two axes (real ACS data, fetch_data.py --layer
    acs_commute_density --county hawaii) and reports the same low-end range
    (the study's own "minimal effect" cities' observed outcomes) as the
    best-supported estimate, rather than inventing a Hawai'i-specific
    number the study doesn't support.
    """
    path = acs_commute_density_csv or (RAW_DIR / "acs_commute_density_hawaii.csv")
    if not path.exists():
        return {
            "available": False,
            "reason": f"{path} not found -- run `fetch_data.py --layer acs_commute_density "
            "--county hawaii` (requires a free Census API key) first.",
        }
    df = pd.read_csv(path)
    total_commuters = df["commuters_excl_wfh"].sum()
    total_pop = df["population"].sum()
    countywide_alt_mode_pct = (df["pct_alt_mode_commute"] * df["commuters_excl_wfh"]).sum() / total_commuters
    countywide_density = (df["density_per_sqmi"] * df["population"]).sum() / total_pop
    minimal_effect_cities = {
        city: vals for city, vals in PARKING_STUDY_CITY_BENCHMARKS.items()
        if "minimal effect" in vals[2]
    }
    minimal_effect_reductions = sorted(
        round(100 * (1 - after / before), 1) for before, after, _ in minimal_effect_cities.values()
    )
    return {
        "available": True,
        "countywide_alt_mode_commute_pct": round(countywide_alt_mode_pct, 1),
        "countywide_density_per_sqmi": round(countywide_density, 1),
        "study_city_benchmarks": PARKING_STUDY_CITY_BENCHMARKS,
        "low_end_context": PARKING_STUDY_LOW_END_ALT_MODE_COMMUTE_CONTEXT,
        "low_end_reduction_pct_range": minimal_effect_reductions,
        "low_end_reduction_cities": list(minimal_effect_cities.keys()),
        "verdict": f"Hawai'i County's countywide non-car commute share (~{round(countywide_alt_mode_pct, 1)}%) "
        f"and density (~{round(countywide_density, 1)} people/sq mi) sit below the study's own "
        "'minimal effect' anchor cities (Grand Rapids, Hartford) on both axes -- the same "
        "out-of-sample position as Kauai, not Honolulu's in-sample one. Those two cities' OWN "
        f"observed post-reform parking-supply reduction ({minimal_effect_reductions[0]}%-"
        f"{minimal_effect_reductions[1]}%) is the best-supported quantified estimate here too.",
    }


def maui_parking_reform_context(acs_commute_density_csv: Path | None = None) -> dict:
    """
    Maui analog of hawaii_parking_reform_context() -- same out-of-sample
    treatment: no Maui data point in Sohoni & Lee (2025)'s 9-city sample,
    so this reports Maui's real ACS commute/density figures against the
    study's axes and the same literature-derived low-end range, rather
    than a Maui-specific invented ratio.
    """
    path = acs_commute_density_csv or (RAW_DIR / "acs_commute_density_maui.csv")
    if not path.exists():
        return {
            "available": False,
            "reason": f"{path} not found -- run `fetch_data.py --layer acs_commute_density "
            "--county maui` (requires a free Census API key) first.",
        }
    df = pd.read_csv(path)
    total_commuters = df["commuters_excl_wfh"].sum()
    total_pop = df["population"].sum()
    countywide_alt_mode_pct = (df["pct_alt_mode_commute"] * df["commuters_excl_wfh"]).sum() / total_commuters
    countywide_density = (df["density_per_sqmi"] * df["population"]).sum() / total_pop
    minimal_effect_cities = {
        city: vals for city, vals in PARKING_STUDY_CITY_BENCHMARKS.items()
        if "minimal effect" in vals[2]
    }
    minimal_effect_reductions = sorted(
        round(100 * (1 - after / before), 1) for before, after, _ in minimal_effect_cities.values()
    )
    return {
        "available": True,
        "countywide_alt_mode_commute_pct": round(countywide_alt_mode_pct, 1),
        "countywide_density_per_sqmi": round(countywide_density, 1),
        "study_city_benchmarks": PARKING_STUDY_CITY_BENCHMARKS,
        "low_end_context": PARKING_STUDY_LOW_END_ALT_MODE_COMMUTE_CONTEXT,
        "low_end_reduction_pct_range": minimal_effect_reductions,
        "low_end_reduction_cities": list(minimal_effect_cities.keys()),
        "verdict": f"Maui's countywide non-car commute share (~{round(countywide_alt_mode_pct, 1)}%) and "
        f"density (~{round(countywide_density, 1)} people/sq mi) sit below the study's own 'minimal "
        "effect' anchor cities (Grand Rapids, Hartford) on both axes, the same out-of-sample "
        f"position as Kauai/Hawai'i County. Those cities' OWN observed post-reform reduction "
        f"({minimal_effect_reductions[0]}%-{minimal_effect_reductions[1]}%) is the best-supported "
        "quantified estimate here too -- not a Honolulu-scale reduction.",
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

    # Real, county-specific current-code ADU parking requirement, not a
    # single Kauai figure assumed to hold everywhere:
    # - Kauai/Honolulu: 1 space/unit (as before).
    # - Hawai'i County: Zoning Code Sec. 25-4-53 -- confirmed via a real
    #   permit-approval condition (Planning Dept. Ohana Dwelling permit
    #   22-0008, Condition 6(d), gis.hawaiicounty.gov) citing a 2-space
    #   minimum for an ohana/accessory dwelling unit. This is sourced from
    #   one permit's condition referencing the code section, not the
    #   master zoning-code parking table itself, so it is reported as the
    #   best real evidence found rather than a verified blanket minimum --
    #   stated here, not hidden.
    # - Maui: County Code Sec. 19.36B.020 (as amended by Ord. 5983, 2023)
    #   -- an accessory dwelling is explicitly required to provide "the
    #   same minimum number of off-street parking spaces as a single-
    #   family dwelling" under the bedroom-based schedule, which is 1
    #   space for a studio/1-bedroom unit (the ADU size class this model
    #   uses). Confirmed via the ordinance text itself, not assumed.
    parking_spaces_required = {
        "kauai": 1,
        "honolulu": 1,
        "hawaii": 2,
        "maui": 1,
    }.get(county, 1)

    sqft_per_parking_space = 200  # planning-literature placeholder, not county-sourced
    adu_tract = adu_tract.copy()
    adu_tract["parking_land_consumed_sqft_if_required"] = (
        adu_tract["eligible_parcel_count"] * parking_spaces_required * sqft_per_parking_space
    )
    metadata["parking_spaces_required_per_unit_current_code"] = parking_spaces_required
    metadata["sqft_per_parking_space_assumption"] = sqft_per_parking_space
    metadata["note"] = (
        f"Parking does not change eligibility in this county (ADUs require "
        f"{parking_spaces_required} space(s) either way under current code) -- this scenario "
        "reports land consumed by that requirement, not a change in which parcels qualify. "
        "Reducing the requirement to 0 removes this land cost but produces the same "
        "eligible_parcel_count as the ADU scenario above."
    )
    if county == "kauai":
        metadata["post_reform_parking_supply_context"] = kauai_parking_reform_context()
    elif county == "hawaii":
        metadata["post_reform_parking_supply_context"] = hawaii_parking_reform_context()
        metadata["current_code_parking_requirement_source"] = (
            "Hawai'i County Zoning Code Sec. 25-4-53, as cited in Ohana Dwelling permit "
            "22-0008 Condition 6(d) (gis.hawaiicounty.gov) -- a 2-space minimum confirmed via "
            "that permit condition, not the master zoning-code table directly."
        )
    elif county == "maui":
        metadata["post_reform_parking_supply_context"] = maui_parking_reform_context()
        metadata["current_code_parking_requirement_source"] = (
            "Maui County Code Sec. 19.36B.020, as amended by Ord. 5983 (2023) -- an accessory "
            "dwelling must provide the same off-street parking as a single-family dwelling "
            "under the bedroom-based schedule (1 space for a studio/1-bedroom unit)."
        )
    elif county == "honolulu":
        metadata["post_reform_parking_supply_context"] = honolulu_parking_reform_context()
        # ROH 21-6.30(b)'s own waiver provisions (confirmed 2026-10-15):
        # the +1 stall requirement this scenario's land-consumed figure
        # assumes is waived outright within 0.5mi of a rail station, or
        # within 800ft of a qualifying bus stop in the PUC/Ewa plan areas.
        # No rail-station or bus-stop geometry has been fetched yet (see
        # the ADU wiring commit's own notes), so this land-consumed
        # figure is an upper bound -- it assumes the stall is required
        # everywhere, which overstates the land actually at stake in
        # whichever share of Honolulu's eligible parcels already qualify
        # for a waiver under current code.
        metadata["parking_waiver_not_yet_applied_note"] = (
            "ROH 21-6.30(b) waives this requirement near rail stations and qualifying bus "
            "stops (see data/reference/honolulu_adu_ordinance_verification.md) -- not yet "
            "modeled here for lack of station/stop geometry, so parking_land_consumed_sqft "
            "overstates the true land at stake for whichever parcels already qualify."
        )

    out_path = OUT_DIR / f"{county}_parking_scenario_tract.csv"
    adu_tract.to_csv(out_path, index=False)
    return adu_tract, metadata


# Per the Official Rules: use this standardized figure for comparability
# across submissions rather than sourcing four different real county fee
# schedules (Honolulu's are documented, the outer counties' much less
# so -- see the Baseline table above). This is a Challenge-mandated
# assumption, not a researched Kauai-specific number.
STANDARD_DEVELOPMENT_FEE_PER_UNIT_USD = 50_000

# Real Hawaii per-unit government fee components, Honolulu DPP's own
# Affordable Housing Requirement Incentives handout (AHR_Incentives_
# handout.pdf, honolulu.gov/tod, Feb. 2025) -- the fees that document says
# are WAIVED for affordable units, i.e. the real dollar stack a market-rate
# unit otherwise pays at permit issuance. Used only to bound the fee-
# reduction slider's range to something the state actually charges, not to
# replace the Challenge-mandated $50,000 standardized fee above.
HAWAII_DEVELOPMENT_FEE_COMPONENTS_USD = {
    "wastewater_system_facility_charge": (6_616, 6_616),
    "water_system_facility_and_meter_charge": (4_200, 8_000),
    "building_permit_and_plan_review": (2_200, 2_200),
    "park_dedication_in_lieu_fee": (24_000, 57_000),
}
# Sum of the low ends (~$37,016) to the sum of the high ends (~$73,816),
# rounded to a clean number -- the slider's reasonable ceiling. Note this
# is a Honolulu-specific published breakdown; no equivalent Kauai-specific
# fee schedule was found public, so this is used as the best available
# statewide bound, not a Kauai-verified figure. The Challenge's own
# $50,000 standardized default sits almost exactly mid-range of this real
# stack -- a useful cross-check, not a coincidence engineered here.
DEVELOPMENT_FEE_SLIDER_MAX_USD = 75_000
DEVELOPMENT_FEE_SLIDER_MIN_USD = 0
DEVELOPMENT_FEE_SOURCE_NOTE = (
    "Honolulu DPP, Affordable Housing Requirement Incentives handout (honolulu.gov/tod, Feb. 2025) -- "
    "real per-unit fees ($6,616 wastewater + $4,200-8,000 water + ~$2,200 building permit + "
    "$24,000-57,000 park dedication-in-lieu, ~$37,000-74,000 combined) bound the slider's range "
    "to what Hawaii actually charges, rather than leaving it open-ended. Honolulu-specific; no "
    "published Kauai-specific breakdown was found, used here as the best available statewide bound."
)


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


# Real, Honolulu/Oahu-specific development pro forma inputs, digitized
# 2026-10-15 from the SAME "Real Estate Assumptions" table as
# KAUAI_LIHUE_PRO_FORMA_INPUTS above (same source report, page 52,
# user-supplied screenshots) -- this time the "Iwilei-Kapalama (O'ahu)"
# column. Notably, Oahu is this table's BASE-COST geography, not a
# derived figure: the other three sites' Low/Mid/High-Rise hard costs are
# a stated percentage premium over Oahu's own ($570 Maui = $475 Oahu x
# 1.20; $594 Kaua'i = $475 x 1.25; $532 Hawai'i x ~1.12) -- confirmed by
# cross-checking those arithmetic relationships against the table's own
# "Premium from O'ahu" row, not assumed.
#
# Oahu's own reference product in this table is Low/Mid/High-Rise
# FOR-SALE (a denser TOD-corridor typology), NOT the townhome-for-sale
# product Kauai/Maui/Hawai'i County are modeled on -- the table's
# Townhome-for-sale row is blank for Oahu. This is an honest difference
# in what the source itself populated, not a simplification made here --
# no townhome_for_sale entry is included below because the source gives
# none for this geography.
OAHU_IWILEI_KAPALAMA_PRO_FORMA_INPUTS = {
    "lowrise_for_sale": {
        "unit_size_gsf": 840,  # 700 GSF rental base x 1.20 for-sale size premium -- shared row, all 4 sites
        "hard_cost_per_gsf_usd": 475,
        "soft_cost_pct_of_hard": 0.15,
        "sale_price_per_unit_usd": {"market": 646_800, "affordable": 643_000},
    },
    "midrise_for_sale": {
        "unit_size_gsf": 840,
        "hard_cost_per_gsf_usd": 475,  # table repeats the same Low-Rise figure for Mid-Rise, all 4 sites
        "soft_cost_pct_of_hard": 0.15,
        "sale_price_per_unit_usd": {"market": 679_140, "affordable": 643_000},
    },
    "highrise_for_sale": {
        "unit_size_gsf": 840,
        "hard_cost_per_gsf_usd": 475,
        "soft_cost_pct_of_hard": 0.15,
        "sale_price_per_unit_usd": {"market": 711_480, "affordable": 643_000},
    },
}
# "Average Price Per SF, Mid- and High-Density Residential" -- Oahu column.
# An order of magnitude above Kauai's $36: Iwilei-Kapalama is an urban
# Honolulu TOD corridor, not Lihu'e's town core, and the source's own land
# comps reflect that real difference rather than a modeling choice made
# here.
OAHU_IWILEI_KAPALAMA_LAND_PRICE_PER_BUILDING_GSF_USD = 225
# Financing terms and the 15% for-sale profit margin are the SAME across
# all four of this table's TOD sites -- explicitly grouped under the
# source's own "Shared Assumptions" heading, not a coincidence or a reuse
# shortcut taken here. KAUAI_LIHUE_FINANCING and
# KAUAI_LIHUE_DEVELOPER_PROFIT_MARGIN_PCT_OF_SALE are reused as-is below
# despite their Kauai-specific names, because the underlying source values
# genuinely don't vary by geography in this table.


def oahu_iwilei_kapalama_development_fee_reduction_feasibility(
    typology: str = "lowrise_for_sale",
    affordability: str = "market",
    development_fee_usd: float | None = None,
) -> dict:
    """
    Honolulu/Oahu analog of kauai_development_fee_reduction_feasibility(),
    same "excess value" feasibility test (market value minus total
    development cost minus required developer profit -- see
    docs/MODEL_AND_ASSUMPTIONS.md), same two explicit simplifications
    (land cost allocated per building-GSF rather than a true site-area/FAR
    basis; financing cost uses an average-outstanding-balance
    approximation rather than a full monthly draw schedule).

    development_fee_usd defaults to Honolulu's own real development-fee
    schedule midpoint (HAWAII_DEVELOPMENT_FEE_COMPONENTS_USD -- sourced to
    Honolulu DPP directly, unlike the Challenge's standardized $50,000
    default used elsewhere), since this function is Honolulu-specific and
    a more accurate default is available for it specifically.
    """
    if development_fee_usd is None:
        development_fee_usd = sum(
            (lo + hi) / 2 for lo, hi in HAWAII_DEVELOPMENT_FEE_COMPONENTS_USD.values()
        )
    inputs = OAHU_IWILEI_KAPALAMA_PRO_FORMA_INPUTS[typology]
    unit_size = inputs["unit_size_gsf"]
    hard_cost = unit_size * inputs["hard_cost_per_gsf_usd"]
    soft_cost = hard_cost * inputs["soft_cost_pct_of_hard"]
    land_cost = unit_size * OAHU_IWILEI_KAPALAMA_LAND_PRICE_PER_BUILDING_GSF_USD
    cost_before_financing_and_fee = hard_cost + soft_cost + land_cost

    fin = KAUAI_LIHUE_FINANCING  # shared across all 4 sites in the source table -- see note above
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
        "development_fee_usd": round(development_fee_usd),
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
        "Iwilei-Kapalama (O'ahu) column.",
    }


# Honolulu countywide median home value (ACS 2022 5-year, B25077, tract
# median of medians) -- same role as KAUAI_COUNTYWIDE_MEDIAN_HOME_VALUE_USD
# below: the denominator used to scale the Iwilei-Kapalama reference sale
# price to each Honolulu tract's own housing market. Verified live via
# Census API, 2026-10-14 -- see docs/EXISTING_CONDITIONS.md.
HONOLULU_COUNTYWIDE_MEDIAN_HOME_VALUE_USD = 809_550

# Zillow Observed Rent Index (ZORI), Honolulu County, Aug 2026 -- same role
# as KAUAI_COUNTYWIDE_ZORI_USD_PER_MONTH below. Unlike Kauai, Honolulu has
# full ZIP-level ZORI coverage in principle, but only the countywide series
# has been pulled so far (to match the ACS/sales geography used everywhere
# else here) -- a real option for a future pass, not yet done.
HONOLULU_COUNTYWIDE_ZORI_USD_PER_MONTH = 3_019.72  # Aug 2026, latest available


def tract_scaled_oahu_development_fee_reduction_feasibility(
    acs_commute_density_csv: Path | None = None,
    typology: str = "lowrise_for_sale",
    affordability: str = "market",
    development_fee_usd: float | None = None,
) -> pd.DataFrame:
    """
    Honolulu analog of tract_scaled_development_fee_reduction_feasibility()
    below -- runs oahu_iwilei_kapalama_development_fee_reduction_feasibility()
    once per tract, scaling the Iwilei-Kapalama reference sale price by each
    tract's own ACS median home value relative to HONOLULU_COUNTYWIDE_
    MEDIAN_HOME_VALUE_USD, same mechanism and same stated limitations as the
    Kauai version (cost side held fixed at the source table's figures; only
    revenue is tract-scaled). Added 2026-10-15 -- Honolulu previously had no
    feasibility gate at all; this wires the real Oahu pro forma already
    sourced in this file into the scenario pipeline rather than leaving it
    computed but unused.
    """
    path = acs_commute_density_csv or (RAW_DIR / "acs_commute_density_honolulu.csv")
    if not path.exists():
        raise RuntimeError(
            f"{path} not found -- run `fetch_data.py --layer acs_commute_density --county honolulu` first."
        )
    acs = pd.read_csv(path, dtype={"GEOID": str})
    acs = acs.dropna(subset=["median_home_value_usd"]).copy()

    base_inputs = OAHU_IWILEI_KAPALAMA_PRO_FORMA_INPUTS[typology]
    reference_sale_price = base_inputs["sale_price_per_unit_usd"][affordability]

    rows = []
    for _, tract in acs.iterrows():
        scale = tract["median_home_value_usd"] / HONOLULU_COUNTYWIDE_MEDIAN_HOME_VALUE_USD
        scaled_sale_price = reference_sale_price * scale
        scaled_inputs = dict(OAHU_IWILEI_KAPALAMA_PRO_FORMA_INPUTS[typology])
        scaled_inputs["sale_price_per_unit_usd"] = {affordability: scaled_sale_price}
        original = OAHU_IWILEI_KAPALAMA_PRO_FORMA_INPUTS[typology]
        OAHU_IWILEI_KAPALAMA_PRO_FORMA_INPUTS[typology] = scaled_inputs
        try:
            result = oahu_iwilei_kapalama_development_fee_reduction_feasibility(
                typology, affordability, development_fee_usd
            )
        finally:
            OAHU_IWILEI_KAPALAMA_PRO_FORMA_INPUTS[typology] = original
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


def honolulu_tract_affordability_constrained_capacity(
    income_csv: Path | None = None,
    sales_feasibility_df: pd.DataFrame | None = None,
    tenure: str = "for_sale",
) -> pd.DataFrame:
    """
    Honolulu analog of tract_affordability_constrained_capacity() below --
    required income backed out of the comps-implied price or, for rental,
    the countywide ZORI figure. For `tenure="for_sale"`, checked against
    the tract's real ACS B19001 (ALL households) income distribution, same
    as before. **Updated 2026-10-15, per the user's direct request:** for
    `tenure="rental"`, now checked against the tract's real ACS B25118
    RENTER-OCCUPIED-ONLY income distribution instead of B19001's blended
    all-household one -- the right population for a rental affordability
    question, since B19001 includes owner households who already bought
    and aren't shopping for a new rental unit.
    """
    rows = []
    if tenure == "for_sale":
        path = income_csv or (RAW_DIR / "acs_income_distribution_honolulu.csv")
        if not path.exists():
            raise RuntimeError(
                f"{path} not found -- run `fetch_data.py --layer acs_income_distribution "
                "--county honolulu` first (needs CENSUS_API_KEY)."
            )
        income = pd.read_csv(path, dtype={"GEOID": str})
        if sales_feasibility_df is None:
            raise ValueError("tenure='for_sale' requires sales_feasibility_df (a comps/ACS feasibility DataFrame).")
        feas = sales_feasibility_df.copy()
        feas["GEOID"] = feas["GEOID"].astype(str)
        merged = income.merge(feas[["GEOID", "scaled_sale_price_usd"]], on="GEOID", how="inner")
        for _, tract in merged.iterrows():
            required_income = _required_income_for_sale_price(tract["scaled_sale_price_usd"])
            bracket_counts = {col: tract[col] for col, _, _ in ACS_B19001_BRACKETS_FOR_MODEL}
            pct = _pct_households_above_income(bracket_counts, tract["total_households"], required_income)
            rows.append(
                {
                    "GEOID": tract["GEOID"],
                    "tenure": tenure,
                    "price_or_rent_usd": round(tract["scaled_sale_price_usd"]),
                    "required_income_usd": round(required_income),
                    "total_households": int(tract["total_households"]),
                    "pct_households_affording": round(pct, 1),
                }
            )
    elif tenure == "rental":
        path = income_csv or (RAW_DIR / "acs_renter_income_distribution_honolulu.csv")
        if not path.exists():
            raise RuntimeError(
                f"{path} not found -- run `fetch_data.py --layer acs_renter_income_distribution "
                "--county honolulu` first (needs CENSUS_API_KEY)."
            )
        renter_income = pd.read_csv(path, dtype={"GEOID": str})
        required_income = _required_income_for_rent(HONOLULU_COUNTYWIDE_ZORI_USD_PER_MONTH)
        for _, tract in renter_income.iterrows():
            if tract["total_renter_households"] <= 0:
                continue
            bracket_counts = {col: tract[col] for col, _, _ in ACS_B25118_RENTER_BRACKETS_FOR_MODEL}
            pct = _pct_households_above_income(
                bracket_counts, tract["total_renter_households"], required_income,
                brackets=ACS_B25118_RENTER_BRACKETS_FOR_MODEL,
            )
            rows.append(
                {
                    "GEOID": tract["GEOID"],
                    "tenure": tenure,
                    "price_or_rent_usd": HONOLULU_COUNTYWIDE_ZORI_USD_PER_MONTH,
                    "required_income_usd": round(required_income),
                    "total_households": int(tract["total_renter_households"]),
                    "pct_households_affording": round(pct, 1),
                }
            )
    else:
        raise ValueError(f"tenure must be 'for_sale' or 'rental', got {tenure!r}")
    return pd.DataFrame(rows)


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

# Zillow Observed Rent Index (ZORI), Kauai County, Aug 2026 -- county-level
# is the finest granularity Zillow publishes a Kauai rent figure at.
# Checked ZIP-level coverage first (files.zillowstatic.com/research/
# public_csvs/zori/Zip_zori_uc_sfrcondomfr_sm_month.csv): ZERO Kauai zip
# codes appear -- the rental market there is too thin for Zillow to
# publish a reliable index at that granularity. County-level (`County_
# zori_uc_sfrcondomfr_sm_month.csv`) does have a "Kauai County" row.
# Decision, 2026-10-15: use this single county-wide figure, same role as
# KAUAI_COUNTYWIDE_MEDIAN_HOME_VALUE_USD above, rather than switching to a
# paid provider (e.g. Rentcast) for finer granularity -- a different
# vendor re-slicing the same small set of actual Kauai rental listings
# doesn't manufacture real tract-level signal, it just adds an unverified,
# paid dependency for a market too thin to support that granularity either
# way. NOT YET WIRED INTO A SCENARIO: this is the market-rent input a real
# multifamily RENTAL pro forma (rent -> NOI -> supportable value at a cap
# rate, vs. mid-rise construction cost) would need to properly feasibility-
# test the TOD scenario's higher-density parcels, which the current
# feasibility gate still can't do (it prices a townhome-RESALE product --
# see the Transit-Oriented Density section of MODEL_AND_ASSUMPTIONS.md).
# Building that rental pro forma is the next real step, not yet done.
KAUAI_COUNTYWIDE_ZORI_USD_PER_MONTH = 3_708  # Aug 2026, latest available


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


def tract_comps_development_fee_reduction_feasibility(
    sales_joined_csv: Path | None = None,
    typology: str = "townhome_for_sale",
    affordability: str = "market",
    development_fee_usd: float = STANDARD_DEVELOPMENT_FEE_PER_UNIT_USD,
    min_sales_per_tract: int = 5,
) -> pd.DataFrame:
    """
    Same mechanism as tract_scaled_development_fee_reduction_feasibility(),
    but scales the Lihu'e reference sale price by REAL observed
    transaction $/building-sqft per tract (user-supplied monthly county
    sales exports, Feb 2025-Jun 2026 -- 17 months, extended from the
    original 6 to match the Zillow ZORI rent series' own history -- processed
    by scripts/process_sales_exports.py -- see that script's docstring for
    the county-doesn't-self-serve-bulk-exports caveat and the TMK join-key
    reverse-engineering), instead of the ACS median-home-value RATIO used
    in the other version. This replaces a Census proxy with an actual
    revenue figure for all 17 of Kauai's 17 tracts (min_sales_per_tract=5
    excludes any tract without enough sales to trust a median).

    Sanity check against the existing pro forma: the countywide median
    across all 722 usable residential sales was $768/building-sqft --
    within 3% of the Lihu'e reference townhome's own implied $786/sqft
    ($1,179,403 / 1,500 sqft), which is reassuring cross-validation of the
    OPSD-sourced figure, not a coincidence manufactured by this function.

    Still scales REVENUE only, same stated asymmetry as the ACS version --
    no tract-level land-cost or hard-cost source exists yet, so cost
    inputs stay fixed at the Lihu'e table's figures everywhere.
    """
    path = sales_joined_csv or (OUT_DIR / "kauai_sales_2026_joined.csv")
    if not path.exists():
        raise RuntimeError(
            f"{path} not found -- run `scripts/process_sales_exports.py` first "
            "(needs monthly sales exports in data/raw/sales_exports/, which the user supplies "
            "manually -- no bulk self-service export exists on the county's site)."
        )
    sales = pd.read_csv(path, dtype={"GEOID": str})
    cfg = COUNTY_CONFIG["kauai"]
    usable = sales[
        (sales["building_sqft"] > 0)
        & sales["price_per_sqft"].notna()
        & sales["taxclass"].isin(cfg["existing_residential_taxclasses"])
        & sales["GEOID"].notna()
    ]
    by_tract = usable.groupby("GEOID")["price_per_sqft"].agg(["median", "count"])
    by_tract = by_tract[by_tract["count"] >= min_sales_per_tract]

    base_inputs = KAUAI_LIHUE_PRO_FORMA_INPUTS[typology]
    reference_sale_price = base_inputs["sale_price_per_unit_usd"][affordability]
    reference_price_per_sqft = reference_sale_price / base_inputs["unit_size_gsf"]

    rows = []
    for geoid, row in by_tract.iterrows():
        scale = row["median"] / reference_price_per_sqft
        scaled_sale_price = reference_sale_price * scale
        original = KAUAI_LIHUE_PRO_FORMA_INPUTS[typology]
        scaled_inputs = dict(original)
        scaled_inputs["sale_price_per_unit_usd"] = {affordability: scaled_sale_price}
        KAUAI_LIHUE_PRO_FORMA_INPUTS[typology] = scaled_inputs
        try:
            result = kauai_development_fee_reduction_feasibility(typology, affordability, development_fee_usd)
        finally:
            KAUAI_LIHUE_PRO_FORMA_INPUTS[typology] = original
        rows.append(
            {
                "GEOID": geoid,
                "comps_median_price_per_sqft": round(row["median"], 2),
                "comps_sale_count": int(row["count"]),
                "scaled_sale_price_usd": round(scaled_sale_price),
                "excess_value_usd": result["excess_value_usd"],
                "feasible": result["feasible"],
            }
        )
    return pd.DataFrame(rows)


# Hawaii 30-year fixed mortgage rate, HiCentral (Honolulu Board of
# REALTORS), https://www.hicentral.com/hawaii-mortgage-rates.php,
# confirmed live 2026-10-15 (page dated 2026-10-14). The page itself
# quotes rate/APR by lender for "20 percent down, $200,000 owner-occupant
# mortgages" -- nine lenders listed, 30-yr fixed rate ranging 6.750% to
# 9.625%; the 9.625% figure (Matanuska Valley FCU, an Alaska-based credit
# union, not a Hawaii-market lender) is excluded as an outlier, leaving a
# median 30-yr fixed rate of 7.000% across the eight Hawaii-market
# lenders. Used, with the same 20% down convention the page itself uses,
# to back required household income out of a sale price -- see
# tract_affordability_constrained_capacity() below.
HAWAII_MORTGAGE_RATE_30YR_FIXED = 0.070
HAWAII_MORTGAGE_DOWN_PAYMENT_PCT = 0.20
HAWAII_MORTGAGE_TERM_YEARS = 30

# Standard affordability underwriting thresholds: housing cost (principal
# + interest for a mortgage, gross rent for a rental) should not exceed
# this share of gross household income. 30% is the HUD/Census standard
# "cost-burdened" threshold, used here as the affordability bar rather
# than a project-invented number.
AFFORDABILITY_INCOME_SHARE = 0.30


def _required_income_for_sale_price(sale_price_usd: float) -> float:
    """
    Backs the household income required to afford `sale_price_usd` out of
    a standard mortgage calculation: 20% down (HAWAII_MORTGAGE_DOWN_PAYMENT_PCT,
    matching the HiCentral quote convention), amortized over
    HAWAII_MORTGAGE_TERM_YEARS at HAWAII_MORTGAGE_RATE_30YR_FIXED, with the
    monthly principal+interest payment capped at AFFORDABILITY_INCOME_SHARE
    of gross monthly income.

    STATED SIMPLIFICATION: this prices principal + interest only, not full
    PITI (property tax, homeowner's insurance, HOA/CPR fees, which are
    common on Kauai condo/CPR units) -- no Hawaii-specific property-tax-
    rate or insurance-cost source is in this project yet. Required income
    is therefore an UNDERSTATE of the true bar -- the real percentage of
    households who can afford a unit is at or below what this computes,
    never above it.
    """
    loan_amount = sale_price_usd * (1 - HAWAII_MORTGAGE_DOWN_PAYMENT_PCT)
    monthly_rate = HAWAII_MORTGAGE_RATE_30YR_FIXED / 12
    n_payments = HAWAII_MORTGAGE_TERM_YEARS * 12
    monthly_pi = loan_amount * (monthly_rate * (1 + monthly_rate) ** n_payments) / (
        (1 + monthly_rate) ** n_payments - 1
    )
    annual_pi = monthly_pi * 12
    return annual_pi / AFFORDABILITY_INCOME_SHARE


def _required_income_for_rent(monthly_rent_usd: float) -> float:
    """Annual income required to keep rent at AFFORDABILITY_INCOME_SHARE of gross income."""
    return (monthly_rent_usd * 12) / AFFORDABILITY_INCOME_SHARE


def _affordable_sale_price_for_income(annual_income_usd: float) -> float:
    """
    Inverse of _required_income_for_sale_price(): the maximum home price a
    household earning annual_income_usd could afford, under the same
    standard underwriting (20% down, 30-yr fixed at
    HAWAII_MORTGAGE_RATE_30YR_FIXED, monthly P&I capped at
    AFFORDABILITY_INCOME_SHARE of gross income). Same stated
    simplification as the forward direction -- principal + interest only,
    so this is an UPPER bound on what a household could truly afford once
    property tax, insurance, and HOA/CPR fees are included.
    """
    annual_pi = annual_income_usd * AFFORDABILITY_INCOME_SHARE
    monthly_pi = annual_pi / 12
    monthly_rate = HAWAII_MORTGAGE_RATE_30YR_FIXED / 12
    n_payments = HAWAII_MORTGAGE_TERM_YEARS * 12
    loan_amount = monthly_pi * ((1 + monthly_rate) ** n_payments - 1) / (
        monthly_rate * (1 + monthly_rate) ** n_payments
    )
    return loan_amount / (1 - HAWAII_MORTGAGE_DOWN_PAYMENT_PCT)


def _pct_households_above_income(
    bracket_counts: dict,
    total_households: int,
    required_income: float,
    brackets: list | None = None,
) -> float:
    """
    Real percentage of a tract's households with income >= required_income,
    computed from a real ACS income-bracket distribution rather than a
    median-based yes/no. Households in brackets entirely above the
    threshold count fully; the one bracket the threshold falls inside is
    split by linear interpolation (assumes income is spread uniformly
    within that bracket -- a standard, stated simplification, not a claim
    of the true within-bracket distribution). The open-ended top bracket
    counts fully if the threshold is below its floor, or not at all
    otherwise (no upper bound to interpolate against).

    `brackets` defaults to ACS_B19001_BRACKETS_FOR_MODEL (ALL households,
    16 brackets) for backward compatibility, but callers using a
    different bracket set -- e.g. ACS_B25118_RENTER_BRACKETS_FOR_MODEL
    (renter-occupied households only, 11 coarser brackets) -- MUST pass
    it explicitly. **Bug fixed 2026-10-15:** this function used to
    hardcode ACS_B19001_BRACKETS_FOR_MODEL regardless of what
    `bracket_counts` actually held, so passing renter-income brackets
    (different column names and boundaries) silently matched zero
    brackets via `.get(col, 0)` and returned 0.0 for every tract --
    caught when Honolulu's and Kauai's rental affordability both came
    back exactly 0% across every tract after switching to the real
    renter-income distribution, which is implausible on its face.
    """
    if total_households <= 0:
        return 0.0
    brackets = brackets or ACS_B19001_BRACKETS_FOR_MODEL
    qualifying = 0.0
    for col, lo, hi in brackets:
        count = bracket_counts.get(col, 0)
        if hi is None:  # open-ended top bracket
            if required_income <= lo:
                qualifying += count
            continue
        if required_income <= lo:
            qualifying += count
        elif required_income < hi:
            frac_above = (hi - required_income) / (hi - lo)
            qualifying += count * frac_above
        # else required_income >= hi: none of this bracket qualifies
    return 100 * qualifying / total_households


# Mirrors fetch_data.ACS_B19001_BRACKETS's (lo, hi) tiers, keyed to the
# column names fetch_acs_income_distribution() actually writes, so this
# module doesn't import fetch_data.py just for one constant.
ACS_B19001_BRACKETS_FOR_MODEL = [
    ("hh_income_0_10000", 0, 10_000),
    ("hh_income_10000_15000", 10_000, 15_000),
    ("hh_income_15000_20000", 15_000, 20_000),
    ("hh_income_20000_25000", 20_000, 25_000),
    ("hh_income_25000_30000", 25_000, 30_000),
    ("hh_income_30000_35000", 30_000, 35_000),
    ("hh_income_35000_40000", 35_000, 40_000),
    ("hh_income_40000_45000", 40_000, 45_000),
    ("hh_income_45000_50000", 45_000, 50_000),
    ("hh_income_50000_60000", 50_000, 60_000),
    ("hh_income_60000_75000", 60_000, 75_000),
    ("hh_income_75000_100000", 75_000, 100_000),
    ("hh_income_100000_125000", 100_000, 125_000),
    ("hh_income_125000_150000", 125_000, 150_000),
    ("hh_income_150000_200000", 150_000, 200_000),
    ("hh_income_200000_plus", 200_000, None),
]

# Mirrors fetch_data.ACS_B25118_RENTER_BRACKETS -- a REAL renter-occupied-
# only household income distribution (Census table B25118), fetched
# 2026-10-15 per the user's direct request. Unlike ACS_B19001_BRACKETS_FOR_
# MODEL above (ALL households, owners and renters blended), this is the
# income distribution of households who actually rent -- the right
# population to check rental affordability against, since a blended
# distribution includes owner households who already bought and aren't
# shopping for a rental unit.
ACS_B25118_RENTER_BRACKETS_FOR_MODEL = [
    ("renter_hh_income_0_5000", 0, 5_000),
    ("renter_hh_income_5000_10000", 5_000, 10_000),
    ("renter_hh_income_10000_15000", 10_000, 15_000),
    ("renter_hh_income_15000_20000", 15_000, 20_000),
    ("renter_hh_income_20000_25000", 20_000, 25_000),
    ("renter_hh_income_25000_35000", 25_000, 35_000),
    ("renter_hh_income_35000_50000", 35_000, 50_000),
    ("renter_hh_income_50000_75000", 50_000, 75_000),
    ("renter_hh_income_75000_100000", 75_000, 100_000),
    ("renter_hh_income_100000_150000", 100_000, 150_000),
    ("renter_hh_income_150000_plus", 150_000, None),
]

# Mirrors fetch_data.ACS_B25063_BRACKETS -- the REAL distribution of what
# renter-occupied units currently pay (Census table B25063), fetched
# 2026-10-15 per the user's direct request, as a check on the assumed
# market rent used in a rental pro forma (e.g. the Skyline TOD Feasibility
# Study's $3,200/mo figure) -- the same role Kauai's real sales comps play
# for the for-sale feasibility gate.
ACS_B25063_BRACKETS_FOR_MODEL = [
    ("rent_0_100", 0, 100),
    ("rent_100_150", 100, 150),
    ("rent_150_200", 150, 200),
    ("rent_200_250", 200, 250),
    ("rent_250_300", 250, 300),
    ("rent_300_350", 300, 350),
    ("rent_350_400", 350, 400),
    ("rent_400_450", 400, 450),
    ("rent_450_500", 450, 500),
    ("rent_500_550", 500, 550),
    ("rent_550_600", 550, 600),
    ("rent_600_650", 600, 650),
    ("rent_650_700", 650, 700),
    ("rent_700_750", 700, 750),
    ("rent_750_800", 750, 800),
    ("rent_800_900", 800, 900),
    ("rent_900_1000", 900, 1_000),
    ("rent_1000_1250", 1_000, 1_250),
    ("rent_1250_1500", 1_250, 1_500),
    ("rent_1500_2000", 1_500, 2_000),
    ("rent_2000_2500", 2_000, 2_500),
    ("rent_2500_3000", 2_500, 3_000),
    ("rent_3000_3500", 3_000, 3_500),
    ("rent_3500_plus", 3_500, None),
]


def pct_renters_currently_paying_at_or_above(
    county: str,
    threshold_rent_usd: float,
    rent_csv: Path | None = None,
) -> pd.DataFrame:
    """
    Real, honest cross-check on an ASSUMED market rent (e.g. the Skyline
    TOD Feasibility Study's $3,200/mo Honolulu figure, or
    KAUAI_COUNTYWIDE_ZORI_USD_PER_MONTH) against what renter-occupied units
    in each tract are REALLY paying today (ACS B25063), added 2026-10-15
    per the user's direct request. This does NOT feed into the feasibility
    gate -- a market-rent assumption for NEW construction is expected to
    sit above what the existing (often older, sometimes rent-stabilized or
    long-tenancy) rental stock charges on average. It is a transparency
    check: how far above today's real rents is the assumption actually
    sitting, tract by tract and countywide.

    The open-ended top bracket ($3,500+ or more) is NOT interpolated --
    there's no real data on its internal distribution, so a threshold that
    falls INSIDE that open bracket (as Kauai's $3,708 ZORI figure does)
    returns the share at or above the bracket's own $3,500 floor as an
    upper-bound estimate, flagged via `threshold_above_top_bracket_floor`,
    rather than guessing a finer split within it.
    """
    path = rent_csv or (RAW_DIR / f"acs_rent_distribution_{county}.csv")
    if not path.exists():
        raise RuntimeError(
            f"{path} not found -- run `fetch_data.py --layer acs_rent_distribution "
            f"--county {county}` first (needs CENSUS_API_KEY)."
        )
    rent = pd.read_csv(path, dtype={"GEOID": str})

    rows = []
    for _, tract in rent.iterrows():
        total = tract["total_with_cash_rent"]
        if total <= 0:
            continue
        qualifying = 0.0
        for col, lo, hi in ACS_B25063_BRACKETS_FOR_MODEL:
            count = tract[col]
            if hi is None:
                if lo >= threshold_rent_usd:
                    qualifying += count
                # threshold falls inside the open top bracket -- no finer
                # split available; counted as an upper bound, not interpolated.
                continue
            if lo >= threshold_rent_usd:
                qualifying += count
            elif hi > threshold_rent_usd:
                qualifying += count * (hi - threshold_rent_usd) / (hi - lo)
        rows.append(
            {
                "GEOID": tract["GEOID"],
                "total_renter_units_with_cash_rent": int(total),
                "pct_currently_paying_at_or_above": round(100 * qualifying / total, 1),
                "threshold_above_top_bracket_floor": threshold_rent_usd > 3_500,
            }
        )
    return pd.DataFrame(rows)


def _load_sales_joined(path_or_paths) -> pd.DataFrame:
    """
    Reads one county's processed sales-comps CSV, or -- for Hawai'i
    County, which as of 2026-10-28 has TWO independent sales sources that
    cover different, only-partially-overlapping windows and can't be
    produced by a single script -- concatenates several. The Regrid
    export (process_hawaii_county_sales_exports.py) is parcel-joined and
    carries assessor-recorded building_sqft but only covers 2025-01-02 to
    2025-05-09 (a partial year). The Zillow-listing export
    (process_hawaii_county_zillow_sales.py) is address-geocoded to a
    census tract (no parcel/TMK join is possible from a street address
    alone) and carries Zillow's self-reported sqft, but covers roughly
    the last 24 months. Concatenating both widens the real date range
    this county's comps draw from, at the cost of mixing two sqft
    definitions -- stated here and in each script's own docstring, not
    hidden. A path that doesn't exist yet (e.g. only one of the two
    scripts has been run so far) is skipped rather than raising, so this
    degrades gracefully to whichever source(s) are actually present.
    """
    paths = path_or_paths if isinstance(path_or_paths, (list, tuple)) else [path_or_paths]
    existing = [Path(p) for p in paths if Path(p).exists()]
    if not existing:
        raise RuntimeError(
            f"None of {[str(p) for p in paths]} found -- run this county's sales-processing "
            "script(s) first."
        )
    frames = [pd.read_csv(p, dtype={"GEOID": str}) for p in existing]
    return pd.concat(frames, ignore_index=True, sort=False)


def income_bracket_vs_affordable_stock_share(
    county: str = "kauai", income_csv: Path | None = None, sales_joined_csv: Path | None = None
) -> pd.DataFrame:
    """
    Pairs, for each real ACS B19001 income bracket, the real share of a
    county's households in that bracket against the real share of that
    same county's own sales comps (the same usable set as
    tract_comps_development_fee_reduction_feasibility(), Feb 2025-Jun
    2026) priced at or below what a household earning that bracket's
    representative income could afford, under this project's standard
    underwriting (20% down, 30-yr fixed at HAWAII_MORTGAGE_RATE_30YR_FIXED,
    30% of gross income -- the same math as tract_affordability_
    constrained_capacity(), just run in the other direction). This is the
    direct answer to "how much of what's actually selling could this
    income band ever buy" -- household SHARE, not a raw count, intended
    to sit next to the affordable-stock share as a clustered column chart.

    FIXED 2026-10-16: this function only ever accepted Kauai's own
    hardcoded file paths and had no `county` parameter at all -- so every
    caller that passed `county=...` (including Kauai's own dashboard
    build) was silently raising TypeError and falling back to an empty
    series, which is why the income/affordability chart was showing NO
    Kauai bars either, not just a missing Honolulu comparison as the
    surrounding code's comments assumed. Honolulu's own income
    distribution and sales-joined CSVs already exist and use the same
    column names, so both counties are wired through the same per-county
    file lookup below rather than one hardcoded path.

    The top, open-ended bracket ($200,000+) has no real upper bound; a
    representative $250,000 is used as a stated, documented stand-in
    (consistent with this project's other stated-simplification
    choices), and is called out wherever this is surfaced.
    """
    default_income_paths = {
        "kauai": RAW_DIR / "acs_income_distribution_kauai.csv",
        "honolulu": RAW_DIR / "acs_income_distribution_honolulu.csv",
    }
    default_sales_paths = {
        "kauai": OUT_DIR / "kauai_sales_2026_joined.csv",
        "honolulu": OUT_DIR / "honolulu_sales_joined.csv",
        "hawaii": [OUT_DIR / "hawaii_sales_joined.csv", OUT_DIR / "hawaii_zillow_sales_joined.csv"],
    }
    income_path = income_csv or default_income_paths.get(county, RAW_DIR / f"acs_income_distribution_{county}.csv")
    if not income_path.exists():
        raise RuntimeError(
            f"{income_path} not found -- run `fetch_data.py --layer acs_income_distribution --county {county}` first"
        )
    income_df = pd.read_csv(income_path, dtype={"GEOID": str})

    sales_path = sales_joined_csv or default_sales_paths.get(county, OUT_DIR / f"{county}_sales_joined.csv")
    sales = _load_sales_joined(sales_path)
    cfg = COUNTY_CONFIG[county]
    # Real bug, found 2026-10-28: this filter had no floor on sale_price_usd
    # and no valid_sale check, so a county whose raw export includes
    # non-arm's-length $0 transfers (Maui's: 11,160 of 14,179 rows are
    # flagged valid_sale=False by the county's own CAMA system, overwhelmingly
    # at $0) got those counted as "affordable sales" at every income bracket,
    # including the very lowest -- Maui's pct_sales_affordable was ~63% even
    # for a <$10,000/year household, which is not a real result, it's $0
    # non-market transfers passing through uncaught. Same `valid_sale`
    # convention already used correctly in build_dashboard_data.py's own
    # sales-chart wiring (grep `valid_col` there) is applied here too, plus a
    # hard sale_price_usd > 0 floor as a backstop for any county with no
    # valid_sale column at all (Hawai'i County's sales have no valid_sale
    # flag -- see process_hawaii_county_sales_exports.py's own stated
    # caveat -- but its real sale prices are already all >= $10,000, so this
    # floor is a no-op there, not a new exclusion).
    valid_col = sales["valid_sale"] if "valid_sale" in sales.columns else True
    usable_sales = sales[
        (sales["building_sqft"] > 0)
        & sales["price_per_sqft"].notna()
        & sales["sale_price_usd"].notna()
        & (sales["sale_price_usd"] > 0)
        & sales["taxclass"].isin(cfg["existing_residential_taxclasses"])
        & valid_col
    ]
    total_sales = len(usable_sales)
    bracket_cols = [c for c, _, _ in ACS_B19001_BRACKETS_FOR_MODEL]
    total_households = income_df[bracket_cols].sum().sum()

    rows = []
    for col, lo, hi in ACS_B19001_BRACKETS_FOR_MODEL:
        bracket_households = income_df[col].sum()
        rep_income = hi if hi is not None else 250_000
        affordable_price = _affordable_sale_price_for_income(rep_income)
        n_affordable = int((usable_sales["sale_price_usd"] <= affordable_price).sum())
        rows.append(
            {
                "bracket": col,
                "income_lo": lo,
                "income_hi": hi,
                "representative_income_usd": rep_income,
                "pct_households": round(100 * bracket_households / total_households, 2) if total_households else None,
                "affordable_sale_price_usd": round(affordable_price),
                "pct_sales_affordable": round(100 * n_affordable / total_sales, 2) if total_sales else None,
            }
        )
    return pd.DataFrame(rows)


# Real rental-product valuation inputs, re-fetched and cross-checked
# 2026-10-15 per the user's direct request ("re fetch but also check to
# see cap rates for this area"). Re-confirmed Appendix B of the Skyline
# TOD Feasibility Study, Final Deliverable (July 2026) -- the only page
# this project has found with a real, Hawaii-specific rental cap rate and
# NOI-to-value mechanism (see data/DATA_SOURCES.md for the full citation):
#   - Market Rate Rentals: $3,200/mo rent, 2.5% vacancy, 40% opex ratio,
#     4.5% cap rate, 3.0% rent growth, $1,400/unit/yr other (sewer)
#     revenue -> the study's own computed $574,080/unit stabilized value.
#   - Cross-check, same source, p.26: the Study Area's OWN actual 2025
#     multifamily cap rate was 4.4%, i.e. the 4.5% assumption is close to
#     (not invented independently of) a real observed local figure.
#   - Independent cross-check attempted against a second source
#     (apartmentloanstore.com, "Cap Rates in Honolulu, Hawaii," pulled
#     2026-10-15): Class A metro mid/high-rise multifamily 4.70%, Class B
#     5.10%, Class C 5.64% -- same order of magnitude and direction
#     (new/Class-A product trades tighter, closer to the Skyline figure)
#     but this page names no underlying survey or methodology, so it's
#     treated as weak corroboration, not a primary citation. A gated CBRE
#     H1 2025 Cap Rate Survey PDF and Marcus & Millichap's Honolulu office
#     page were also checked; neither yields a usable Honolulu-specific
#     figure (CBRE's survey requires a login/download not reachable here;
#     Marcus & Millichap's page has no Hawaii market data).
#   - No developer profit margin or required yield-on-cost spread for a
#     RENTAL deal is stated anywhere in the Skyline study (re-confirmed on
#     this re-fetch) -- unlike the for-sale pro forma's cited 15%-of-
#     sale-price margin, there is no Hawaii-sourced rental equivalent.
#     tract_scaled_rental_feasibility() below therefore computes excess
#     value WITHOUT subtracting a required profit margin -- a real,
#     stated gap, not an invented substitute.
SKYLINE_TOD_STUDY_RENTAL_INPUTS = {
    "rent_usd_per_month": 3_200,
    "other_revenue_usd_per_unit_annual": 1_400,
    "vacancy_pct": 0.025,
    "opex_ratio_pct": 0.40,
    "cap_rate_pct": 0.045,
    "rent_growth_pct": 0.03,
    # The study's own computed output, used directly as the reference
    # "stabilized value" rather than re-derived from the inputs above --
    # the appendix text available to this project describes the inputs
    # but not the exact NOI-assembly formula (how vacancy/opex/other
    # revenue combine), and reverse-engineering a formula that happens to
    # land on $574,080 risks silently fabricating a different mechanism
    # under the same label. Using the source's own stated result avoids that.
    "stabilized_value_per_unit_usd": 574_080,
    "source": "Skyline TOD Feasibility Study, Final Deliverable (July 2026), State of Hawaii "
    "DBEDT OPSD -- Appendix B, Detailed Assumptions, Market Rate Rentals (p.88-93); cap rate "
    "cross-checked against the Study Area's own real 2025 multifamily cap rate of 4.4% (p.26).",
}

KAUAI_COUNTYWIDE_MEDIAN_GROSS_RENT_USD = 1_733  # ACS B25064, tract median of medians, 16 of 17 tracts
HONOLULU_COUNTYWIDE_MEDIAN_GROSS_RENT_USD = 2_071  # ACS B25064, tract median of medians, 307 of 321 tracts


def tract_scaled_rental_feasibility(
    county: str,
    acs_commute_density_csv: Path | None = None,
    development_fee_usd: float = STANDARD_DEVELOPMENT_FEE_PER_UNIT_USD,
) -> pd.DataFrame:
    """
    Real rental-product feasibility test, added 2026-10-15 per the user's
    direct request, mirroring the for-sale tract-scaled feasibility
    functions above but with a rental-specific mechanism throughout:

    - REVENUE: SKYLINE_TOD_STUDY_RENTAL_INPUTS's $574,080/unit stabilized
      value (rent -> NOI -> cap-rate-implied value, all real Hawaii
      inputs), scaled by each tract's own real ACS B25064 median gross
      rent relative to the countywide median -- NOT by home value (the
      for-sale gate's scaling driver), since a tract's rent level, not
      its home-sale value, is the economically relevant driver of a
      rental product's achievable revenue.
    - COST: each county's own already-sourced hard/land cost figures
      (KAUAI_LIHUE_PRO_FORMA_INPUTS / OAHU_IWILEI_KAPALAMA_PRO_FORMA_
      INPUTS, "lowrise_for_sale" row -- the only product each table
      prices), held fixed across tracts, same stated asymmetry as the
      for-sale gates. Unit size is MULTIFAMILY_RENTAL_UNIT_SIZE_GSF (815
      sqft, the real RentCafe/Yardi Matrix rental-apartment average) --
      NOT the for-sale tables' own 840 sqft townhome/lowrise-for-sale
      unit size, since this is a rental product.
    - NO developer profit margin is subtracted (see
      SKYLINE_TOD_STUDY_RENTAL_INPUTS's comment) -- `excess_value_usd`
      here is therefore a more PERMISSIVE bar than the for-sale gates'
      (which do subtract one), stated plainly rather than silently
      applying a mismatched for-sale margin to a rental deal.
    """
    cfg = {
        "kauai": {
            "acs_path": RAW_DIR / "acs_commute_density_kauai.csv",
            "hard_cost_per_gsf_usd": KAUAI_LIHUE_PRO_FORMA_INPUTS["lowrise_for_sale"]["hard_cost_per_gsf_usd"],
            "soft_cost_pct_of_hard": KAUAI_LIHUE_PRO_FORMA_INPUTS["lowrise_for_sale"]["soft_cost_pct_of_hard"],
            "land_price_per_gsf_usd": KAUAI_LIHUE_LAND_PRICE_PER_BUILDING_GSF_USD,
            "financing": KAUAI_LIHUE_FINANCING,
            "countywide_median_gross_rent_usd": KAUAI_COUNTYWIDE_MEDIAN_GROSS_RENT_USD,
        },
        "honolulu": {
            "acs_path": RAW_DIR / "acs_commute_density_honolulu.csv",
            "hard_cost_per_gsf_usd": OAHU_IWILEI_KAPALAMA_PRO_FORMA_INPUTS["lowrise_for_sale"]["hard_cost_per_gsf_usd"],
            "soft_cost_pct_of_hard": OAHU_IWILEI_KAPALAMA_PRO_FORMA_INPUTS["lowrise_for_sale"]["soft_cost_pct_of_hard"],
            "land_price_per_gsf_usd": OAHU_IWILEI_KAPALAMA_LAND_PRICE_PER_BUILDING_GSF_USD,
            "financing": KAUAI_LIHUE_FINANCING,  # shared across all 4 TOD sites in the source table
            "countywide_median_gross_rent_usd": HONOLULU_COUNTYWIDE_MEDIAN_GROSS_RENT_USD,
        },
        # Hawai'i County / Maui entries, added 2026-10-27 -- see
        # _TRACT_SCALED_RENTAL_FEASIBILITY_EXTRA_CFG's own comment above for
        # why these are built from the generic pro-forma constants instead
        # of being hand-duplicated a third/fourth time inline here. Merged
        # in at call time (not literal dict keys above) only because this
        # dict's definition appears before the generic constants exist
        # later in the file; functionally identical to typing them inline.
        **_TRACT_SCALED_RENTAL_FEASIBILITY_EXTRA_CFG,
    }[county]

    path = acs_commute_density_csv or cfg["acs_path"]
    if not path.exists():
        raise RuntimeError(f"{path} not found -- run `fetch_data.py --layer acs_commute_density --county {county}` first.")
    acs = pd.read_csv(path, dtype={"GEOID": str})
    acs = acs.dropna(subset=["median_gross_rent_usd"]).copy()

    unit_size = MULTIFAMILY_RENTAL_UNIT_SIZE_GSF
    hard_cost = unit_size * cfg["hard_cost_per_gsf_usd"]
    soft_cost = hard_cost * cfg["soft_cost_pct_of_hard"]
    land_cost = unit_size * cfg["land_price_per_gsf_usd"]
    cost_before_financing_and_fee = hard_cost + soft_cost + land_cost

    fin = cfg["financing"]
    loan_amount = cost_before_financing_and_fee * fin["loan_to_cost"]
    construction_years = fin["construction_period_months"] / 12
    financing_interest = loan_amount * fin["construction_interest_rate"] * construction_years * 0.5
    financing_fees = loan_amount * (fin["construction_loan_fee_pct"] + fin["permanent_loan_fee_pct"])
    financing_cost = financing_interest + financing_fees

    total_development_cost = cost_before_financing_and_fee + financing_cost + development_fee_usd
    reference_value = SKYLINE_TOD_STUDY_RENTAL_INPUTS["stabilized_value_per_unit_usd"]

    rows = []
    for _, tract in acs.iterrows():
        scale = tract["median_gross_rent_usd"] / cfg["countywide_median_gross_rent_usd"]
        scaled_value = reference_value * scale
        excess_value = scaled_value - total_development_cost
        rows.append(
            {
                "GEOID": str(tract["GEOID"]),
                "median_gross_rent_usd": tract["median_gross_rent_usd"],
                "gross_rent_scale_vs_countywide": round(scale, 3),
                "scaled_stabilized_value_usd": round(scaled_value),
                "total_development_cost_usd": round(total_development_cost),
                "excess_value_usd": round(excess_value),
                "feasible": excess_value >= 0,
            }
        )
    return pd.DataFrame(rows)


def tract_affordability_constrained_capacity(
    income_csv: Path | None = None,
    sales_feasibility_df: pd.DataFrame | None = None,
    tenure: str = "for_sale",
) -> pd.DataFrame:
    """
    The third gate, distinct from feasibility: feasibility
    (kauai_development_fee_reduction_feasibility()/tract_comps_...()) asks
    whether a developer could profitably build and sell/rent at the
    comps-implied price. This asks a different question -- given that
    price, what share of the tract's OWN households could actually afford
    it? A tract can be feasible and still be a market of a handful of
    households able to pay the required price; this gate makes that
    explicit instead of leaving it assumed away.

    For `tenure="for_sale"`: uses each tract's `scaled_sale_price_usd`
    from tract_comps_development_fee_reduction_feasibility() (or the ACS-
    scaled version, if that's what's passed in), backs out the required
    household income via _required_income_for_sale_price() (20% down,
    7.000% 30-yr fixed, HiCentral 2026-10-14 -- see
    HAWAII_MORTGAGE_RATE_30YR_FIXED), then looks up the real percentage of
    that tract's households clearing that bar from ACS B19001.

    For `tenure="rental"`: uses the single countywide Zillow ZORI figure
    (KAUAI_COUNTYWIDE_ZORI_USD_PER_MONTH) rather than a tract-scaled rent --
    no tract-level Kauai rent source exists (see DATA_SOURCES.md's Zillow
    section), so every tract gets the same required-income bar for rental
    product. **Updated 2026-10-15, per the user's direct request:** checked
    against the tract's real ACS B25118 RENTER-OCCUPIED-ONLY income
    distribution instead of B19001's blended all-household one -- the
    right population for a rental affordability question, since B19001
    includes owner households who already bought and aren't shopping for
    a new rental unit.

    Returns one row per tract: `required_income_usd`, `pct_households_affording`,
    and (where `sales_feasibility_df` is for_sale) the comps price used.
    This is a PERCENTAGE, not a unit count -- apply_affordability_gate()
    below is what multiplies it onto a scenario's unit estimate.
    """
    rows = []
    if tenure == "for_sale":
        if sales_feasibility_df is None:
            raise ValueError("tenure='for_sale' requires sales_feasibility_df (a comps/ACS feasibility DataFrame).")
        path = income_csv or (RAW_DIR / "acs_income_distribution_kauai.csv")
        if not path.exists():
            raise RuntimeError(
                f"{path} not found -- run `fetch_data.py --layer acs_income_distribution "
                "--county kauai` first (needs CENSUS_API_KEY)."
            )
        income = pd.read_csv(path, dtype={"GEOID": str})
        feas = sales_feasibility_df.copy()
        feas["GEOID"] = feas["GEOID"].astype(str)
        merged = income.merge(feas[["GEOID", "scaled_sale_price_usd"]], on="GEOID", how="inner")
        for _, tract in merged.iterrows():
            required_income = _required_income_for_sale_price(tract["scaled_sale_price_usd"])
            bracket_counts = {col: tract[col] for col, _, _ in ACS_B19001_BRACKETS_FOR_MODEL}
            pct = _pct_households_above_income(bracket_counts, tract["total_households"], required_income)
            rows.append(
                {
                    "GEOID": tract["GEOID"],
                    "tenure": tenure,
                    "price_or_rent_usd": round(tract["scaled_sale_price_usd"]),
                    "required_income_usd": round(required_income),
                    "total_households": int(tract["total_households"]),
                    "pct_households_affording": round(pct, 1),
                }
            )
    elif tenure == "rental":
        path = income_csv or (RAW_DIR / "acs_renter_income_distribution_kauai.csv")
        if not path.exists():
            raise RuntimeError(
                f"{path} not found -- run `fetch_data.py --layer acs_renter_income_distribution "
                "--county kauai` first (needs CENSUS_API_KEY)."
            )
        renter_income = pd.read_csv(path, dtype={"GEOID": str})
        required_income = _required_income_for_rent(KAUAI_COUNTYWIDE_ZORI_USD_PER_MONTH)
        for _, tract in renter_income.iterrows():
            if tract["total_renter_households"] <= 0:
                continue
            bracket_counts = {col: tract[col] for col, _, _ in ACS_B25118_RENTER_BRACKETS_FOR_MODEL}
            pct = _pct_households_above_income(
                bracket_counts, tract["total_renter_households"], required_income,
                brackets=ACS_B25118_RENTER_BRACKETS_FOR_MODEL,
            )
            rows.append(
                {
                    "GEOID": tract["GEOID"],
                    "tenure": tenure,
                    "price_or_rent_usd": KAUAI_COUNTYWIDE_ZORI_USD_PER_MONTH,
                    "required_income_usd": round(required_income),
                    "total_households": int(tract["total_renter_households"]),
                    "pct_households_affording": round(pct, 1),
                }
            )
    else:
        raise ValueError(f"tenure must be 'for_sale' or 'rental', got {tenure!r}")
    return pd.DataFrame(rows)


def apply_affordability_gate(
    scenario_tract_df: pd.DataFrame,
    affordability_df: pd.DataFrame,
    units_col: str = "feasibility_adjusted_units",
) -> pd.DataFrame:
    """
    Stacks on top of apply_feasibility_gate(), not instead of it: scales
    `units_col` (normally the feasibility-adjusted number) by
    `pct_households_affording` / 100 -- a demand-side ceiling, not a
    second yes/no toggle. A tract where a project is feasible for a
    developer but only 8% of local households clear the required-income
    bar now shows ~8% of the feasibility-adjusted units as the realistic,
    market-supportable number, instead of implying every feasible unit
    would actually find a paying household.

    STATED SIMPLIFICATION: this treats "% of tract households who could
    afford the unit" as a direct ceiling on units absorbed in that tract,
    which assumes buyers/renters come from the same tract rather than a
    wider commute-shed or move-in population -- a conservative, locally-
    grounded assumption (undercounts regional demand), not a claim that
    every qualifying household in a tract is in the market for a new
    unit this year (that's still what the separate absorption rate is
    for). The two are multiplicative: affordability narrows WHO could
    ever pay for a unit; absorption rate narrows how many of those who
    could, do, in a given year.
    """
    scenario_tract_df = scenario_tract_df.copy()
    scenario_tract_df["GEOID"] = scenario_tract_df["GEOID"].astype(str)
    affordability_df = affordability_df.copy()
    affordability_df["GEOID"] = affordability_df["GEOID"].astype(str)
    merged = scenario_tract_df.merge(
        affordability_df[["GEOID", "pct_households_affording", "required_income_usd"]],
        on="GEOID",
        how="left",
    )
    merged["pct_households_affording"] = merged["pct_households_affording"].fillna(0.0)
    merged["affordability_adjusted_units"] = (
        merged[units_col] * merged["pct_households_affording"] / 100
    )
    return merged


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
    merge_cols = ["GEOID", "feasible", "excess_value_usd"]
    # When `feasibility_df` comes from combine_dual_tenure_feasibility()
    # (added 2026-10-15), carry `winning_tenure` through too -- it's what
    # apply_dual_tenure_affordability_gate() needs downstream to route
    # each tract to the correct (for-sale vs rental) income distribution.
    # Single-tenure feasibility DataFrames don't have this column, so
    # this is a no-op for every existing call site.
    if "winning_tenure" in feasibility_df.columns:
        merge_cols.append("winning_tenure")
    merged = scenario_tract_df.merge(feasibility_df[merge_cols], on="GEOID", how="left")
    merged["feasible"] = merged["feasible"].fillna(False)
    merged["feasibility_adjusted_units"] = merged[estimated_units_col].where(merged["feasible"], 0.0)
    return merged


def combine_dual_tenure_feasibility(
    for_sale_feasibility_df: pd.DataFrame,
    rental_feasibility_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Combines a for-sale and a rental feasibility test for the SAME
    tracts into ONE feasibility verdict per tract. Added 2026-10-15, per
    the user's direct question: "how do we account for the differences
    between the feasibility gap of for sale vs rental? If either one is
    feasible do we just assume it's going to get built?"

    The answer implemented here is neither "either feasible -> built"
    (which would let one unit of zoning capacity count as BOTH a
    for-sale AND a rental outcome -- double-counting it) nor summing the
    two excess values. A given unit of zoning capacity gets built as ONE
    product: a developer builds whichever pencils better, not both. So
    this takes the per-tract MAX of the two tests' `excess_value_usd`
    and tags which tenure won via `winning_tenure`. Exactly one tenure
    wins per tract (or neither, if both are infeasible), which is what
    lets `apply_dual_tenure_affordability_gate()` below route each
    tract's capacity to the correct income distribution without any unit
    ever being counted under both tenures.

    A tract missing one side's feasibility data (e.g. no ACS median
    gross rent for the rental test) simply isn't tested on that axis --
    it competes only on whichever side has data, rather than being
    dropped or defaulted to infeasible on the missing side.

    STATED SIMPLIFICATION: a tract where BOTH tenures pencil is treated
    winner-take-all (100% of that tract's capacity tagged with whichever
    excess value is larger), not split between the two. A more granular
    model would split it by a real local multifamily-rental-vs-for-sale
    permit-mix share for that submarket; no such tract-level source is in
    this project yet, so this states the simplification rather than
    inventing a split ratio.
    """
    fs = for_sale_feasibility_df[["GEOID", "excess_value_usd"]].copy()
    fs["GEOID"] = fs["GEOID"].astype(str)
    fs = fs.rename(columns={"excess_value_usd": "excess_value_usd_for_sale"})

    rent = rental_feasibility_df[["GEOID", "excess_value_usd"]].copy()
    rent["GEOID"] = rent["GEOID"].astype(str)
    rent = rent.rename(columns={"excess_value_usd": "excess_value_usd_rental"})

    merged = fs.merge(rent, on="GEOID", how="outer")

    rows = []
    for _, r in merged.iterrows():
        fs_val = r["excess_value_usd_for_sale"]
        rent_val = r["excess_value_usd_rental"]
        fs_ok = pd.notna(fs_val)
        rent_ok = pd.notna(rent_val)
        if fs_ok and rent_ok:
            if fs_val >= rent_val:
                winning_tenure, excess_value = "for_sale", fs_val
            else:
                winning_tenure, excess_value = "rental", rent_val
        elif fs_ok:
            winning_tenure, excess_value = "for_sale", fs_val
        elif rent_ok:
            winning_tenure, excess_value = "rental", rent_val
        else:
            winning_tenure, excess_value = None, None
        rows.append(
            {
                "GEOID": r["GEOID"],
                "excess_value_usd_for_sale": fs_val,
                "excess_value_usd_rental": rent_val,
                "winning_tenure": winning_tenure,
                "excess_value_usd": excess_value,
                "feasible": (excess_value is not None) and (excess_value >= 0),
            }
        )
    return pd.DataFrame(rows)


def apply_dual_tenure_affordability_gate(
    scenario_tract_df: pd.DataFrame,
    for_sale_affordability_df: pd.DataFrame,
    rental_affordability_df: pd.DataFrame,
    units_col: str = "feasibility_adjusted_units",
) -> pd.DataFrame:
    """
    Dual-tenure analog of apply_affordability_gate(), added 2026-10-15
    alongside combine_dual_tenure_feasibility() -- see that function's
    docstring for the "why" (not double-counting a unit under both
    tenures).

    Requires `scenario_tract_df` to already carry a `winning_tenure`
    column, i.e. it must have been run through apply_feasibility_gate()
    using a combine_dual_tenure_feasibility() output as the feasibility
    df. This routes each tract's `units_col` through the CORRECT
    affordability test for whichever tenure won that tract's feasibility
    test: for_sale-won tracts check the real ACS B19001-derived owner-
    household affordability percentage; rental-won tracts check the real
    ACS B25118 renter-occupied-household one. Exactly one tenure's
    affordability percentage applies per tract -- nothing is counted, or
    discounted, twice.

    This "closes the loop" the user asked about: alongside the combined
    `affordability_adjusted_units` column (kept so existing dashboard
    code that only knows the single-tenure column name still works
    unchanged), it adds `affordability_adjusted_units_for_sale` and
    `affordability_adjusted_units_rental` breakouts, plus the
    tenure-specific required-income figures, so a reform's impact can be
    reported as "creates N units; M affordable to owner households
    earning $A+, K affordable to renter households earning $B+" instead
    of one blended number that hides which households actually benefit.
    """
    df = scenario_tract_df.copy()
    df["GEOID"] = df["GEOID"].astype(str)
    if "winning_tenure" not in df.columns:
        raise ValueError(
            "scenario_tract_df must carry a 'winning_tenure' column -- run it through "
            "apply_feasibility_gate() with a combine_dual_tenure_feasibility() output first."
        )

    fs_afford = for_sale_affordability_df[["GEOID", "pct_households_affording", "required_income_usd"]].copy()
    fs_afford["GEOID"] = fs_afford["GEOID"].astype(str)
    fs_afford = fs_afford.rename(
        columns={
            "pct_households_affording": "pct_households_affording_for_sale",
            "required_income_usd": "required_income_usd_for_sale",
        }
    )
    rent_afford = rental_affordability_df[["GEOID", "pct_households_affording", "required_income_usd"]].copy()
    rent_afford["GEOID"] = rent_afford["GEOID"].astype(str)
    rent_afford = rent_afford.rename(
        columns={
            "pct_households_affording": "pct_households_affording_rental",
            "required_income_usd": "required_income_usd_rental",
        }
    )

    merged = df.merge(fs_afford, on="GEOID", how="left").merge(rent_afford, on="GEOID", how="left")
    merged["pct_households_affording_for_sale"] = merged["pct_households_affording_for_sale"].fillna(0.0)
    merged["pct_households_affording_rental"] = merged["pct_households_affording_rental"].fillna(0.0)

    is_for_sale = merged["winning_tenure"] == "for_sale"
    is_rental = merged["winning_tenure"] == "rental"

    merged["affordability_adjusted_units_for_sale"] = 0.0
    merged["affordability_adjusted_units_rental"] = 0.0
    merged.loc[is_for_sale, "affordability_adjusted_units_for_sale"] = (
        merged.loc[is_for_sale, units_col] * merged.loc[is_for_sale, "pct_households_affording_for_sale"] / 100
    )
    merged.loc[is_rental, "affordability_adjusted_units_rental"] = (
        merged.loc[is_rental, units_col] * merged.loc[is_rental, "pct_households_affording_rental"] / 100
    )
    merged["affordability_adjusted_units"] = (
        merged["affordability_adjusted_units_for_sale"] + merged["affordability_adjusted_units_rental"]
    )
    merged["pct_households_affording"] = 0.0
    merged.loc[is_for_sale, "pct_households_affording"] = merged.loc[is_for_sale, "pct_households_affording_for_sale"]
    merged.loc[is_rental, "pct_households_affording"] = merged.loc[is_rental, "pct_households_affording_rental"]
    merged["required_income_usd"] = pd.NA
    merged.loc[is_for_sale, "required_income_usd"] = merged.loc[is_for_sale, "required_income_usd_for_sale"]
    merged.loc[is_rental, "required_income_usd"] = merged.loc[is_rental, "required_income_usd_rental"]
    return merged


# ---------------------------------------------------------------------------
# Hawai'i County and Maui pro formas, added 2026-10-27 per the user's direct
# instruction ("do both. we have the data for all counties now"), using the
# SAME source table as KAUAI_LIHUE_PRO_FORMA_INPUTS/OAHU_IWILEI_KAPALAMA_
# PRO_FORMA_INPUTS above (HR&A Advisors et al., "Financial Analysis of TOD
# Infrastructure Funding and Financing Options," State of Hawaii OPSD,
# September 2023, "Real Estate Assumptions" table, p.52-53) -- this report
# covers FOUR TOD pilot areas, not just Honolulu and Kaua'i: Ka'ahumanu
# Avenue Community Corridor (Maui) and Ane Keohokalole Highway Corridor,
# Kailua-Kona (Hawai'i County) are the other two columns, confirmed by
# downloading and pdftotext-extracting the full report
# (files.hawaii.gov/dbedt/op/lud/02PROJ/OPSDTODInfra_2022/
# 3.Deliverable3_20230908_HawaiiTODInfraFin-FullFinal.pdf) rather than
# assumed from the two counties already coded.
#
# Rather than duplicating ~150 lines of near-identical pro-forma/feasibility
# code per county (the pattern KAUAI_LIHUE_*/OAHU_IWILEI_KAPALAMA_* already
# follow), this section generalizes it ONE time behind SITE_PRO_FORMA_CONFIG
# -- a per-county lookup -- and two new county-parameterized functions that
# both new counties use, and that a future 5th county could reuse too. The
# existing Kaua'i/Honolulu-specific functions above are left untouched
# (zero risk of regressing their already-verified output); only Hawai'i
# County and Maui route through the new generic path.
MAUI_KAAHUMANU_PRO_FORMA_INPUTS = {
    "townhome_for_sale": {
        "unit_size_gsf": 1_500,
        "hard_cost_per_gsf_usd": 420,  # source table's Townhomes Hard Costs per GSF, Maui column
        "soft_cost_pct_of_hard": 0.15,
        "sale_price_per_unit_usd": {"market": 1_213_321, "affordable": 605_100},
    },
    "lowrise_for_sale": {
        "unit_size_gsf": 840,  # 700 GSF rental base x 1.20 for-sale size premium -- shared row, all 4 sites
        "hard_cost_per_gsf_usd": 570,
        "soft_cost_pct_of_hard": 0.15,
        "sale_price_per_unit_usd": {"market": 612_497, "affordable": 302_600},
    },
}
# "Average Price Per SF, Mid- and High-Density Residential" -- Maui column.
MAUI_KAAHUMANU_LAND_PRICE_PER_BUILDING_GSF_USD = 34
# Developer profit margin (15%, for-sale) and financing terms (60%
# loan-to-cost, 24-mo construction period, 7.00% construction interest,
# 1.50%/1.00% fees) are under the source's own "Shared Assumptions"
# heading and verified identical across all 4 TOD sites in the table
# (re-confirmed 2026-10-27 against the Maui and Hawai'i County columns
# specifically, not just assumed from the Kaua'i/Honolulu ones already
# coded) -- so KAUAI_LIHUE_FINANCING and
# KAUAI_LIHUE_DEVELOPER_PROFIT_MARGIN_PCT_OF_SALE are reused as-is below,
# same as oahu_iwilei_kapalama_development_fee_reduction_feasibility()
# already does.

HAWAII_ANEKEOHOKALOLE_PRO_FORMA_INPUTS = {
    "townhome_for_sale": {
        "unit_size_gsf": 1_500,
        "hard_cost_per_gsf_usd": 440,  # source table's Townhomes Hard Costs per GSF, Hawai'i column
        "soft_cost_pct_of_hard": 0.15,
        "sale_price_per_unit_usd": {"market": 1_061_274, "affordable": 560_900},
    },
    "lowrise_for_sale": {
        "unit_size_gsf": 840,
        "hard_cost_per_gsf_usd": 532,
        "soft_cost_pct_of_hard": 0.15,
        "sale_price_per_unit_usd": {"market": 549_286, "affordable": 280_500},
    },
}
# "Average Price Per SF, Mid- and High-Density Residential" -- Hawai'i
# column. An order of magnitude below Kaua'i's $36 or Maui's $34 -- the
# Ane Keohokalole corridor is a less-developed Kailua-Kona-area site, and
# the source's own land comps reflect that real difference, not a
# modeling choice made here.
HAWAII_ANEKEOHOKALOLE_LAND_PRICE_PER_BUILDING_GSF_USD = 5.5

# county -> (PRO_FORMA_INPUTS dict, land $/building-GSF) for the two newly
# added sites. Financing and developer-profit-margin constants are shared
# across all 4 TOD sites (see note above) and are NOT duplicated per county
# here -- every call below reuses KAUAI_LIHUE_FINANCING /
# KAUAI_LIHUE_DEVELOPER_PROFIT_MARGIN_PCT_OF_SALE directly, despite their
# Kaua'i-specific names, for the same reason oahu_iwilei_kapalama_
# development_fee_reduction_feasibility() already does.
SITE_PRO_FORMA_CONFIG = {
    "maui": (MAUI_KAAHUMANU_PRO_FORMA_INPUTS, MAUI_KAAHUMANU_LAND_PRICE_PER_BUILDING_GSF_USD),
    "hawaii": (HAWAII_ANEKEOHOKALOLE_PRO_FORMA_INPUTS, HAWAII_ANEKEOHOKALOLE_LAND_PRICE_PER_BUILDING_GSF_USD),
}

# Source labels for the "source" string in generic_development_fee_reduction_feasibility()'s
# return value, matching the citation style of kauai_development_fee_reduction_feasibility()/
# oahu_iwilei_kapalama_development_fee_reduction_feasibility() above.
SITE_SOURCE_LABEL = {
    "maui": "Ka'ahumanu Avenue Community Corridor (Maui) column.",
    "hawaii": "Ane Keohokalole Highway Corridor (Hawai'i County) column.",
}


def generic_development_fee_reduction_feasibility(
    county: str,
    typology: str = "lowrise_for_sale",
    affordability: str = "market",
    development_fee_usd: float = STANDARD_DEVELOPMENT_FEE_PER_UNIT_USD,
) -> dict:
    """
    Hawai'i County / Maui analog of kauai_development_fee_reduction_feasibility()
    and oahu_iwilei_kapalama_development_fee_reduction_feasibility() above,
    generalized by `county` (one of SITE_PRO_FORMA_CONFIG's keys) instead of
    copy-pasted per county, since this is the THIRD and FOURTH site using
    the exact same mechanism against the exact same source report. Same
    "excess value" feasibility test (market value minus total development
    cost minus required developer profit -- see docs/MODEL_AND_ASSUMPTIONS.md)
    and the same two explicit simplifications as the Kaua'i/Honolulu
    versions: land cost allocated per building-GSF rather than a true
    site-area/FAR basis; financing cost uses an average-outstanding-balance
    approximation rather than a full monthly draw schedule.
    """
    if county not in SITE_PRO_FORMA_CONFIG:
        raise ValueError(f"county must be one of {list(SITE_PRO_FORMA_CONFIG)}, got {county!r}")
    pro_forma_inputs, land_price_per_gsf = SITE_PRO_FORMA_CONFIG[county]
    inputs = pro_forma_inputs[typology]
    unit_size = inputs["unit_size_gsf"]
    hard_cost = unit_size * inputs["hard_cost_per_gsf_usd"]
    soft_cost = hard_cost * inputs["soft_cost_pct_of_hard"]
    land_cost = unit_size * land_price_per_gsf
    cost_before_financing_and_fee = hard_cost + soft_cost + land_cost

    fin = KAUAI_LIHUE_FINANCING  # shared across all 4 sites in the source table -- see note above
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
        "development_fee_usd": round(development_fee_usd),
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
        + SITE_SOURCE_LABEL[county],
    }


def generic_tract_comps_development_fee_reduction_feasibility(
    county: str,
    sales_joined_csv: Path | None = None,
    typology: str = "lowrise_for_sale",
    affordability: str = "market",
    development_fee_usd: float = STANDARD_DEVELOPMENT_FEE_PER_UNIT_USD,
    min_sales_per_tract: int = 5,
) -> pd.DataFrame:
    """
    Hawai'i County / Maui analog of tract_comps_development_fee_reduction_feasibility()
    (Kaua'i's real-sales-comps version) above -- scales the site's reference
    sale price by REAL observed transaction $/building-sqft per tract
    instead of an ACS median-home-value ratio.

    Hawai'i County's comps are scripts/process_hawaii_county_sales_exports.py's
    output (Regrid vendor export, 2025-01-02 to 2025-05-09 -- a partial year,
    not a full calendar year like Kaua'i/Honolulu's benchmarks, and with no
    arm's-length/valid-sale flag -- see that script's docstring and
    docs/DATA_PROVENANCE.md for both caveats stated plainly, not papered over).

    Maui's comps are scripts/process_maui_sales_exports.py's output (the
    county's own property-records export, with a real "Valid Sale or Other
    Reason" flag already applied upstream).

    Still scales REVENUE only, same stated asymmetry as the Kaua'i version --
    no tract-level land-cost or hard-cost source exists yet, so cost inputs
    stay fixed at each site's own pro-forma table figures everywhere.
    """
    default_sales_paths = {
        "hawaii": [OUT_DIR / "hawaii_sales_joined.csv", OUT_DIR / "hawaii_zillow_sales_joined.csv"],
        "maui": OUT_DIR / "maui_sales_joined.csv",
    }
    path = sales_joined_csv or default_sales_paths[county]
    sales = _load_sales_joined(path)
    # Maui's sales-joined CSV stores GEOID as a float-formatted string
    # ("15009030706.0") -- a real artifact of process_maui_sales_exports.py
    # writing a numeric GEOID column to CSV, confirmed by inspecting the
    # file directly, not assumed. Strip the trailing ".0" so this joins
    # correctly against every other GEOID source in this project (plain
    # 11-digit strings, e.g. census_tracts.json, ACS pulls). Hawai'i
    # County's own sales-joined CSV already writes GEOID as a plain
    # string, so this is a no-op there.
    sales["GEOID"] = sales["GEOID"].str.replace(r"\.0$", "", regex=True)
    cfg = COUNTY_CONFIG[county]
    # Same real bug and same fix as income_bracket_vs_affordable_stock_share()
    # (found 2026-10-28): Maui's raw export has thousands of $0 non-arm's-
    # length transfers already flagged valid_sale=False by the county's own
    # CAMA system, and this filter had no sale_price_usd floor or valid_sale
    # check, so those $0 rows were dragging down the tract median $/sqft this
    # function uses to scale Maui's feasibility-gate revenue -- a real
    # modeling error, not just a display one, since this function's output
    # feeds build_gated_county_features()'s actual ADU/Missing Middle unit
    # counts for Maui.
    valid_col = sales["valid_sale"] if "valid_sale" in sales.columns else True
    usable = sales[
        (sales["building_sqft"] > 0)
        & sales["price_per_sqft"].notna()
        & sales["sale_price_usd"].notna()
        & (sales["sale_price_usd"] > 0)
        & sales["taxclass"].isin(cfg["existing_residential_taxclasses"])
        & sales["GEOID"].notna()
        & valid_col
    ]
    by_tract = usable.groupby("GEOID")["price_per_sqft"].agg(["median", "count"])
    by_tract = by_tract[by_tract["count"] >= min_sales_per_tract]

    pro_forma_inputs, _ = SITE_PRO_FORMA_CONFIG[county]
    base_inputs = pro_forma_inputs[typology]
    reference_sale_price = base_inputs["sale_price_per_unit_usd"][affordability]
    reference_price_per_sqft = reference_sale_price / base_inputs["unit_size_gsf"]

    rows = []
    for geoid, row in by_tract.iterrows():
        scale = row["median"] / reference_price_per_sqft
        scaled_sale_price = reference_sale_price * scale
        original = pro_forma_inputs[typology]
        scaled_inputs = dict(original)
        scaled_inputs["sale_price_per_unit_usd"] = {affordability: scaled_sale_price}
        pro_forma_inputs[typology] = scaled_inputs
        try:
            result = generic_development_fee_reduction_feasibility(county, typology, affordability, development_fee_usd)
        finally:
            pro_forma_inputs[typology] = original
        rows.append(
            {
                "GEOID": geoid,
                "comps_median_price_per_sqft": round(row["median"], 2),
                "comps_sale_count": int(row["count"]),
                "scaled_sale_price_usd": round(scaled_sale_price),
                "excess_value_usd": result["excess_value_usd"],
                "feasible": result["feasible"],
            }
        )
    return pd.DataFrame(rows)


# Zillow Observed Rent Index (ZORI), Hawai'i County / Maui County, Sep 2026
# (latest available at the time these were fetched, 2026-10-27) -- same
# role as KAUAI_COUNTYWIDE_ZORI_USD_PER_MONTH above. Fetched live via
# fetch_data.fetch_zillow_county_timeseries() (the same function already
# used for Kaua'i/Honolulu), not estimated. See docs/DATA_PROVENANCE.md.
HAWAII_COUNTYWIDE_ZORI_USD_PER_MONTH = 2_562.48  # Sep 2026
MAUI_COUNTYWIDE_ZORI_USD_PER_MONTH = 3_207.69  # Sep 2026

# ACS 2022 5-year median gross rent (B25064), tract median of medians --
# same role as KAUAI_COUNTYWIDE_MEDIAN_GROSS_RENT_USD/HONOLULU_COUNTYWIDE_
# MEDIAN_GROSS_RENT_USD above, used by tract_scaled_rental_feasibility()'s
# per-county cfg dict below. Hawai'i County: 43/43 tracts report a value;
# Maui: 42/44.
HAWAII_COUNTYWIDE_MEDIAN_GROSS_RENT_USD = 1_359
MAUI_COUNTYWIDE_MEDIAN_GROSS_RENT_USD = 1_894


def generic_tract_affordability_constrained_capacity(
    county: str,
    income_csv: Path | None = None,
    sales_feasibility_df: pd.DataFrame | None = None,
    tenure: str = "for_sale",
) -> pd.DataFrame:
    """
    Hawai'i County / Maui analog of honolulu_tract_affordability_constrained_capacity()
    above, generalized by `county` rather than copy-pasted a third and
    fourth time. Same mechanism: for `tenure="for_sale"`, required income is
    backed out of the comps-implied price and checked against the tract's
    real ACS B19001 (ALL households) income distribution; for
    `tenure="rental"`, required income is backed out of the county's real
    Zillow ZORI figure and checked against the tract's real ACS B25118
    (RENTER-OCCUPIED-ONLY) income distribution.
    """
    zori_by_county = {"hawaii": HAWAII_COUNTYWIDE_ZORI_USD_PER_MONTH, "maui": MAUI_COUNTYWIDE_ZORI_USD_PER_MONTH}
    rows = []
    if tenure == "for_sale":
        path = income_csv or (RAW_DIR / f"acs_income_distribution_{county}.csv")
        if not path.exists():
            raise RuntimeError(
                f"{path} not found -- run `fetch_data.py --layer acs_income_distribution "
                f"--county {county}` first (needs CENSUS_API_KEY)."
            )
        income = pd.read_csv(path, dtype={"GEOID": str})
        if sales_feasibility_df is None:
            raise ValueError("tenure='for_sale' requires sales_feasibility_df (a comps/ACS feasibility DataFrame).")
        feas = sales_feasibility_df.copy()
        feas["GEOID"] = feas["GEOID"].astype(str)
        merged = income.merge(feas[["GEOID", "scaled_sale_price_usd"]], on="GEOID", how="inner")
        for _, tract in merged.iterrows():
            required_income = _required_income_for_sale_price(tract["scaled_sale_price_usd"])
            bracket_counts = {col: tract[col] for col, _, _ in ACS_B19001_BRACKETS_FOR_MODEL}
            pct = _pct_households_above_income(bracket_counts, tract["total_households"], required_income)
            rows.append(
                {
                    "GEOID": tract["GEOID"],
                    "tenure": tenure,
                    "price_or_rent_usd": round(tract["scaled_sale_price_usd"]),
                    "required_income_usd": round(required_income),
                    "total_households": int(tract["total_households"]),
                    "pct_households_affording": round(pct, 1),
                }
            )
    elif tenure == "rental":
        path = income_csv or (RAW_DIR / f"acs_renter_income_distribution_{county}.csv")
        if not path.exists():
            raise RuntimeError(
                f"{path} not found -- run `fetch_data.py --layer acs_renter_income_distribution "
                f"--county {county}` first (needs CENSUS_API_KEY)."
            )
        renter_income = pd.read_csv(path, dtype={"GEOID": str})
        required_income = _required_income_for_rent(zori_by_county[county])
        for _, tract in renter_income.iterrows():
            if tract["total_renter_households"] <= 0:
                continue
            bracket_counts = {col: tract[col] for col, _, _ in ACS_B25118_RENTER_BRACKETS_FOR_MODEL}
            pct = _pct_households_above_income(
                bracket_counts, tract["total_renter_households"], required_income,
                brackets=ACS_B25118_RENTER_BRACKETS_FOR_MODEL,
            )
            rows.append(
                {
                    "GEOID": tract["GEOID"],
                    "tenure": tenure,
                    "price_or_rent_usd": zori_by_county[county],
                    "required_income_usd": round(required_income),
                    "total_households": int(tract["total_renter_households"]),
                    "pct_households_affording": round(pct, 1),
                }
            )
    else:
        raise ValueError(f"tenure must be 'for_sale' or 'rental', got {tenure!r}")
    return pd.DataFrame(rows)


# Extend tract_scaled_rental_feasibility()'s per-county cfg with Hawai'i
# County and Maui, reusing the generic pro-forma inputs above rather than
# duplicating that function. See its docstring for the full rental-pro-forma
# mechanism (Skyline TOD Feasibility Study rent/cap-rate inputs, no
# developer-profit margin subtracted -- a stated, real gap, not an invented
# substitute).
_TRACT_SCALED_RENTAL_FEASIBILITY_EXTRA_CFG = {
    "maui": {
        "acs_path": RAW_DIR / "acs_commute_density_maui.csv",
        "hard_cost_per_gsf_usd": MAUI_KAAHUMANU_PRO_FORMA_INPUTS["lowrise_for_sale"]["hard_cost_per_gsf_usd"],
        "soft_cost_pct_of_hard": MAUI_KAAHUMANU_PRO_FORMA_INPUTS["lowrise_for_sale"]["soft_cost_pct_of_hard"],
        "land_price_per_gsf_usd": MAUI_KAAHUMANU_LAND_PRICE_PER_BUILDING_GSF_USD,
        "financing": KAUAI_LIHUE_FINANCING,
        "countywide_median_gross_rent_usd": MAUI_COUNTYWIDE_MEDIAN_GROSS_RENT_USD,
    },
    "hawaii": {
        "acs_path": RAW_DIR / "acs_commute_density_hawaii.csv",
        "hard_cost_per_gsf_usd": HAWAII_ANEKEOHOKALOLE_PRO_FORMA_INPUTS["lowrise_for_sale"]["hard_cost_per_gsf_usd"],
        "soft_cost_pct_of_hard": HAWAII_ANEKEOHOKALOLE_PRO_FORMA_INPUTS["lowrise_for_sale"]["soft_cost_pct_of_hard"],
        "land_price_per_gsf_usd": HAWAII_ANEKEOHOKALOLE_LAND_PRICE_PER_BUILDING_GSF_USD,
        "financing": KAUAI_LIHUE_FINANCING,
        "countywide_median_gross_rent_usd": HAWAII_COUNTYWIDE_MEDIAN_GROSS_RENT_USD,
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--county", choices=list(COUNTY_CONFIG), default="kauai", help="Which county to build (default: kauai)"
    )
    parser.add_argument(
        "--min-lot-sqft", type=float, default=None, help="Overrides the county's own confirmed default"
    )
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
