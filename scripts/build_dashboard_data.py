"""
Regenerates data/sample/kauai_dashboard.geojson -- the data embedded in
index.html -- from build_model.py's live output. Run this after any change
to the ADU, Missing Middle, Parking Reduction, or TOD scenarios, or the
development-fee feasibility pro forma, then re-run
`python scripts/embed_dashboard_data.py` to re-inline it into index.html
(see that script's docstring).

Embeds GROSS (pre-absorption) capacity for every scenario, not just the
absorption-adjusted unit count -- this is what lets index.html's
absorption-rate slider recompute units live in the browser
(units = gross_capacity * rate) instead of being locked to whichever rate
was baked in at generation time.

Requires the raw Kauai GIS pulls (fetch_data.py), the ACS commute/density/
home-value layer (fetch_data.py --layer acs_commute_density --county kauai,
needs CENSUS_API_KEY), the Kauai Bus GTFS major-stops layer, and the HDOT
arterial-roads layer (fetch_data.py --layer kauai_bus_major_stops /
kauai_arterial_roads) to already be present in data/raw/.
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import build_model as bm

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
OUT_PATH = Path(__file__).parent.parent / "data" / "sample" / "kauai_dashboard.geojson"


def _zillow_timeseries_for_metadata() -> dict:
    """
    Real monthly history (not just the latest value) of Kauai County's
    Zillow ZORI/ZHVI, for the Existing Conditions "over time" chart --
    feeds the user's 2026-10-15 point that trend data "better reflects
    the urgency of the call to action" than a single snapshot.
    """
    path = RAW_DIR / "zillow_kauai_county_timeseries.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    out = {}
    for series in ("zori", "zhvi"):
        sub = df[df["series"] == series].sort_values("date")
        out[series] = {"dates": sub["date"].tolist(), "values": sub["value"].round(0).tolist()}
    return out


def _sales_monthly_trend_for_metadata() -> list:
    """
    Real month-by-month median $/building-sqft from the user-supplied
    Kauai sales exports (Feb 2025-Jun 2026, 17 months -- pulled to match
    the Zillow ZORI rent series' own history) -- real transactions, not
    interpolated.
    """
    path = Path(__file__).parent.parent / "data" / "processed" / "kauai_sales_2026_joined.csv"
    if not path.exists():
        return []
    df = pd.read_csv(path, dtype={"GEOID": str})
    cfg = bm.COUNTY_CONFIG["kauai"]
    usable = df[
        (df["building_sqft"] > 0)
        & df["price_per_sqft"].notna()
        & df["taxclass"].isin(cfg["existing_residential_taxclasses"])
    ].copy()
    usable["month"] = pd.to_datetime(usable["sale_date"]).dt.to_period("M").astype(str)
    by_month = usable.groupby("month")["price_per_sqft"].agg(["median", "count"]).reset_index()
    return [
        {"month": r["month"], "median_price_per_sqft": round(r["median"], 2), "sales_count": int(r["count"])}
        for _, r in by_month.iterrows()
    ]


def _income_vs_affordable_stock_for_metadata() -> list:
    """
    Wraps bm.income_bracket_vs_affordable_stock_share(): for each real ACS
    B19001 income bracket, the real household share in that bracket next
    to the real share of Kauai's own sales comps priced at or below what
    that bracket's representative income could afford. Feeds the
    Existing Conditions "Household income & the affordability gap"
    clustered-column chart.
    """
    try:
        df = bm.income_bracket_vs_affordable_stock_share()
    except RuntimeError:
        return []
    return df.to_dict("records")


def main() -> None:
    bm.build_county("kauai")
    default_absorption_rate = bm.absorption_rate_benchmark_for("kauai")

    adu_tract, _ = bm.run_adu_scenario("kauai")
    mm_tract, _ = bm.run_missing_middle_scenario("kauai")
    parking_tract, _ = bm.run_parking_reduction_scenario("kauai")
    tod_tract, tod_meta = bm.run_tod_scenario("kauai")
    feas = bm.tract_scaled_development_fee_reduction_feasibility()

    adu_gated = bm.apply_feasibility_gate(adu_tract, feas, "estimated_new_units")
    mm_gated = bm.apply_feasibility_gate(mm_tract, feas, "additional_estimated_units")
    tod_gated = bm.apply_feasibility_gate(tod_tract, feas, "additional_estimated_units")

    # Third gate: of the feasibility-adjusted units, what share could the
    # tract's OWN households actually afford, under standard mortgage/rent
    # underwriting (bm.tract_affordability_constrained_capacity())? ADU and
    # Missing Middle both use feas's for-sale product; TOD uses the single
    # countywide Zillow rent figure (no tract-level TOD-product sale price
    # exists -- it's new multifamily rental, not the townhome-resale
    # product feas prices). See docs/MODEL_AND_ASSUMPTIONS.md's
    # Affordability-Constrained Demand section for the finding this
    # surfaced -- it is NOT a small correction.
    afford_for_sale = bm.tract_affordability_constrained_capacity(sales_feasibility_df=feas, tenure="for_sale")
    afford_rental = bm.tract_affordability_constrained_capacity(tenure="rental")
    adu_gated = bm.apply_affordability_gate(adu_gated, afford_for_sale, "feasibility_adjusted_units")
    mm_gated = bm.apply_affordability_gate(mm_gated, afford_for_sale, "feasibility_adjusted_units")
    tod_gated = bm.apply_affordability_gate(tod_gated, afford_rental, "feasibility_adjusted_units")

    acs = pd.read_csv(RAW_DIR / "acs_commute_density_kauai.csv")
    acs["GEOID"] = acs["GEOID"].astype(str)

    # Real tract -> place-name crosswalk (per the user's 2026-10-15 request:
    # "no one associates a place by tract number"). Hawaii has no
    # incorporated cities, so this is a real spatial join against Census
    # Designated Place boundaries (fetch_data.py --layer census_places),
    # not a hand-typed guess -- see tract_to_place_crosswalk()'s docstring.
    place_by_geoid = {}
    places_path = RAW_DIR / "census_places.json"
    if places_path.exists():
        crosswalk = bm.tract_to_place_crosswalk()
        place_by_geoid = {r["GEOID"]: r["place_name"] for r in crosswalk.to_dict("records")}

    # Existing-conditions baseline data (added 2026-10-15, user request):
    # real income distribution, real sales comps, and the ACS median-rent
    # figure, all already gathered for the scenario math above but never
    # surfaced on their own as "what Kauai actually looks like today" --
    # separate from any policy scenario. Feeds index.html's own
    # "Existing Conditions" tab and docs/EXISTING_CONDITIONS.md.
    income_path = RAW_DIR / "acs_income_distribution_kauai.csv"
    income_by_geoid = {}
    if income_path.exists():
        income_df = pd.read_csv(income_path, dtype={"GEOID": str})
        income_by_geoid = {r["GEOID"]: r for r in income_df.to_dict("records")}

    sales_path = Path(__file__).parent.parent / "data" / "processed" / "kauai_sales_2026_joined.csv"
    sales_by_geoid = {}
    countywide_sales_count = 0
    countywide_median_price_per_sqft = None
    if sales_path.exists():
        sales_df = pd.read_csv(sales_path, dtype={"GEOID": str})
        cfg = bm.COUNTY_CONFIG["kauai"]
        usable = sales_df[
            (sales_df["building_sqft"] > 0)
            & sales_df["price_per_sqft"].notna()
            & sales_df["taxclass"].isin(cfg["existing_residential_taxclasses"])
            & sales_df["GEOID"].notna()
        ]
        countywide_sales_count = int(len(usable))
        countywide_median_price_per_sqft = (
            round(usable["price_per_sqft"].median(), 2) if countywide_sales_count else None
        )
        by_tract = usable.groupby("GEOID")["price_per_sqft"].agg(["median", "count"])
        sales_by_geoid = {
            geoid: {"median_price_per_sqft": round(row["median"], 2), "sales_count": int(row["count"])}
            for geoid, row in by_tract.iterrows()
        }

    tracts = json.loads((RAW_DIR / "census_tracts.json").read_text())["features"]
    tract_by_geoid = {f["properties"]["GEOID"]: f for f in tracts}
    adu_by_geoid = {r["GEOID"]: r for r in adu_gated.to_dict("records")}
    mm_by_geoid = {r["GEOID"]: r for r in mm_gated.to_dict("records")}
    tod_by_geoid = {r["GEOID"]: r for r in tod_gated.to_dict("records")}
    parking_tract = parking_tract.copy()
    parking_tract["GEOID"] = parking_tract["GEOID"].astype(str)
    parking_by_geoid = {
        r["GEOID"]: r for r in parking_tract.to_dict("records") if pd.notna(r.get("GEOID"))
    }
    acs_by_geoid = {r["GEOID"]: r for r in acs.to_dict("records")}

    try:
        from shapely.geometry import mapping, shape
    except ImportError:
        shape = mapping = None

    features = []
    for geoid, feat in tract_by_geoid.items():
        if not any(geoid in d for d in (adu_by_geoid, mm_by_geoid, tod_by_geoid, parking_by_geoid)):
            continue  # water-only/zero-population tracts etc.
        adu_r = adu_by_geoid.get(geoid, {})
        mm_r = mm_by_geoid.get(geoid, {})
        tod_r = tod_by_geoid.get(geoid, {})
        parking_r = parking_by_geoid.get(geoid, {})
        acs_r = acs_by_geoid.get(geoid, {})
        income_r = income_by_geoid.get(geoid, {})
        sales_r = sales_by_geoid.get(geoid, {})
        props = {
            "GEOID": geoid,
            "name": feat["properties"].get("NAME"),
            # Real place name (largest-land-overlap Census Designated Place,
            # or "Rural Kaua'i (unincorporated)") -- what the dashboard's
            # tract/municipality toggle groups by.
            "place_name": place_by_geoid.get(geoid),
            # ADU: gross capacity IS eligible_parcel_count (1 potential unit/parcel).
            "adu_eligible_parcels": adu_r.get("eligible_parcel_count"),
            "adu_gross_capacity": adu_r.get("eligible_parcel_count"),
            "adu_feasible": adu_r.get("feasible"),
            "adu_pct_affording": adu_r.get("pct_households_affording"),
            # Baseline excess_value_usd at the Challenge's $50,000 standardized
            # fee -- the dashboard's development-fee slider recomputes
            # feasible = (excess_value_usd_at_50k + (50000 - slider_fee)) >= 0
            # client-side, since fee changes affect excess_value dollar-for-
            # dollar (kauai_development_fee_reduction_feasibility()'s own
            # documented mechanic) -- no need to re-run the model per drag.
            "adu_excess_value_usd_at_fee50k": adu_r.get("excess_value_usd"),
            # Missing Middle
            "mm_eligible_parcels": mm_r.get("eligible_parcel_count"),
            "mm_gross_capacity": mm_r.get("additional_gross_capacity"),
            "mm_feasible": mm_r.get("feasible"),
            "mm_pct_affording": mm_r.get("pct_households_affording"),
            "mm_excess_value_usd_at_fee50k": mm_r.get("excess_value_usd"),
            # Transit-Oriented Density
            "tod_eligible_parcels": tod_r.get("eligible_parcel_count"),
            # Computed at the statute's base FAR (4.0); the dashboard's FAR
            # slider scales this proportionally client-side rather than
            # re-running the model per drag -- an approximation (it skips
            # re-applying the existing-units floor-at-zero clip per parcel
            # at other FAR values), noted in the UI.
            "tod_gross_capacity_at_far4": tod_r.get("additional_gross_capacity"),
            "tod_feasible": tod_r.get("feasible"),
            "tod_pct_affording": tod_r.get("pct_households_affording"),
            "tod_excess_value_usd_at_fee50k": tod_r.get("excess_value_usd"),
            # Parking Requirement Reduction: does NOT change eligibility or unit
            # count vs. ADU (see run_parking_reduction_scenario docstring) --
            # its own metric is land consumed by the current parking requirement.
            "parking_eligible_parcels": parking_r.get("eligible_parcel_count"),
            "parking_land_consumed_sqft": parking_r.get("parking_land_consumed_sqft_if_required"),
            # ACS context
            "median_home_value_usd": acs_r.get("median_home_value_usd"),
            "median_gross_rent_usd": acs_r.get("median_gross_rent_usd"),
            "pct_alt_mode_commute": acs_r.get("pct_alt_mode_commute"),
            "population": acs_r.get("population"),
            "commuters_excl_wfh": acs_r.get("commuters_excl_wfh"),
            "density_per_sqmi": acs_r.get("density_per_sqmi"),
            # Land area, sq mi -- lets the dashboard compute a REAL
            # place-level density (sum population / sum land area) when
            # grouping tracts by place, rather than averaging per-tract
            # density figures (which isn't how density actually aggregates).
            "area_land_sqmi": (feat["properties"].get("AREALAND") or 0) / 2_589_988.11,
            # Existing Conditions tab: real 2026 sales comps (Jan-Jun,
            # min 5 sales/tract already applied upstream where used for
            # feasibility -- here shown as-is, including thinner tracts,
            # since this is descriptive baseline context, not a feasibility
            # input) and the real ACS B19001 household-income distribution.
            "sales_median_price_per_sqft": sales_r.get("median_price_per_sqft"),
            "sales_count_2026": sales_r.get("sales_count"),
            "total_households": income_r.get("total_households"),
            "income_brackets": {
                col: income_r.get(col) for col, _, _ in bm.ACS_B19001_BRACKETS_FOR_MODEL
            } if income_r else None,
        }
        geometry = feat["geometry"]
        if shape is not None:
            geometry = mapping(shape(geometry).simplify(0.0002, preserve_topology=True))
        features.append({"type": "Feature", "geometry": geometry, "properties": props})

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": features,
                "metadata": {
                    "default_absorption_rate": default_absorption_rate,
                    "tod_far_default": bm.TOD_FAR_BASE,
                    "tod_density_mechanism_note": bm.TOD_DENSITY_SOURCE_NOTE,
                    "affordability_mortgage_rate": bm.HAWAII_MORTGAGE_RATE_30YR_FIXED,
                    "affordability_down_payment_pct": bm.HAWAII_MORTGAGE_DOWN_PAYMENT_PCT,
                    "affordability_income_share": bm.AFFORDABILITY_INCOME_SHARE,
                    "countywide_zori_usd_per_month": bm.KAUAI_COUNTYWIDE_ZORI_USD_PER_MONTH,
                    "zillow_timeseries": _zillow_timeseries_for_metadata(),
                    "sales_monthly_trend": _sales_monthly_trend_for_metadata(),
                    "income_vs_affordable_stock": _income_vs_affordable_stock_for_metadata(),
                    "countywide_median_home_value_usd": bm.KAUAI_COUNTYWIDE_MEDIAN_HOME_VALUE_USD,
                    "countywide_sales_median_price_per_sqft": countywide_median_price_per_sqft,
                    "countywide_sales_count_2026": countywide_sales_count,
                    "existing_conditions_note": (
                        "Baseline context, not a policy scenario: real county sales comps "
                        "(Feb 2025-Jun 2026, user-supplied monthly exports), Zillow's county-level "
                        "rent index (ZORI, zip-level coverage is zero for Kauai -- the rental "
                        "market is too thin), ACS 2022 5-year median home value/rent and "
                        "household-income brackets (B19001). See docs/EXISTING_CONDITIONS.md."
                    ),
                    "development_fee_default_usd": bm.STANDARD_DEVELOPMENT_FEE_PER_UNIT_USD,
                    "development_fee_slider_min_usd": bm.DEVELOPMENT_FEE_SLIDER_MIN_USD,
                    "development_fee_slider_max_usd": bm.DEVELOPMENT_FEE_SLIDER_MAX_USD,
                    "development_fee_source_note": bm.DEVELOPMENT_FEE_SOURCE_NOTE,
                    "affordability_note": (
                        "Of the feasibility-adjusted units, pct_affording is the real share of "
                        "that TRACT's OWN households whose income clears the mortgage/rent bar "
                        "for the comps-implied price (ADU/Missing Middle: 20% down, 7.000% 30-yr "
                        "fixed, HiCentral 2026-10-14; TOD: countywide Zillow ZORI rent), per ACS "
                        "B19001 income brackets -- not a median-based guess. This is a demand-side "
                        "ceiling, separate from and multiplicative with the absorption rate."
                    ),
                },
            }
        )
    )
    print(f"{len(features)} tracts -> {OUT_PATH} (default absorption rate: {default_absorption_rate})")


if __name__ == "__main__":
    main()
