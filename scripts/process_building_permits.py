"""
Processes the user-supplied Census Building Permits Survey (BPS) monthly
export (data/raw/census_bps_monthly_2019_2025.csv -- all 4 Hawaii counties,
Jan 2019-Dec 2025, by structure type) into two things:

1. A refreshed data/reference/absorption_rate_benchmarks.csv that adds a
   genuine PRE-COVID (2019 only) annual rate alongside the existing
   2016-2025/2011-2025 blended FRED-derived averages -- the current file
   only has multi-year averages that already smooth over the 2020-2021
   COVID permitting disruption, so there was no way to isolate a clean
   "before" baseline. This is additive: every existing column is kept,
   so absorption_rate_benchmark_for() and every caller of it are
   unaffected unless/until they're pointed at the new column.
2. data/reference/building_permits_by_type.csv -- annual permits by
   county AND structure type (single-family / 2-unit / 3-4-unit /
   5+-unit), 2019-2025. This is the real evidence behind the Missing
   Middle motivation ("how rare is 2-4 unit construction, really?") and
   is meant to feed the new Assumptions tab's absorption-rate and
   unit-mix explainer charts, not the scenario math itself.

Both outputs are checked into git (small, derived reference tables), same
as the existing absorption_rate_benchmarks.csv -- the raw monthly export
itself stays in data/raw/ like every other county source file.
"""
import sys
from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
REF_DIR = Path(__file__).parent.parent / "data" / "reference"
BPS_PATH = RAW_DIR / "census_bps_monthly_2019_2025.csv"
BENCHMARKS_PATH = REF_DIR / "absorption_rate_benchmarks.csv"
BY_TYPE_PATH = REF_DIR / "building_permits_by_type.csv"

COUNTY_LOCATION_MAP = {
    "HAWAII COUNTY, HI (Hawaii County)": "hawaii_county_bigisland",
    "HONOLULU COUNTY, HI (Honolulu County)": "honolulu_oahu",
    "KAUAI COUNTY, HI (Kauai County)": "kauai_county",
    "MAUI COUNTY, HI (Maui County)": "maui_county",
}

PRE_COVID_YEAR = 2019  # the one full year in this export before the 2020 permitting disruption


def main() -> None:
    if not BPS_PATH.exists():
        raise RuntimeError(f"{BPS_PATH} not found -- place the Census BPS monthly export there first")
    df = pd.read_csv(BPS_PATH)
    df["area"] = df["Location"].map(COUNTY_LOCATION_MAP)
    unmapped = df[df["area"].isna()]["Location"].unique()
    if len(unmapped):
        raise RuntimeError(f"Unrecognized county location(s) in BPS export: {list(unmapped)}")

    # -- By-structure-type annual table (all years, real evidence for the
    # Assumptions tab) --
    by_type = (
        df.groupby(["area", "Year", "Series"])["Permits"].sum().reset_index()
        .rename(columns={"Year": "year", "Series": "structure_type", "Permits": "annual_permits"})
    )
    by_type.to_csv(BY_TYPE_PATH, index=False)

    # -- Pre-COVID (2019) Total Units annual rate, added to the existing
    # benchmarks file as new columns -- existing columns untouched --
    total_2019 = (
        df[(df["Series"] == "Total Units") & (df["Year"] == PRE_COVID_YEAR)]
        .groupby("area")["Permits"].sum()
    )
    benchmarks = pd.read_csv(BENCHMARKS_PATH)
    benchmarks["avg_annual_permits_2019_precovid"] = benchmarks["area"].map(total_2019)
    benchmarks["annual_rate_pct_2019_precovid"] = (
        100 * benchmarks["avg_annual_permits_2019_precovid"] / benchmarks["housing_units_2023"]
    ).round(2)
    benchmarks["decade_equivalent_pct_2019_precovid"] = (
        10 * benchmarks["annual_rate_pct_2019_precovid"]
    ).round(1)
    # Statewide row: 2019 total across all 4 counties, same pattern as the
    # existing statewide row (sum of counties, not a separate FRED pull).
    statewide_2019 = total_2019.sum()
    statewide_mask = benchmarks["area"] == "statewide"
    benchmarks.loc[statewide_mask, "avg_annual_permits_2019_precovid"] = statewide_2019
    benchmarks.loc[statewide_mask, "annual_rate_pct_2019_precovid"] = round(
        100 * statewide_2019 / benchmarks.loc[statewide_mask, "housing_units_2023"].iloc[0], 2
    )
    benchmarks.loc[statewide_mask, "decade_equivalent_pct_2019_precovid"] = round(
        10 * benchmarks.loc[statewide_mask, "annual_rate_pct_2019_precovid"].iloc[0], 1
    )
    benchmarks.to_csv(BENCHMARKS_PATH, index=False)

    print(f"Wrote {BY_TYPE_PATH} ({len(by_type)} rows)")
    print(f"Updated {BENCHMARKS_PATH} with pre-COVID (2019) columns:")
    print(benchmarks[["area", "decade_equivalent_pct_10yr", "decade_equivalent_pct_2019_precovid"]])


if __name__ == "__main__":
    main()
