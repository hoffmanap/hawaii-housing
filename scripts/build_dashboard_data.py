"""
Regenerates data/sample/dashboard.geojson -- the data embedded in
index.html -- from build_model.py's live output. Run this after any change
to the ADU, Missing Middle, Parking Reduction, or TOD scenarios, the
development-fee feasibility pro forma, or the Existing Conditions inputs
for ANY county, then re-run `python scripts/embed_dashboard_data.py` to
re-inline it into index.html (see that script's docstring).

MULTI-COUNTY, 2026-10-15: every feature now carries a "county" property.
The Existing Conditions baseline (rents, sales, income, commute/density)
runs for both Kauai and Honolulu, nested per-county under
metadata.by_county, per the user's decision to "combine with county as a
dimension" rather than a county switcher once a second county had data.

SAME DAY, LATER PASS: all four reform scenarios (ADU, Missing Middle,
Parking Reduction, TOD) now also run for Honolulu -- see
build_honolulu_features()'s docstring for exactly what's real there vs.
Kauai (Honolulu's numbers are gross capacity only, not yet feasibility-
or affordability-gated). Top-level metadata keys are left untouched and
still describe Kauai specifically (the Scenario Explorer reads them
directly for its Kauai-default view), so this remains additive.

Embeds GROSS (pre-absorption) capacity for every scenario, not just the
absorption-adjusted unit count -- this is what lets index.html's
absorption-rate slider recompute units live in the browser
(units = gross_capacity * rate) instead of being locked to whichever rate
was baked in at generation time.
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import build_model as bm

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"
OUT_PATH = Path(__file__).parent.parent / "data" / "sample" / "dashboard.geojson"

COUNTY_LABELS = {"kauai": "Kaua'i", "honolulu": "Honolulu", "hawaii": "Hawai'i County", "maui": "Maui County"}

# TIGERweb county FIPS prefixes for census_tracts.json's GEOID -- used to
# scope the generic census_tracts.json layer (statewide) down to each
# county's own tracts, same as the hardcoded "15007"/"15003" literals
# already used in build_kauai_features()/build_honolulu_features().
COUNTY_TRACT_FIPS_PREFIX = {"kauai": "15007", "honolulu": "15003", "hawaii": "15001", "maui": "15009"}


def _zillow_timeseries_for_metadata(county: str) -> dict:
    path = RAW_DIR / f"zillow_{county}_county_timeseries.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    out = {}
    for series in ("zori", "zhvi"):
        sub = df[df["series"] == series].sort_values("date")
        out[series] = {"dates": sub["date"].tolist(), "values": sub["value"].round(0).tolist()}
    return out


def _sales_monthly_trend_for_metadata(county: str, sales_path: Path, taxclass_filter) -> list:
    if not sales_path.exists():
        return []
    df = pd.read_csv(sales_path, dtype={"GEOID": str})
    usable = df[
        (df["building_sqft"] > 0) & df["price_per_sqft"].notna() & taxclass_filter(df)
    ].copy()
    usable["month"] = pd.to_datetime(usable["sale_date"]).dt.to_period("M").astype(str)
    by_month = usable.groupby("month")["price_per_sqft"].agg(["median", "count"]).reset_index()
    return [
        {"month": r["month"], "median_price_per_sqft": round(r["median"], 2), "sales_count": int(r["count"])}
        for _, r in by_month.iterrows()
    ]


def _income_vs_affordable_stock_for_metadata(county: str) -> list:
    try:
        df = bm.income_bracket_vs_affordable_stock_share(county=county)
    except (RuntimeError, TypeError):
        # TypeError: income_bracket_vs_affordable_stock_share() may not yet
        # accept a county kwarg if it hasn't been generalized -- Honolulu
        # simply gets no affordable-stock comparison series in that case,
        # rather than crashing the whole dashboard build.
        return []
    return df.to_dict("records")


def build_kauai_features() -> tuple[list, dict]:
    """Unchanged from the single-county version of this script -- full
    scenario pipeline (ADU, Missing Middle, Parking, TOD, feasibility,
    affordability gates), still the only county with all four reform
    scenarios wired up."""
    bm.build_county("kauai")
    default_absorption_rate = bm.absorption_rate_benchmark_for("kauai")

    adu_tract, _ = bm.run_adu_scenario("kauai")
    mm_tract, _ = bm.run_missing_middle_scenario("kauai")
    # BASELINE scenario, added 2026-10-16 per the user's direct request
    # to develop "the baseline scenarios based on existing zoned
    # capacity," not just the reform/intervention numbers -- what
    # today's zoning already legally allows beyond what's built, before
    # any Missing Middle upzoning. See run_missing_middle_baseline_tract()'s
    # docstring: this number existed internally (as a == 0 eligibility
    # filter) but was never itself rolled up and reported until now.
    mm_baseline_tract = bm.run_missing_middle_baseline_tract("kauai")
    # Existing Conditions reframe, 2026-10-22 per direct instruction:
    # "existing conditions should be the actual existing conditions based
    # on the current zoned capacity" -- built vs. legally allowed today,
    # no reform, no market data.
    zoned_capacity_tract = bm.existing_zoned_capacity_tract("kauai")
    parking_tract, _ = bm.run_parking_reduction_scenario("kauai")
    # TOD rebuilt 2026-10-15 on the real statutory eligibility test (see
    # build_model.py's TOD comment block) -- "bus" is Kauai's only real
    # transit mode (no rail), capped at the statute's FAR 4.0 base tier.
    tod_tract, tod_meta = bm.run_tod_scenario("kauai", mode="bus")
    feas = bm.tract_scaled_development_fee_reduction_feasibility()
    # Real rental-product feasibility test (added 2026-10-15 per the
    # user's direct request) -- Skyline TOD Feasibility Study rent/cap-
    # rate inputs, tract-scaled by ACS median gross rent.
    tod_rental_feas = bm.tract_scaled_rental_feasibility("kauai")
    # Dual-tenure feasibility test, added 2026-10-15 (next day's work),
    # per the user's direct question: "how do we account for the
    # differences between the feasibility gap of for sale vs rental? If
    # either one is feasible do we just assume it's going to get built?"
    # Every reform's capacity (ADU, Missing Middle, TOD) now competes on
    # BOTH pro formas per tract; combine_dual_tenure_feasibility() takes
    # the max of the two excess values and tags which tenure won, so a
    # unit of capacity is counted under exactly one tenure outcome, never
    # both. Prior to this, TOD was gated on rental only and ADU/Missing
    # Middle on for-sale only, as if a parcel could only ever become one
    # tenure or the other by the type of reform, not by which product
    # actually pencils there -- see build_model.py's
    # combine_dual_tenure_feasibility() docstring for the full reasoning.
    combined_feas = bm.combine_dual_tenure_feasibility(feas, tod_rental_feas)

    adu_gated = bm.apply_feasibility_gate(adu_tract, combined_feas, "estimated_new_units")
    mm_gated = bm.apply_feasibility_gate(mm_tract, combined_feas, "additional_estimated_units")
    tod_gated = bm.apply_feasibility_gate(tod_tract, combined_feas, "additional_estimated_units")

    afford_for_sale = bm.tract_affordability_constrained_capacity(sales_feasibility_df=feas, tenure="for_sale")
    afford_rental = bm.tract_affordability_constrained_capacity(tenure="rental")
    # Routes each tract's feasibility-adjusted units through whichever
    # tenure actually won that tract's feasibility test -- "closing the
    # loop" the user asked for: a for-sale-won tract's units are checked
    # against real owner-household income brackets (ACS B19001), a
    # rental-won tract's against real renter-household income brackets
    # (ACS B25118), never both and never neither.
    adu_gated = bm.apply_dual_tenure_affordability_gate(adu_gated, afford_for_sale, afford_rental, "feasibility_adjusted_units")
    mm_gated = bm.apply_dual_tenure_affordability_gate(mm_gated, afford_for_sale, afford_rental, "feasibility_adjusted_units")
    tod_gated = bm.apply_dual_tenure_affordability_gate(tod_gated, afford_for_sale, afford_rental, "feasibility_adjusted_units")

    acs = pd.read_csv(RAW_DIR / "acs_commute_density_kauai.csv")
    acs["GEOID"] = acs["GEOID"].astype(str)

    place_by_geoid = {}
    places_path = RAW_DIR / "census_places.json"
    if places_path.exists():
        crosswalk = bm.tract_to_place_crosswalk(county="kauai")
        place_by_geoid = {r["GEOID"]: r["place_name"] for r in crosswalk.to_dict("records")}

    income_path = RAW_DIR / "acs_income_distribution_kauai.csv"
    income_by_geoid = {}
    if income_path.exists():
        income_df = pd.read_csv(income_path, dtype={"GEOID": str})
        income_by_geoid = {r["GEOID"]: r for r in income_df.to_dict("records")}

    sales_path = PROCESSED_DIR / "kauai_sales_2026_joined.csv"
    cfg = bm.COUNTY_CONFIG["kauai"]
    taxclass_filter = lambda df: df["taxclass"].isin(cfg["existing_residential_taxclasses"])
    sales_by_geoid = {}
    countywide_sales_count = 0
    countywide_median_price_per_sqft = None
    if sales_path.exists():
        sales_df = pd.read_csv(sales_path, dtype={"GEOID": str})
        usable = sales_df[
            (sales_df["building_sqft"] > 0)
            & sales_df["price_per_sqft"].notna()
            & taxclass_filter(sales_df)
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
    tract_by_geoid = {f["properties"]["GEOID"]: f for f in tracts if f["properties"]["GEOID"].startswith("15007")}
    adu_by_geoid = {r["GEOID"]: r for r in adu_gated.to_dict("records")}
    mm_by_geoid = {r["GEOID"]: r for r in mm_gated.to_dict("records")}
    tod_by_geoid = {r["GEOID"]: r for r in tod_gated.to_dict("records")}
    mm_baseline_tract = mm_baseline_tract.copy()
    mm_baseline_tract["GEOID"] = mm_baseline_tract["GEOID"].astype(str)
    mm_baseline_by_geoid = {r["GEOID"]: r for r in mm_baseline_tract.to_dict("records")}
    zoned_capacity_tract = zoned_capacity_tract.copy()
    zoned_capacity_tract["GEOID"] = zoned_capacity_tract["GEOID"].astype(str)
    zoned_by_geoid = {r["GEOID"]: r for r in zoned_capacity_tract.to_dict("records")}
    parking_tract = parking_tract.copy()
    parking_tract["GEOID"] = parking_tract["GEOID"].astype(str)
    parking_by_geoid = {r["GEOID"]: r for r in parking_tract.to_dict("records") if pd.notna(r.get("GEOID"))}
    acs_by_geoid = {r["GEOID"]: r for r in acs.to_dict("records")}

    try:
        from shapely.geometry import mapping, shape
    except ImportError:
        shape = mapping = None

    features = []
    for geoid, feat in tract_by_geoid.items():
        if not any(geoid in d for d in (adu_by_geoid, mm_by_geoid, tod_by_geoid, parking_by_geoid)):
            continue
        adu_r = adu_by_geoid.get(geoid, {})
        mm_r = mm_by_geoid.get(geoid, {})
        tod_r = tod_by_geoid.get(geoid, {})
        parking_r = parking_by_geoid.get(geoid, {})
        mm_baseline_r = mm_baseline_by_geoid.get(geoid, {})
        zoned_r = zoned_by_geoid.get(geoid, {})
        acs_r = acs_by_geoid.get(geoid, {})
        income_r = income_by_geoid.get(geoid, {})
        sales_r = sales_by_geoid.get(geoid, {})
        props = {
            "GEOID": geoid,
            "county": "kauai",
            "name": feat["properties"].get("NAME"),
            "place_name": place_by_geoid.get(geoid),
            "existing_total_parcel_count": zoned_r.get("total_parcel_count"),
            "existing_residential_parcel_count": zoned_r.get("residential_parcel_count"),
            "existing_units_built": zoned_r.get("existing_units_built"),
            "existing_zoned_capacity_known_parcel_count": zoned_r.get("zoned_capacity_known_parcel_count"),
            "existing_zoned_capacity_total": zoned_r.get("zoned_capacity_total"),
            "existing_zoned_headroom": zoned_r.get("baseline_capacity_gross"),
            "existing_mm_headroom": zoned_r.get("mm_headroom"),
            "existing_adu_headroom": zoned_r.get("adu_headroom"),
            "adu_eligible_parcels": adu_r.get("eligible_parcel_count"),
            "adu_gross_capacity": adu_r.get("eligible_parcel_count"),
            "adu_feasible": adu_r.get("feasible"),
            "adu_pct_affording": adu_r.get("pct_households_affording"),
            "adu_excess_value_usd_at_fee50k": adu_r.get("excess_value_usd"),
            "adu_winning_tenure": adu_r.get("winning_tenure"),
            "mm_eligible_parcels": mm_r.get("eligible_parcel_count"),
            "mm_gross_capacity": mm_r.get("additional_gross_capacity"),
            "mm_feasible": mm_r.get("feasible"),
            "mm_pct_affording": mm_r.get("pct_households_affording"),
            "mm_excess_value_usd_at_fee50k": mm_r.get("excess_value_usd"),
            "mm_winning_tenure": mm_r.get("winning_tenure"),
            "mm_baseline_capacity_estimated_units": mm_baseline_r.get("baseline_capacity_estimated_units"),
            "tod_eligible_parcels": tod_r.get("eligible_parcel_count"),
            "tod_gross_capacity_at_far4": tod_r.get("additional_gross_capacity"),
            "tod_feasible": tod_r.get("feasible"),
            "tod_pct_affording": tod_r.get("pct_households_affording"),
            "tod_excess_value_usd_at_fee50k": tod_r.get("excess_value_usd"),
            "tod_winning_tenure": tod_r.get("winning_tenure"),
            "parking_eligible_parcels": parking_r.get("eligible_parcel_count"),
            "parking_land_consumed_sqft": parking_r.get("parking_land_consumed_sqft_if_required"),
            "median_home_value_usd": acs_r.get("median_home_value_usd"),
            "median_gross_rent_usd": acs_r.get("median_gross_rent_usd"),
            "pct_alt_mode_commute": acs_r.get("pct_alt_mode_commute"),
            "population": acs_r.get("population"),
            "commuters_excl_wfh": acs_r.get("commuters_excl_wfh"),
            "density_per_sqmi": acs_r.get("density_per_sqmi"),
            "area_land_sqmi": (feat["properties"].get("AREALAND") or 0) / 2_589_988.11,
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

    metadata = {
        "default_absorption_rate": default_absorption_rate,
        "tod_density_mechanism_note": bm.TOD_DENSITY_SOURCE_NOTE,
        "tod_bus_transit_stop_note": tod_meta.get("major_transit_stop_definition"),
        "tod_modes_available": ["bus"],  # Kauai has no rail -- see TOD_TRANSIT_MODES_BY_COUNTY
        "affordability_mortgage_rate": bm.HAWAII_MORTGAGE_RATE_30YR_FIXED,
        "affordability_down_payment_pct": bm.HAWAII_MORTGAGE_DOWN_PAYMENT_PCT,
        "affordability_income_share": bm.AFFORDABILITY_INCOME_SHARE,
        "countywide_zori_usd_per_month": bm.KAUAI_COUNTYWIDE_ZORI_USD_PER_MONTH,
        "zillow_timeseries": _zillow_timeseries_for_metadata("kauai"),
        "sales_monthly_trend": _sales_monthly_trend_for_metadata("kauai", sales_path, taxclass_filter),
        "income_vs_affordable_stock": _income_vs_affordable_stock_for_metadata("kauai"),
        # Wired in 2026-10-16 (previously computed but never reached the
        # dashboard) -- the quantified parking-reduction estimate behind
        # the Commute & Density chart's headline.
        "parking_reform_context": bm.kauai_parking_reform_context(),
        "absorption_rate_benchmark_detail": bm.absorption_rate_benchmark_detail("kauai"),
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
            "for the comps-implied price (for_sale-won tracts: 20% down, 7.000% 30-yr "
            "fixed, HiCentral 2026-10-14, per ACS B19001; rental-won tracts: countywide "
            "ZORI rent, per ACS B25118 renter-occupied households) -- not a median-based "
            "guess. This is a demand-side ceiling, separate from and multiplicative with "
            "the absorption rate."
        ),
        "tenure_split_note": (
            "Added 2026-10-15: every reform's capacity now competes on BOTH a for-sale "
            "and a rental pro forma per tract; combine_dual_tenure_feasibility() takes "
            "the max excess value and tags which tenure wins, so a unit of capacity is "
            "counted under exactly one tenure, never both. affordability_adjusted_units "
            "is split into _for_sale and _rental totals below, by reform, so impact can "
            "be reported by which households actually benefit (owner-household income "
            "band vs. renter-household income band), not one blended figure."
        ),
        "adu_affordability_adjusted_units_for_sale": round(adu_gated["affordability_adjusted_units_for_sale"].sum(), 1),
        "adu_affordability_adjusted_units_rental": round(adu_gated["affordability_adjusted_units_rental"].sum(), 1),
        "mm_affordability_adjusted_units_for_sale": round(mm_gated["affordability_adjusted_units_for_sale"].sum(), 1),
        "mm_affordability_adjusted_units_rental": round(mm_gated["affordability_adjusted_units_rental"].sum(), 1),
        "tod_affordability_adjusted_units_for_sale": round(tod_gated["affordability_adjusted_units_for_sale"].sum(), 1),
        "tod_affordability_adjusted_units_rental": round(tod_gated["affordability_adjusted_units_rental"].sum(), 1),
        "baseline_scenario_note": (
            "Added 2026-10-16: ADU's reported number already IS the baseline -- current Kauai "
            "code's own 2-ADU-per-lot mandate (Act 39/SB3202), not a hypothetical reform (see the "
            "legislation section above). Missing Middle's baseline is now reported separately too: "
            "mm_baseline_capacity_estimated_units is what today's zoning already legally allows "
            "beyond what's built, BEFORE any Missing Middle upzoning -- previously computed "
            "internally (as an eligibility filter) but never itself rolled up and shown. TOD's own "
            "additional_estimated_units now nets out this same existing-zoned-capacity baseline "
            "(whichever is larger of what's built or what current zoning already permits), not just "
            "what's physically built today, fixing a real understatement of TOD's true baseline."
        ),
        "mm_baseline_capacity_estimated_units_total": round(mm_baseline_tract["baseline_capacity_estimated_units"].sum(), 1),
        "existing_conditions_summary": {
            "total_parcel_count": int(zoned_capacity_tract["total_parcel_count"].sum()),
            "residential_parcel_count": int(zoned_capacity_tract["residential_parcel_count"].sum()),
            "existing_units_built": int(zoned_capacity_tract["existing_units_built"].sum()),
            "zoned_capacity_known_parcel_count": int(zoned_capacity_tract["zoned_capacity_known_parcel_count"].sum()),
            "zoned_capacity_total": int(zoned_capacity_tract["zoned_capacity_total"].sum()),
            "zoned_headroom": int(zoned_capacity_tract["baseline_capacity_gross"].sum()),
            "mm_headroom": int(zoned_capacity_tract["mm_headroom"].sum()),
            "adu_headroom": int(zoned_capacity_tract["adu_headroom"].sum()),
        },
    }
    return features, metadata


def build_honolulu_features() -> tuple[list, dict]:
    """
    All four reform scenarios now run for Honolulu -- ADU, Missing
    Middle, Parking Reduction, and TOD (2026-10-15: Missing Middle keyed
    off the confirmed ROH Table 21-5.1 single-family-district set rather
    than Kauai's density-per-acre rule; TOD keyed off real HART rail
    station geometry rather than a bus-stop proxy -- see
    run_missing_middle_baseline()/honolulu_tod_eligible_parcels() in
    build_model.py).

    **Update, 2026-10-15 (same day, later pass):** ADU, Missing Middle,
    and both TOD modes now also run through a real Honolulu-specific
    feasibility and affordability gate. ADU/Missing Middle use
    tract_scaled_oahu_development_fee_reduction_feasibility() (the real
    Iwilei-Kapalama O'ahu for-sale pro forma already sourced in
    build_model.py, tract-scaled by each tract's own ACS median home
    value, same mechanism as Kauai's gate). **Update, same day, final
    pass:** TOD (both modes) now uses its own rental feasibility test,
    tract_scaled_rental_feasibility() -- real Skyline TOD Feasibility
    Study rent/vacancy/opex/cap-rate inputs, tract-scaled by ACS median
    GROSS RENT rather than home value -- since TOD models a rental
    apartment product, not the for-sale product ADU/Missing Middle do.
    Both feasibility functions pair with
    honolulu_tract_affordability_constrained_capacity() (same required-
    income-vs-ACS-B19001-bracket mechanism, using Honolulu's own ZORI
    figure for TOD's rental product). Parking Reduction is NOT gated,
    same as Kauai -- it reports land freed, not a unit count. These are
    real, previously-sourced-but-unwired inputs, not new figures invented
    for this pass.
    """
    acs_path = RAW_DIR / "acs_commute_density_honolulu.csv"
    if not acs_path.exists():
        return [], {}
    acs = pd.read_csv(acs_path, dtype={"GEOID": str})

    bm.build_county("honolulu")
    default_absorption_rate = bm.absorption_rate_benchmark_for("honolulu")
    adu_tract, _ = bm.run_adu_scenario("honolulu")
    mm_tract, _ = bm.run_missing_middle_scenario("honolulu")
    # BASELINE scenario, added 2026-10-16 -- see the matching Kauai
    # comment above. Honolulu's number is 0 everywhere under the
    # coarser label-based baseline method (no sourced density-per-acre
    # rule for Honolulu's R-districts -- see run_missing_middle_baseline()'s
    # docstring), which is a real, stated limitation, not a bug: it
    # means this project doesn't yet have the data to say how much
    # EXTRA headroom Honolulu's current zoning allows beyond single-
    # family, only whether a parcel is single-family-restricted at all.
    mm_baseline_tract = bm.run_missing_middle_baseline_tract("honolulu")
    zoned_capacity_tract = bm.existing_zoned_capacity_tract("honolulu")
    parking_tract, _ = bm.run_parking_reduction_scenario("honolulu")
    # TOD rebuilt 2026-10-15 on the real statutory eligibility test --
    # Honolulu has BOTH real transit modes (TheBus + Skyline rail, same
    # combined GTFS feed), so both are run: "bus" caps at the statute's
    # FAR 4.0 base tier everywhere; "rail" is the one LPA project in
    # Hawaii and reaches the 6.0/7.0 near-station tiers. The dashboard's
    # mode toggle switches which of these two result sets it displays.
    tod_bus_tract, tod_bus_meta = bm.run_tod_scenario("honolulu", mode="bus")
    tod_rail_tract, tod_rail_meta = bm.run_tod_scenario("honolulu", mode="rail")

    # Honolulu feasibility/affordability gate -- see docstring update above.
    # development_fee_usd pinned to the Challenge's $50,000 standardized
    # default (not this function's own Honolulu-DPP-based default) so the
    # dashboard's fee slider -- which recomputes feasibility client-side as
    # baseline_excess_value + (defaultFeeUsd - sliderFeeUsd) -- has a
    # consistent baseline to adjust from, same as Kauai's gate.
    honolulu_feas = bm.tract_scaled_oahu_development_fee_reduction_feasibility(
        development_fee_usd=bm.STANDARD_DEVELOPMENT_FEE_PER_UNIT_USD
    )
    # TOD models a rental apartment product (815 sqft unit size, mode-
    # agnostic FAR standard -- never a for-sale townhome/condo), so it
    # gets its own rental feasibility test (added 2026-10-15 per the
    # user's direct request), not the for-sale pro forma ADU/Missing
    # Middle use. This is also the real fix behind the earlier "0 of 34
    # rail-eligible tracts feasible" finding: that was the for-sale
    # product's cost structure (calibrated to Honolulu's priciest
    # neighborhoods) applied to a corridor that doesn't reach that price
    # tier -- the rental gate below uses real rent-based revenue instead.
    honolulu_tod_rental_feas = bm.tract_scaled_rental_feasibility(
        "honolulu", development_fee_usd=bm.STANDARD_DEVELOPMENT_FEE_PER_UNIT_USD
    )
    # Dual-tenure feasibility test, added 2026-10-15 (next day's work),
    # per the user's direct question: "how do we account for the
    # differences between the feasibility gap of for sale vs rental? If
    # either one is feasible do we just assume it's going to get built?"
    # Every reform's capacity (ADU, Missing Middle, both TOD modes) now
    # competes on BOTH pro formas per tract; combine_dual_tenure_
    # feasibility() takes the max excess value and tags which tenure
    # won, so a unit of capacity is counted under exactly one tenure
    # outcome, never both -- see build_model.py's docstring for the full
    # reasoning.
    honolulu_combined_feas = bm.combine_dual_tenure_feasibility(honolulu_feas, honolulu_tod_rental_feas)

    adu_tract = bm.apply_feasibility_gate(adu_tract, honolulu_combined_feas, "estimated_new_units")
    mm_tract = bm.apply_feasibility_gate(mm_tract, honolulu_combined_feas, "additional_estimated_units")
    tod_bus_tract = bm.apply_feasibility_gate(tod_bus_tract, honolulu_combined_feas, "additional_estimated_units")
    tod_rail_tract = bm.apply_feasibility_gate(tod_rail_tract, honolulu_combined_feas, "additional_estimated_units")

    honolulu_afford_for_sale = bm.honolulu_tract_affordability_constrained_capacity(
        sales_feasibility_df=honolulu_feas, tenure="for_sale"
    )
    honolulu_afford_rental = bm.honolulu_tract_affordability_constrained_capacity(tenure="rental")
    # Routes each tract's feasibility-adjusted units through whichever
    # tenure actually won that tract's feasibility test -- a for-sale-won
    # tract's units are checked against real owner-household income
    # brackets (ACS B19001), a rental-won tract's against real renter-
    # household income brackets (ACS B25118), never both and never
    # neither.
    adu_tract = bm.apply_dual_tenure_affordability_gate(adu_tract, honolulu_afford_for_sale, honolulu_afford_rental, "feasibility_adjusted_units")
    mm_tract = bm.apply_dual_tenure_affordability_gate(mm_tract, honolulu_afford_for_sale, honolulu_afford_rental, "feasibility_adjusted_units")
    tod_bus_tract = bm.apply_dual_tenure_affordability_gate(tod_bus_tract, honolulu_afford_for_sale, honolulu_afford_rental, "feasibility_adjusted_units")
    tod_rail_tract = bm.apply_dual_tenure_affordability_gate(tod_rail_tract, honolulu_afford_for_sale, honolulu_afford_rental, "feasibility_adjusted_units")

    adu_tract = adu_tract.copy(); adu_tract["GEOID"] = adu_tract["GEOID"].astype(str)
    mm_tract = mm_tract.copy(); mm_tract["GEOID"] = mm_tract["GEOID"].astype(str)
    parking_tract = parking_tract.copy(); parking_tract["GEOID"] = parking_tract["GEOID"].astype(str)
    tod_bus_tract = tod_bus_tract.copy(); tod_bus_tract["GEOID"] = tod_bus_tract["GEOID"].astype(str)
    tod_rail_tract = tod_rail_tract.copy(); tod_rail_tract["GEOID"] = tod_rail_tract["GEOID"].astype(str)
    adu_by_geoid = {r["GEOID"]: r for r in adu_tract.to_dict("records")}
    mm_by_geoid = {r["GEOID"]: r for r in mm_tract.to_dict("records")}
    parking_by_geoid = {r["GEOID"]: r for r in parking_tract.to_dict("records")}
    tod_bus_by_geoid = {r["GEOID"]: r for r in tod_bus_tract.to_dict("records")}
    tod_rail_by_geoid = {r["GEOID"]: r for r in tod_rail_tract.to_dict("records")}
    mm_baseline_tract = mm_baseline_tract.copy()
    mm_baseline_tract["GEOID"] = mm_baseline_tract["GEOID"].astype(str)
    mm_baseline_by_geoid = {r["GEOID"]: r for r in mm_baseline_tract.to_dict("records")}
    zoned_capacity_tract = zoned_capacity_tract.copy()
    zoned_capacity_tract["GEOID"] = zoned_capacity_tract["GEOID"].astype(str)
    zoned_by_geoid = {r["GEOID"]: r for r in zoned_capacity_tract.to_dict("records")}
    # Honolulu's enacted TOD FAR overlay (HB1409/Act 159) -- surfaced
    # separately from zoned_capacity_total, not folded in. See
    # honolulu_tod_overlay_capacity_tract()'s docstring for exactly why:
    # it's enacted law like the ADU mandate, but its eligibility
    # footprint is this project's own documented unresolved finding.
    tod_overlay_tract = bm.honolulu_tod_overlay_capacity_tract()
    tod_overlay_tract["GEOID"] = tod_overlay_tract["GEOID"].astype(str)
    tod_overlay_by_geoid = {r["GEOID"]: r for r in tod_overlay_tract.to_dict("records")}

    place_by_geoid = {}
    places_path = RAW_DIR / "census_places.json"
    if places_path.exists():
        crosswalk = bm.tract_to_place_crosswalk(county="honolulu")
        place_by_geoid = {r["GEOID"]: r["place_name"] for r in crosswalk.to_dict("records")}

    income_path = RAW_DIR / "acs_income_distribution_honolulu.csv"
    income_by_geoid = {}
    if income_path.exists():
        income_df = pd.read_csv(income_path, dtype={"GEOID": str})
        income_by_geoid = {r["GEOID"]: r for r in income_df.to_dict("records")}

    sales_path = PROCESSED_DIR / "honolulu_sales_joined.csv"
    taxclass_filter = lambda df: df["parcel_class"].eq("RESIDENTIAL") & df["sale_valid"].str.startswith("Valid")
    sales_by_geoid = {}
    countywide_sales_count = 0
    countywide_median_price_per_sqft = None
    if sales_path.exists():
        sales_df = pd.read_csv(sales_path, dtype={"GEOID": str})
        usable = sales_df[
            (sales_df["building_sqft"] > 0)
            & sales_df["price_per_sqft"].notna()
            & taxclass_filter(sales_df)
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
    tract_by_geoid = {f["properties"]["GEOID"]: f for f in tracts if f["properties"]["GEOID"].startswith("15003")}
    acs_by_geoid = {r["GEOID"]: r for r in acs.to_dict("records")}

    try:
        from shapely.geometry import mapping, shape
    except ImportError:
        shape = mapping = None

    features = []
    for geoid, acs_r in acs_by_geoid.items():
        feat = tract_by_geoid.get(geoid)
        if feat is None:
            continue
        income_r = income_by_geoid.get(geoid, {})
        sales_r = sales_by_geoid.get(geoid, {})
        adu_r = adu_by_geoid.get(geoid, {})
        mm_r = mm_by_geoid.get(geoid, {})
        parking_r = parking_by_geoid.get(geoid, {})
        tod_bus_r = tod_bus_by_geoid.get(geoid, {})
        tod_rail_r = tod_rail_by_geoid.get(geoid, {})
        mm_baseline_r = mm_baseline_by_geoid.get(geoid, {})
        zoned_r = zoned_by_geoid.get(geoid, {})
        tod_overlay_r = tod_overlay_by_geoid.get(geoid, {})
        props = {
            "GEOID": geoid,
            "county": "honolulu",
            "name": feat["properties"].get("NAME"),
            "place_name": place_by_geoid.get(geoid),
            "existing_total_parcel_count": zoned_r.get("total_parcel_count"),
            "existing_residential_parcel_count": zoned_r.get("residential_parcel_count"),
            "existing_units_built": zoned_r.get("existing_units_built"),
            "existing_zoned_capacity_known_parcel_count": zoned_r.get("zoned_capacity_known_parcel_count"),
            "existing_zoned_capacity_total": zoned_r.get("zoned_capacity_total"),
            "existing_zoned_headroom": zoned_r.get("baseline_capacity_gross"),
            "existing_mm_headroom": zoned_r.get("mm_headroom"),
            "existing_adu_headroom": zoned_r.get("adu_headroom"),
            "existing_tod_overlay_capacity_gross": tod_overlay_r.get("tod_overlay_capacity_gross"),
            "existing_tod_overlay_eligible_parcels": tod_overlay_r.get("eligible_parcel_count"),
            "existing_mm_headroom": zoned_r.get("mm_headroom"),
            "existing_adu_headroom": zoned_r.get("adu_headroom"),
            # Now gated through a real Honolulu-specific feasibility/
            # affordability pipeline (added 2026-10-15 -- see this
            # function's docstring update). TOD has two real modes for
            # Honolulu (bus/rail, see run_tod_scenario() in
            # build_model.py) -- both reported, the Scenario Explorer's
            # mode toggle picks which to display.
            "adu_eligible_parcels": adu_r.get("eligible_parcel_count"),
            "adu_gross_capacity": adu_r.get("eligible_parcel_count"),
            "adu_feasible": adu_r.get("feasible"),
            "adu_pct_affording": adu_r.get("pct_households_affording"),
            "adu_excess_value_usd_at_fee50k": adu_r.get("excess_value_usd"),
            "adu_winning_tenure": adu_r.get("winning_tenure"),
            "mm_eligible_parcels": mm_r.get("eligible_parcel_count"),
            "mm_gross_capacity": mm_r.get("additional_gross_capacity"),
            "mm_feasible": mm_r.get("feasible"),
            "mm_pct_affording": mm_r.get("pct_households_affording"),
            "mm_excess_value_usd_at_fee50k": mm_r.get("excess_value_usd"),
            "mm_winning_tenure": mm_r.get("winning_tenure"),
            "mm_baseline_capacity_estimated_units": mm_baseline_r.get("baseline_capacity_estimated_units"),
            "tod_bus_eligible_parcels": tod_bus_r.get("eligible_parcel_count"),
            "tod_bus_gross_capacity": tod_bus_r.get("additional_gross_capacity"),
            "tod_bus_feasible": tod_bus_r.get("feasible"),
            "tod_bus_pct_affording": tod_bus_r.get("pct_households_affording"),
            "tod_bus_excess_value_usd_at_fee50k": tod_bus_r.get("excess_value_usd"),
            "tod_bus_winning_tenure": tod_bus_r.get("winning_tenure"),
            "tod_rail_eligible_parcels": tod_rail_r.get("eligible_parcel_count"),
            "tod_rail_gross_capacity": tod_rail_r.get("additional_gross_capacity"),
            "tod_rail_feasible": tod_rail_r.get("feasible"),
            "tod_rail_pct_affording": tod_rail_r.get("pct_households_affording"),
            "tod_rail_excess_value_usd_at_fee50k": tod_rail_r.get("excess_value_usd"),
            "tod_rail_winning_tenure": tod_rail_r.get("winning_tenure"),
            "parking_eligible_parcels": parking_r.get("eligible_parcel_count"),
            "parking_land_consumed_sqft": parking_r.get("parking_land_consumed_sqft_if_required"),
            "median_home_value_usd": acs_r.get("median_home_value_usd"),
            "median_gross_rent_usd": acs_r.get("median_gross_rent_usd"),
            "pct_alt_mode_commute": acs_r.get("pct_alt_mode_commute"),
            "population": acs_r.get("population"),
            "commuters_excl_wfh": acs_r.get("commuters_excl_wfh"),
            "density_per_sqmi": acs_r.get("density_per_sqmi"),
            "area_land_sqmi": (feat["properties"].get("AREALAND") or 0) / 2_589_988.11,
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

    metadata = {
        "countywide_zori_usd_per_month": 3_019.72,  # Zillow ZORI, Honolulu County, Aug 2026 -- see EXISTING_CONDITIONS.md
        "zillow_timeseries": _zillow_timeseries_for_metadata("honolulu"),
        "sales_monthly_trend": _sales_monthly_trend_for_metadata("honolulu", sales_path, taxclass_filter),
        "income_vs_affordable_stock": _income_vs_affordable_stock_for_metadata("honolulu"),
        "parking_reform_context": bm.honolulu_parking_reform_context(),
        "absorption_rate_benchmark_detail": bm.absorption_rate_benchmark_detail("honolulu"),
        "countywide_median_home_value_usd": 809_550,  # tract median of medians, ACS B25077 -- see EXISTING_CONDITIONS.md
        "countywide_sales_median_price_per_sqft": countywide_median_price_per_sqft,
        "countywide_sales_count_2026": countywide_sales_count,
        "default_absorption_rate": default_absorption_rate,
        "tod_density_mechanism_note": bm.TOD_DENSITY_SOURCE_NOTE,
        "tod_bus_transit_stop_note": tod_bus_meta.get("major_transit_stop_definition"),
        "tod_rail_transit_stop_note": tod_rail_meta.get("major_transit_stop_definition"),
        "tod_modes_available": ["bus", "rail"],
        "scenario_gates_note": (
            "ADU/Missing Middle/TOD (bus and rail) now run through a real Honolulu-specific "
            "feasibility gate (tract_scaled_oahu_development_fee_reduction_feasibility() -- the "
            "Iwilei-Kapalama O'ahu pro forma, tract-scaled by ACS median home value) and "
            "affordability gate (honolulu_tract_affordability_constrained_capacity() -- ACS "
            "B19001 income brackets vs. the required income for the comps-implied for-sale "
            "price or, for the rental pro forma, Honolulu's own ZORI rent), added 2026-10-15. "
            "Parking Reduction is NOT gated, same as Kauai -- it reports land freed, not a unit "
            "count, so there's no unit figure to gate."
        ),
        "tenure_split_note": (
            "Added 2026-10-15 (next day's work): every reform's capacity now competes on BOTH "
            "a for-sale and a rental pro forma per tract; combine_dual_tenure_feasibility() "
            "takes the max excess value and tags which tenure wins, so a unit of capacity is "
            "counted under exactly one tenure, never both. affordability_adjusted_units is "
            "split into _for_sale and _rental totals below, by reform, so impact can be "
            "reported by which households actually benefit (owner-household income band vs. "
            "renter-household income band), not one blended figure."
        ),
        "adu_affordability_adjusted_units_for_sale": round(adu_tract["affordability_adjusted_units_for_sale"].sum(), 1),
        "adu_affordability_adjusted_units_rental": round(adu_tract["affordability_adjusted_units_rental"].sum(), 1),
        "mm_affordability_adjusted_units_for_sale": round(mm_tract["affordability_adjusted_units_for_sale"].sum(), 1),
        "mm_affordability_adjusted_units_rental": round(mm_tract["affordability_adjusted_units_rental"].sum(), 1),
        "tod_bus_affordability_adjusted_units_for_sale": round(tod_bus_tract["affordability_adjusted_units_for_sale"].sum(), 1),
        "tod_bus_affordability_adjusted_units_rental": round(tod_bus_tract["affordability_adjusted_units_rental"].sum(), 1),
        "tod_rail_affordability_adjusted_units_for_sale": round(tod_rail_tract["affordability_adjusted_units_for_sale"].sum(), 1),
        "tod_rail_affordability_adjusted_units_rental": round(tod_rail_tract["affordability_adjusted_units_rental"].sum(), 1),
        "baseline_scenario_note": (
            "Added 2026-10-16: ADU's reported number already IS the baseline -- current law's own "
            "2-ADU-per-lot mandate (Act 39/SB3202), not a hypothetical reform. Missing Middle's "
            "baseline capacity under EXISTING zoning is 0 everywhere for Honolulu -- a real, stated "
            "data limitation (no sourced density-per-acre rule for Honolulu's R-districts), not a "
            "finding that no headroom exists. TOD's additional_estimated_units now nets out the "
            "true existing-zoned-capacity baseline (whichever is larger of what's built or what "
            "current zoning permits via max_units_by_density), not just what's physically built -- "
            "though for Honolulu specifically this has no effect, since the same missing density-"
            "per-acre data means max_units_by_density is 0 for every Honolulu parcel too."
        ),
        "mm_baseline_capacity_estimated_units_total": round(mm_baseline_tract["baseline_capacity_estimated_units"].sum(), 1),
        "existing_conditions_summary": {
            "total_parcel_count": int(zoned_capacity_tract["total_parcel_count"].sum()),
            "residential_parcel_count": int(zoned_capacity_tract["residential_parcel_count"].sum()),
            "existing_units_built": int(zoned_capacity_tract["existing_units_built"].sum()),
            "zoned_capacity_known_parcel_count": int(zoned_capacity_tract["zoned_capacity_known_parcel_count"].sum()),
            "zoned_capacity_total": int(zoned_capacity_tract["zoned_capacity_total"].sum()),
            "zoned_headroom": int(zoned_capacity_tract["baseline_capacity_gross"].sum()),
            "mm_headroom": int(zoned_capacity_tract["mm_headroom"].sum()),
            "adu_headroom": int(zoned_capacity_tract["adu_headroom"].sum()),
        },
        # Enacted law (Honolulu's 2016-2017 TOD Neighborhood Plan rezoning),
        # but NOT in existing_conditions_summary above -- see
        # honolulu_tod_overlay_capacity_tract()'s docstring. Surfaced here,
        # separately, for Existing Conditions to show as its own flagged
        # card rather than silently included or silently omitted, since
        # it's a distinct enacted mechanism from Act 39's ADU mandate
        # already folded into zoned_capacity_total.
        "tod_overlay_summary": {
            "eligible_parcel_count": int(tod_overlay_tract["eligible_parcel_count"].sum()),
            "capacity_gross": int(round(tod_overlay_tract["tod_overlay_capacity_gross"].sum())),
            "mode": "rail",
            "note": (
                "Honolulu's real, ADOPTED TOD Neighborhood Plan rezoning (City Council ordinances "
                "reclassifying parcels near rail stations into TOD-specific zoning districts -- AMX, "
                "BMX, IMX) -- enacted law, like the ADU mandate above. REBUILT 2026-10-22 on real "
                "adopted rezoning polygons and the actual ROH Chapter 21 FAR tables for each TOD zoning "
                "code (replacing an earlier distance-buffer estimate), so this figure is no longer an "
                "upper-bound guess. Kept as its own separate line rather than folded into "
                "zoned_capacity_total because it's a distinct enacted mechanism (city rezoning, not the "
                "ADU mandate) -- see docs/DATA_PROVENANCE.md."
            ),
        },
    }
    return features, metadata


def build_light_county_features(county: str) -> tuple[list, dict]:
    """Hawai'i County and Maui, added to the model pipeline 2026-10-24 (see
    docs/MODEL_AND_ASSUMPTIONS.md and docs/DATA_PROVENANCE.md) but without
    the TOD transit layer, parking-reform study, sales-comps/ACS-scaled
    feasibility pro forma, or ACS income/commute/rent data that Kaua'i and
    Honolulu have -- none of those exist for these two counties yet. This
    function therefore surfaces only what's real and built: Baseline
    (zoned capacity), Missing Middle, and ADU/Ohana, UNGATED (no
    feasibility or affordability adjustment -- there is no pro forma to
    gate them against). It deliberately does NOT add TOD/parking fields,
    an existing_tod_overlay_* field, a *_feasible/*_pct_affording/
    *_winning_tenure field, or an ACS-based metadata block -- adding
    those as null/0 would silently imply they were considered and found
    to be zero, which is false; leaving them out is the honest
    representation of "not modeled yet."

    Returned features intentionally do NOT get added to the dashboard's
    default Scenario Explorer county set (index.html's
    state.scenarioCounties) -- that view's TOD/parking/feasibility
    rendering assumes fields these two counties don't have. They DO
    appear in the Existing Conditions tab, which already renders any
    by_county entry generically (see renderConditionsTab()'s
    activeCounties/scatterDatasets, keyed off whatever's actually present
    in metadata.by_county -- no county-specific JS change was needed for
    that tab to pick these two up).
    """
    bm.build_county(county)
    default_absorption_rate = bm.absorption_rate_benchmark_for(county)

    adu_tract, _ = bm.run_adu_scenario(county)
    mm_tract, _ = bm.run_missing_middle_scenario(county)
    mm_baseline_tract = bm.run_missing_middle_baseline_tract(county)
    zoned_capacity_tract = bm.existing_zoned_capacity_tract(county)

    adu_tract = adu_tract.copy(); adu_tract["GEOID"] = adu_tract["GEOID"].astype(str)
    mm_tract = mm_tract.copy(); mm_tract["GEOID"] = mm_tract["GEOID"].astype(str)
    mm_baseline_tract = mm_baseline_tract.copy(); mm_baseline_tract["GEOID"] = mm_baseline_tract["GEOID"].astype(str)
    zoned_capacity_tract = zoned_capacity_tract.copy(); zoned_capacity_tract["GEOID"] = zoned_capacity_tract["GEOID"].astype(str)

    adu_by_geoid = {r["GEOID"]: r for r in adu_tract.to_dict("records")}
    mm_by_geoid = {r["GEOID"]: r for r in mm_tract.to_dict("records")}
    mm_baseline_by_geoid = {r["GEOID"]: r for r in mm_baseline_tract.to_dict("records")}
    zoned_by_geoid = {r["GEOID"]: r for r in zoned_capacity_tract.to_dict("records")}

    fips_prefix = COUNTY_TRACT_FIPS_PREFIX[county]
    tracts = json.loads((RAW_DIR / "census_tracts.json").read_text())["features"]
    tract_by_geoid = {f["properties"]["GEOID"]: f for f in tracts if f["properties"]["GEOID"].startswith(fips_prefix)}

    # Real tract -> CDP place-name crosswalk (added 2026-10-27 -- same
    # land-area-overlap join already used for Kaua'i/Honolulu,
    # bm.tract_to_place_crosswalk() was already generic by `county`, just
    # never called here). Without this, index.html's "by place" view had
    # no place_name to group by for either county and silently collapsed
    # ALL of a county's tracts into one undefined-key group -- a single,
    # grossly oversized bubble that blew out the scatter chart's shared
    # axis range for every other county shown alongside it. A tract with
    # no CDP overlap gets COUNTY_RURAL_LABEL's real "Rural <county>
    # (unincorporated)" label, same as Kaua'i/Honolulu, not left ungrouped.
    place_by_geoid = {}
    if (RAW_DIR / "census_places.json").exists():
        crosswalk = bm.tract_to_place_crosswalk(county=county)
        place_by_geoid = {r["GEOID"]: r["place_name"] for r in crosswalk.to_dict("records")}

    # Real sales comps, where available: Maui (added 2026-10-26 --
    # scripts/process_maui_sales_exports.py) and Hawai'i County (added
    # 2026-10-27 -- scripts/process_hawaii_county_sales_exports.py, a
    # third-party Regrid export, since this county's own qPublic portal
    # has no bulk export at all). Both are user-supplied, same "no
    # self-service bulk export" situation as Kaua'i's. This is
    # DELIBERATELY sales data only, not a feasibility gate -- there is no
    # sourced construction-cost pro forma for either county yet, so these
    # numbers describe the market (what things sell for), not whether a
    # reform pencils (see this function's own docstring). A county with
    # no `{county}_sales_joined.csv` file yet simply leaves sales_by_geoid
    # empty.
    sales_path = PROCESSED_DIR / f"{county}_sales_joined.csv"
    sales_by_geoid = {}
    countywide_sales_count = 0
    countywide_median_price_per_sqft = None
    sales_monthly_trend = []
    if sales_path.exists():
        sales_df = pd.read_csv(sales_path, dtype={"GEOID": str})
        valid_col = sales_df["valid_sale"] if "valid_sale" in sales_df.columns else True
        usable = sales_df[
            (sales_df["building_sqft"] > 0)
            & sales_df["price_per_sqft"].notna()
            & sales_df["GEOID"].notna()
            & valid_col
        ].copy()
        countywide_sales_count = int(len(usable))
        countywide_median_price_per_sqft = (
            round(usable["price_per_sqft"].median(), 2) if countywide_sales_count else None
        )
        by_tract = usable.groupby("GEOID")["price_per_sqft"].agg(["median", "count"])
        sales_by_geoid = {
            geoid: {"median_price_per_sqft": round(row["median"], 2), "sales_count": int(row["count"])}
            for geoid, row in by_tract.iterrows()
        }
        usable["month"] = pd.to_datetime(usable["sale_date"]).dt.to_period("M").astype(str)
        by_month = usable.groupby("month")["price_per_sqft"].agg(["median", "count"]).reset_index()
        sales_monthly_trend = [
            {"month": r["month"], "median_price_per_sqft": round(r["median"], 2), "sales_count": int(r["count"])}
            for _, r in by_month.iterrows()
        ]

    try:
        from shapely.geometry import mapping, shape
    except ImportError:
        shape = mapping = None

    features = []
    for geoid, feat in tract_by_geoid.items():
        zoned_r = zoned_by_geoid.get(geoid)
        if zoned_r is None:
            # No parcel in this tract matched the county's own layers --
            # same "skip, don't zero-fill" convention build_kauai_features()
            # uses, so an unmatched tract is absent from the data rather
            # than shown as a real, modeled zero.
            continue
        adu_r = adu_by_geoid.get(geoid, {})
        mm_r = mm_by_geoid.get(geoid, {})
        mm_baseline_r = mm_baseline_by_geoid.get(geoid, {})
        sales_r = sales_by_geoid.get(geoid, {})
        props = {
            "GEOID": geoid,
            "county": county,
            "name": feat["properties"].get("NAME"),
            "existing_total_parcel_count": zoned_r.get("total_parcel_count"),
            "existing_residential_parcel_count": zoned_r.get("residential_parcel_count"),
            "existing_units_built": zoned_r.get("existing_units_built"),
            "existing_zoned_capacity_known_parcel_count": zoned_r.get("zoned_capacity_known_parcel_count"),
            "existing_zoned_capacity_total": zoned_r.get("zoned_capacity_total"),
            "existing_zoned_headroom": zoned_r.get("baseline_capacity_gross"),
            "existing_mm_headroom": zoned_r.get("mm_headroom"),
            "existing_adu_headroom": zoned_r.get("adu_headroom"),
            "adu_eligible_parcels": adu_r.get("eligible_parcel_count"),
            "adu_gross_capacity": adu_r.get("eligible_parcel_count"),
            "mm_eligible_parcels": mm_r.get("eligible_parcel_count"),
            "mm_gross_capacity": mm_r.get("additional_gross_capacity"),
            "mm_baseline_capacity_estimated_units": mm_baseline_r.get("baseline_capacity_estimated_units"),
            "area_land_sqmi": (feat["properties"].get("AREALAND") or 0) / 2_589_988.11,
            "place_name": place_by_geoid.get(geoid),
            "sales_median_price_per_sqft": sales_r.get("median_price_per_sqft"),
            "sales_count_2026": sales_r.get("sales_count"),
        }
        geometry = feat["geometry"]
        if shape is not None:
            geometry = mapping(shape(geometry).simplify(0.0002, preserve_topology=True))
        features.append({"type": "Feature", "geometry": geometry, "properties": props})

    metadata = {
        "default_absorption_rate": default_absorption_rate,
        "absorption_rate_benchmark_detail": bm.absorption_rate_benchmark_detail(county),
        "mm_baseline_capacity_estimated_units_total": round(mm_baseline_tract["baseline_capacity_estimated_units"].sum(), 1),
        "existing_conditions_summary": {
            "total_parcel_count": int(zoned_capacity_tract["total_parcel_count"].sum()),
            "residential_parcel_count": int(zoned_capacity_tract["residential_parcel_count"].sum()),
            "existing_units_built": int(zoned_capacity_tract["existing_units_built"].sum()),
            "zoned_capacity_known_parcel_count": int(zoned_capacity_tract["zoned_capacity_known_parcel_count"].sum()),
            "zoned_capacity_total": int(zoned_capacity_tract["zoned_capacity_total"].sum()),
            "zoned_headroom": int(zoned_capacity_tract["baseline_capacity_gross"].sum()),
            "mm_headroom": int(zoned_capacity_tract["mm_headroom"].sum()),
            "adu_headroom": int(zoned_capacity_tract["adu_headroom"].sum()),
        },
        "data_status_note": (
            "Added to the model pipeline 2026-10-24: Baseline (zoned capacity), Missing Middle, "
            "and ADU/Ohana are real, computed from this county's own live parcel/zoning data and a "
            "real, cited ADU ordinance -- but UNGATED, since no feasibility pro forma or affordability "
            "data exists yet for this county (unlike Kaua'i/Honolulu's sales-comps/ACS-scaled gates). "
            "This county's ADU absorption rate is also the generic countywide benchmark, not a real "
            "permit-calibrated rate like Honolulu's -- see docs/MODEL_AND_ASSUMPTIONS.md's Limitations. "
            "No TOD transit layer, parking-reform study, or ACS income/commute/rent data exists "
            "for this county" + (
                ", and no sales data either" if not sales_path.exists() else
                " -- real sales comps ARE now wired in, but as market data only, "
                "not a feasibility gate: there is still no sourced construction-cost pro forma for "
                "this county to compare them against"
            ) + ", so this entry appears in Existing Conditions only, not the Scenario Explorer."
        ),
        "countywide_sales_median_price_per_sqft": countywide_median_price_per_sqft,
        "countywide_sales_count_2026": countywide_sales_count,
        "sales_monthly_trend": sales_monthly_trend,
    }
    return features, metadata


def build_gated_county_features(county: str) -> tuple[list, dict]:
    """
    Hawai'i County / Maui, upgraded 2026-10-27 from build_light_county_features()'s
    UNGATED Baseline/Missing Middle/ADU numbers to a real feasibility +
    affordability gate, now that both counties have a real, sourced
    construction-cost pro forma (HR&A Advisors et al.'s OPSD TOD
    Infrastructure Financing report covers all 4 counties, not just
    Kaua'i/Honolulu -- see build_model.py's SITE_PRO_FORMA_CONFIG) and real
    sales comps (process_hawaii_county_sales_exports.py /
    process_maui_sales_exports.py) to scale it by.

    Mirrors build_kauai_features()'s dual-tenure feasibility/affordability
    wiring (generic_tract_comps_development_fee_reduction_feasibility() +
    tract_scaled_rental_feasibility() + combine_dual_tenure_feasibility(),
    then generic_tract_affordability_constrained_capacity() for both
    tenures, routed through apply_dual_tenure_affordability_gate()) applied
    to ADU and Missing Middle only -- NOT TOD or Parking Reduction, since
    neither county has a TOD transit-stop layer or a parking-reform study
    wired in yet (same real gap build_light_county_features() already
    stated; not newly introduced here). Those two scenarios stay absent
    from this county's `scenarios_available` list for exactly that reason,
    not an oversight.
    """
    bm.build_county(county)
    default_absorption_rate = bm.absorption_rate_benchmark_for(county)

    adu_tract, _ = bm.run_adu_scenario(county)
    mm_tract, _ = bm.run_missing_middle_scenario(county)
    mm_baseline_tract = bm.run_missing_middle_baseline_tract(county)
    zoned_capacity_tract = bm.existing_zoned_capacity_tract(county)

    feas = bm.generic_tract_comps_development_fee_reduction_feasibility(county)
    rental_feas = bm.tract_scaled_rental_feasibility(county)
    combined_feas = bm.combine_dual_tenure_feasibility(feas, rental_feas)

    adu_gated = bm.apply_feasibility_gate(adu_tract, combined_feas, "estimated_new_units")
    mm_gated = bm.apply_feasibility_gate(mm_tract, combined_feas, "additional_estimated_units")

    afford_for_sale = bm.generic_tract_affordability_constrained_capacity(county, sales_feasibility_df=feas, tenure="for_sale")
    afford_rental = bm.generic_tract_affordability_constrained_capacity(county, tenure="rental")
    adu_gated = bm.apply_dual_tenure_affordability_gate(adu_gated, afford_for_sale, afford_rental, "feasibility_adjusted_units")
    mm_gated = bm.apply_dual_tenure_affordability_gate(mm_gated, afford_for_sale, afford_rental, "feasibility_adjusted_units")

    adu_tract = adu_gated.copy(); adu_tract["GEOID"] = adu_tract["GEOID"].astype(str)
    mm_tract = mm_gated.copy(); mm_tract["GEOID"] = mm_tract["GEOID"].astype(str)
    mm_baseline_tract = mm_baseline_tract.copy(); mm_baseline_tract["GEOID"] = mm_baseline_tract["GEOID"].astype(str)
    zoned_capacity_tract = zoned_capacity_tract.copy(); zoned_capacity_tract["GEOID"] = zoned_capacity_tract["GEOID"].astype(str)

    adu_by_geoid = {r["GEOID"]: r for r in adu_tract.to_dict("records")}
    mm_by_geoid = {r["GEOID"]: r for r in mm_tract.to_dict("records")}
    mm_baseline_by_geoid = {r["GEOID"]: r for r in mm_baseline_tract.to_dict("records")}
    zoned_by_geoid = {r["GEOID"]: r for r in zoned_capacity_tract.to_dict("records")}

    fips_prefix = COUNTY_TRACT_FIPS_PREFIX[county]
    tracts = json.loads((RAW_DIR / "census_tracts.json").read_text())["features"]
    tract_by_geoid = {f["properties"]["GEOID"]: f for f in tracts if f["properties"]["GEOID"].startswith(fips_prefix)}

    place_by_geoid = {}
    if (RAW_DIR / "census_places.json").exists():
        crosswalk = bm.tract_to_place_crosswalk(county=county)
        place_by_geoid = {r["GEOID"]: r["place_name"] for r in crosswalk.to_dict("records")}

    income_path = RAW_DIR / f"acs_income_distribution_{county}.csv"
    income_by_geoid = {}
    if income_path.exists():
        income_df = pd.read_csv(income_path, dtype={"GEOID": str})
        income_by_geoid = {r["GEOID"]: r for r in income_df.to_dict("records")}

    acs = pd.read_csv(RAW_DIR / f"acs_commute_density_{county}.csv", dtype={"GEOID": str})
    acs_by_geoid = {r["GEOID"]: r for r in acs.to_dict("records")}

    sales_path = PROCESSED_DIR / f"{county}_sales_joined.csv"
    sales_by_geoid = {}
    countywide_sales_count = 0
    countywide_median_price_per_sqft = None
    sales_monthly_trend = []
    if sales_path.exists():
        sales_df = pd.read_csv(sales_path, dtype={"GEOID": str})
        sales_df["GEOID"] = sales_df["GEOID"].str.replace(r"\.0$", "", regex=True)
        valid_col = sales_df["valid_sale"] if "valid_sale" in sales_df.columns else True
        usable = sales_df[
            (sales_df["building_sqft"] > 0)
            & sales_df["price_per_sqft"].notna()
            & sales_df["GEOID"].notna()
            & valid_col
        ].copy()
        countywide_sales_count = int(len(usable))
        countywide_median_price_per_sqft = (
            round(usable["price_per_sqft"].median(), 2) if countywide_sales_count else None
        )
        by_tract = usable.groupby("GEOID")["price_per_sqft"].agg(["median", "count"])
        sales_by_geoid = {
            geoid: {"median_price_per_sqft": round(row["median"], 2), "sales_count": int(row["count"])}
            for geoid, row in by_tract.iterrows()
        }
        usable["month"] = pd.to_datetime(usable["sale_date"]).dt.to_period("M").astype(str)
        by_month = usable.groupby("month")["price_per_sqft"].agg(["median", "count"]).reset_index()
        sales_monthly_trend = [
            {"month": r["month"], "median_price_per_sqft": round(r["median"], 2), "sales_count": int(r["count"])}
            for _, r in by_month.iterrows()
        ]

    try:
        from shapely.geometry import mapping, shape
    except ImportError:
        shape = mapping = None

    features = []
    for geoid, feat in tract_by_geoid.items():
        zoned_r = zoned_by_geoid.get(geoid)
        if zoned_r is None:
            continue
        adu_r = adu_by_geoid.get(geoid, {})
        mm_r = mm_by_geoid.get(geoid, {})
        mm_baseline_r = mm_baseline_by_geoid.get(geoid, {})
        sales_r = sales_by_geoid.get(geoid, {})
        acs_r = acs_by_geoid.get(geoid, {})
        income_r = income_by_geoid.get(geoid, {})
        props = {
            "GEOID": geoid,
            "county": county,
            "name": feat["properties"].get("NAME"),
            "place_name": place_by_geoid.get(geoid),
            "existing_total_parcel_count": zoned_r.get("total_parcel_count"),
            "existing_residential_parcel_count": zoned_r.get("residential_parcel_count"),
            "existing_units_built": zoned_r.get("existing_units_built"),
            "existing_zoned_capacity_known_parcel_count": zoned_r.get("zoned_capacity_known_parcel_count"),
            "existing_zoned_capacity_total": zoned_r.get("zoned_capacity_total"),
            "existing_zoned_headroom": zoned_r.get("baseline_capacity_gross"),
            "existing_mm_headroom": zoned_r.get("mm_headroom"),
            "existing_adu_headroom": zoned_r.get("adu_headroom"),
            "adu_eligible_parcels": adu_r.get("eligible_parcel_count"),
            "adu_gross_capacity": adu_r.get("eligible_parcel_count"),
            "adu_feasible": adu_r.get("feasible"),
            "adu_pct_affording": adu_r.get("pct_households_affording"),
            "adu_excess_value_usd_at_fee50k": adu_r.get("excess_value_usd"),
            "adu_winning_tenure": adu_r.get("winning_tenure"),
            "mm_eligible_parcels": mm_r.get("eligible_parcel_count"),
            "mm_gross_capacity": mm_r.get("additional_gross_capacity"),
            "mm_feasible": mm_r.get("feasible"),
            "mm_pct_affording": mm_r.get("pct_households_affording"),
            "mm_excess_value_usd_at_fee50k": mm_r.get("excess_value_usd"),
            "mm_winning_tenure": mm_r.get("winning_tenure"),
            "mm_baseline_capacity_estimated_units": mm_baseline_r.get("baseline_capacity_estimated_units"),
            "median_home_value_usd": acs_r.get("median_home_value_usd"),
            "median_gross_rent_usd": acs_r.get("median_gross_rent_usd"),
            "pct_alt_mode_commute": acs_r.get("pct_alt_mode_commute"),
            "population": acs_r.get("population"),
            "commuters_excl_wfh": acs_r.get("commuters_excl_wfh"),
            "density_per_sqmi": acs_r.get("density_per_sqmi"),
            "area_land_sqmi": (feat["properties"].get("AREALAND") or 0) / 2_589_988.11,
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

    zori_by_county = {"hawaii": bm.HAWAII_COUNTYWIDE_ZORI_USD_PER_MONTH, "maui": bm.MAUI_COUNTYWIDE_ZORI_USD_PER_MONTH}

    metadata = {
        "default_absorption_rate": default_absorption_rate,
        "absorption_rate_benchmark_detail": bm.absorption_rate_benchmark_detail(county),
        "affordability_mortgage_rate": bm.HAWAII_MORTGAGE_RATE_30YR_FIXED,
        "affordability_down_payment_pct": bm.HAWAII_MORTGAGE_DOWN_PAYMENT_PCT,
        "affordability_income_share": bm.AFFORDABILITY_INCOME_SHARE,
        "countywide_zori_usd_per_month": zori_by_county[county],
        "zillow_timeseries": _zillow_timeseries_for_metadata(county),
        "sales_monthly_trend": sales_monthly_trend,
        "income_vs_affordable_stock": _income_vs_affordable_stock_for_metadata(county),
        "countywide_sales_median_price_per_sqft": countywide_median_price_per_sqft,
        "countywide_sales_count_2026": countywide_sales_count,
        "mm_baseline_capacity_estimated_units_total": round(mm_baseline_tract["baseline_capacity_estimated_units"].sum(), 1),
        "adu_affordability_adjusted_units_for_sale": round(adu_gated["affordability_adjusted_units_for_sale"].sum(), 1),
        "adu_affordability_adjusted_units_rental": round(adu_gated["affordability_adjusted_units_rental"].sum(), 1),
        "mm_affordability_adjusted_units_for_sale": round(mm_gated["affordability_adjusted_units_for_sale"].sum(), 1),
        "mm_affordability_adjusted_units_rental": round(mm_gated["affordability_adjusted_units_rental"].sum(), 1),
        "existing_conditions_summary": {
            "total_parcel_count": int(zoned_capacity_tract["total_parcel_count"].sum()),
            "residential_parcel_count": int(zoned_capacity_tract["residential_parcel_count"].sum()),
            "existing_units_built": int(zoned_capacity_tract["existing_units_built"].sum()),
            "zoned_capacity_known_parcel_count": int(zoned_capacity_tract["zoned_capacity_known_parcel_count"].sum()),
            "zoned_capacity_total": int(zoned_capacity_tract["zoned_capacity_total"].sum()),
            "zoned_headroom": int(zoned_capacity_tract["baseline_capacity_gross"].sum()),
            "mm_headroom": int(zoned_capacity_tract["mm_headroom"].sum()),
            "adu_headroom": int(zoned_capacity_tract["adu_headroom"].sum()),
        },
        "data_status_note": (
            f"Upgraded 2026-10-27: {COUNTY_LABELS[county]}'s ADU and Missing Middle capacity are now "
            "feasibility- and affordability-gated, using the SAME OPSD 'Financial Analysis of TOD "
            "Infrastructure Funding and Financing Options' report Kaua'i/Honolulu's pro formas come "
            "from (it covers all 4 counties' TOD pilot areas, not just those two), scaled by this "
            "county's own real sales comps, exactly as Kaua'i's tract_comps_ version already works. "
            "TOD and Parking Reduction remain NOT modeled for this county -- no transit-stop layer or "
            "parking-reform study exists yet -- so this entry still does not appear in the Scenario "
            "Explorer's TOD/Parking views, only ADU and Missing Middle."
        ),
        "affordability_note": (
            "Of the feasibility-adjusted units, pct_affording is the real share of that tract's own "
            "households whose income clears the mortgage/rent bar for the comps-implied price "
            "(for_sale-won tracts: 20% down, 7.000% 30-yr fixed, per ACS B19001; rental-won tracts: "
            "countywide ZORI rent, per ACS B25118 renter-occupied households)."
        ),
    }
    return features, metadata


def main() -> None:
    kauai_features, kauai_meta = build_kauai_features()
    honolulu_features, honolulu_meta = build_honolulu_features()
    hawaii_features, hawaii_meta = build_gated_county_features("hawaii")
    maui_features, maui_meta = build_gated_county_features("maui")

    all_features = kauai_features + honolulu_features + hawaii_features + maui_features
    metadata = dict(kauai_meta)  # Scenario Explorer's existing top-level keys, unchanged, Kauai-scoped
    metadata["by_county"] = {
        "kauai": {
            "label": COUNTY_LABELS["kauai"],
            "countywide_zori_usd_per_month": kauai_meta.get("countywide_zori_usd_per_month"),
            "zillow_timeseries": kauai_meta.get("zillow_timeseries"),
            "sales_monthly_trend": kauai_meta.get("sales_monthly_trend"),
            "income_vs_affordable_stock": kauai_meta.get("income_vs_affordable_stock"),
            "countywide_median_home_value_usd": kauai_meta.get("countywide_median_home_value_usd"),
            "countywide_sales_median_price_per_sqft": kauai_meta.get("countywide_sales_median_price_per_sqft"),
            "countywide_sales_count_2026": kauai_meta.get("countywide_sales_count_2026"),
            "parking_reform_context": kauai_meta.get("parking_reform_context"),
            "absorption_rate_benchmark_detail": kauai_meta.get("absorption_rate_benchmark_detail"),
            "existing_conditions_summary": kauai_meta.get("existing_conditions_summary"),
            "mm_baseline_capacity_estimated_units_total": kauai_meta.get("mm_baseline_capacity_estimated_units_total"),
            "tod_bus_transit_stop_note": kauai_meta.get("tod_bus_transit_stop_note"),
            "tod_modes_available": kauai_meta.get("tod_modes_available"),
            "scenarios_available": ["adu", "mm", "tod", "parking"],
        },
        "honolulu": {
            "label": COUNTY_LABELS["honolulu"],
            **honolulu_meta,
            "scenarios_available": ["adu", "mm", "tod", "parking"],
        },
        # Hawai'i County and Maui: upgraded 2026-10-27 to a real feasibility/
        # affordability-gated ADU + Missing Middle (build_gated_county_features()) --
        # still no TOD or Parking Reduction, since neither county has a
        # transit-stop layer or parking-reform study, so scenarios_available
        # stays ["adu", "mm"] for both, same as before this upgrade.
        "hawaii": {"label": COUNTY_LABELS["hawaii"], **hawaii_meta, "scenarios_available": ["adu", "mm"]},
        "maui": {"label": COUNTY_LABELS["maui"], **maui_meta, "scenarios_available": ["adu", "mm"]},
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({"type": "FeatureCollection", "features": all_features, "metadata": metadata}))
    print(
        f"{len(all_features)} tracts ({len(kauai_features)} Kauai + {len(honolulu_features)} Honolulu + "
        f"{len(hawaii_features)} Hawai'i County + {len(maui_features)} Maui) -> {OUT_PATH}"
    )


if __name__ == "__main__":
    main()
