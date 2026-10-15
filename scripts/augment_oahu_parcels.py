"""
One-time preprocessing step for Honolulu/Oahu: parcels_oahu.json (the raw
cadastral layer from geodata.hawaii.gov) has NO taxclass field and NO
unit-count field -- both exist natively on Kauai's parcel layer and are
load-bearing in build_model.py's build_county()/run_adu_scenario() (see
that module's docstring, "OAHU BUILDING-CHARACTERISTICS GAP"). This script
closes that gap by joining two separate Honolulu BFS Real Property
Assessment feature services on the shared 8-digit `tmk` field:

  - asmtpitt_honolulu.json: per-parid assessed building value. Summed per
    TMK, a nonzero total is used as a proxy for "this parcel already has
    a residential improvement on it" -- the same role Kauai's taxclass
    field plays. This is a proxy, not a confirmed land-use code (Oahu's
    own `ovrclass` tax-class field is undecoded -- see fetch_data.py's
    docstring -- so this script does NOT attempt to use it).
  - legdat_honolulu.json: one row per legal unit per TMK. Row count per
    TMK is used as the existing-unit count (condo TMKs will have many
    rows; a typical single-family TMK will have exactly one).

Output: data/raw/parcels_oahu_augmented.json, identical to
parcels_oahu.json except each feature's properties gain:
  - taxclass: "RESIDENTIAL" if summed buildingvalue > 0, else "VACANT/OTHER"
  - existing_units: legdat row count for that TMK (0 if no match)

This lets build_county() treat Honolulu like any other county via its
existing_residential_taxclasses / existing_units_field config, with no
change to the join/bucketing logic itself.
"""
import json
from collections import Counter, defaultdict
from pathlib import Path

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"


def main() -> None:
    parcels = json.loads((RAW_DIR / "parcels_oahu.json").read_text())
    legdat = json.loads((RAW_DIR / "legdat_honolulu.json").read_text())
    asmtpitt = json.loads((RAW_DIR / "asmtpitt_honolulu.json").read_text())

    unit_count_by_tmk: Counter = Counter()
    for row in legdat:
        tmk = row.get("tmk")
        if tmk:
            unit_count_by_tmk[tmk] += 1

    building_value_by_tmk: defaultdict = defaultdict(int)
    for row in asmtpitt:
        tmk = row.get("tmk")
        bv = row.get("buildingvalue") or 0
        if tmk:
            building_value_by_tmk[tmk] += bv

    n_residential = 0
    for feature in parcels["features"]:
        tmk = feature["properties"].get("tmk")
        units = unit_count_by_tmk.get(tmk, 0)
        bv = building_value_by_tmk.get(tmk, 0)
        is_residential = bv > 0
        feature["properties"]["taxclass"] = "RESIDENTIAL" if is_residential else "VACANT/OTHER"
        feature["properties"]["existing_units"] = units if is_residential else 0
        n_residential += is_residential

    out_path = RAW_DIR / "parcels_oahu_augmented.json"
    out_path.write_text(json.dumps(parcels))
    print(
        f"{len(parcels['features'])} parcels -> {out_path} "
        f"({n_residential} flagged RESIDENTIAL via nonzero assessed building value)"
    )


if __name__ == "__main__":
    main()
