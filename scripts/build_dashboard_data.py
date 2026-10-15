"""
Regenerates data/sample/kauai_dashboard.geojson -- the data embedded in
index.html -- from build_model.py's live output. Run this after any change
to the ADU scenario, Missing Middle scenario, or the development-fee
feasibility pro forma, then re-run `python scripts/embed_dashboard_data.py`
to re-inline it into index.html (see that script's docstring).

Requires the raw Kauai GIS pulls (fetch_data.py) and the ACS commute/density/
home-value layer (fetch_data.py --layer acs_commute_density --county kauai,
needs CENSUS_API_KEY) to already be present in data/raw/.
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
    adu_tract, _ = bm.run_adu_scenario("kauai")
    mm_tract, _ = bm.run_missing_middle_scenario("kauai")
    feas = bm.tract_scaled_development_fee_reduction_feasibility()

    adu_gated = bm.apply_feasibility_gate(adu_tract, feas, "estimated_new_units")
    mm_gated = bm.apply_feasibility_gate(mm_tract, feas, "additional_estimated_units")

    acs = pd.read_csv(RAW_DIR / "acs_commute_density_kauai.csv")
    acs["GEOID"] = acs["GEOID"].astype(str)

    tracts = json.loads((RAW_DIR / "census_tracts.json").read_text())["features"]
    tract_by_geoid = {f["properties"]["GEOID"]: f for f in tracts}
    adu_by_geoid = {r["GEOID"]: r for r in adu_gated.to_dict("records")}
    mm_by_geoid = {r["GEOID"]: r for r in mm_gated.to_dict("records")}
    acs_by_geoid = {r["GEOID"]: r for r in acs.to_dict("records")}

    try:
        from shapely.geometry import mapping, shape
    except ImportError:
        shape = mapping = None

    features = []
    for geoid, feat in tract_by_geoid.items():
        if geoid not in adu_by_geoid and geoid not in mm_by_geoid:
            continue  # water-only/zero-population tracts etc.
        adu_r = adu_by_geoid.get(geoid, {})
        mm_r = mm_by_geoid.get(geoid, {})
        acs_r = acs_by_geoid.get(geoid, {})
        props = {
            "GEOID": geoid,
            "name": feat["properties"].get("NAME"),
            "adu_eligible_parcels": adu_r.get("eligible_parcel_count"),
            "adu_units_raw": adu_r.get("estimated_new_units"),
            "adu_units_feasible": adu_r.get("feasibility_adjusted_units"),
            "adu_feasible": adu_r.get("feasible"),
            "mm_eligible_parcels": mm_r.get("eligible_parcel_count"),
            "mm_units_raw": mm_r.get("additional_estimated_units"),
            "mm_units_feasible": mm_r.get("feasibility_adjusted_units"),
            "mm_feasible": mm_r.get("feasible"),
            "median_home_value_usd": acs_r.get("median_home_value_usd"),
            "pct_alt_mode_commute": acs_r.get("pct_alt_mode_commute"),
        }
        geometry = feat["geometry"]
        if shape is not None:
            geometry = mapping(shape(geometry).simplify(0.0002, preserve_topology=True))
        features.append({"type": "Feature", "geometry": geometry, "properties": props})

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    print(f"{len(features)} tracts -> {OUT_PATH}")


if __name__ == "__main__":
    main()
