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
        props = {
            "GEOID": geoid,
            "name": feat["properties"].get("NAME"),
            # ADU: gross capacity IS eligible_parcel_count (1 potential unit/parcel).
            "adu_eligible_parcels": adu_r.get("eligible_parcel_count"),
            "adu_gross_capacity": adu_r.get("eligible_parcel_count"),
            "adu_feasible": adu_r.get("feasible"),
            "adu_pct_affording": adu_r.get("pct_households_affording"),
            # Missing Middle
            "mm_eligible_parcels": mm_r.get("eligible_parcel_count"),
            "mm_gross_capacity": mm_r.get("additional_gross_capacity"),
            "mm_feasible": mm_r.get("feasible"),
            "mm_pct_affording": mm_r.get("pct_households_affording"),
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
            # Parking Requirement Reduction: does NOT change eligibility or unit
            # count vs. ADU (see run_parking_reduction_scenario docstring) --
            # its own metric is land consumed by the current parking requirement.
            "parking_eligible_parcels": parking_r.get("eligible_parcel_count"),
            "parking_land_consumed_sqft": parking_r.get("parking_land_consumed_sqft_if_required"),
            # ACS context
            "median_home_value_usd": acs_r.get("median_home_value_usd"),
            "pct_alt_mode_commute": acs_r.get("pct_alt_mode_commute"),
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
