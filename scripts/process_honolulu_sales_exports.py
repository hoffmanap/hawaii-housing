"""
Honolulu County analog of process_sales_exports.py (Kauai). Consolidates
the user's monthly Honolulu Real Property Assessment sales exports into
one table joined to this project's own parcel/zoning/tract pipeline, and
computes real observed $/building-sqft by zoning district and by tract.

PARCEL ID JOIN KEY, reverse-engineered and confirmed 2026-10-15: these
exports' 12-digit "Parcel ID" is Zone(1) + Section(2) + Plat(3) +
Parcel(3) + CPR/unit(3) -- e.g. "240070020040" decomposes to zone 2,
section 40, plat 070, parcel 020, CPR/unit 040. build_model.py's
parcel_level `tmk` field (from parcels_oahu.json) is the same
Zone+Section+Plat+Parcel, 8 digits, with no CPR/unit suffix. So
Parcel ID[:8] == tmk is the real join key -- confirmed 632/632 (100%)
exact match on a full test month (Jun 2025) against
data/processed/honolulu_parcel_level.csv. Same granularity loss as
Kauai's join: a condo/CPR unit's sale collapses onto its parent TMK.

Schema differs from Kauai's export: columns are 'Parcel ID', 'Address',
'Sale Date', 'Sale Price', 'Sale Valid', 'Parcel  Class ' (note the
double space/trailing space -- stripped below), 'Bldg. SF ', 'Sale  Type',
'Land SF'. 'Parcel  Class ' is a direct usable filter (e.g.
"RESIDENTIAL"), unlike Kauai's enumerated taxclass set -- no need to
cross-reference against existing_residential_taxclasses for the sales
table itself (that set still gates the parcel-level table via
build_county()).

Run after build_county("honolulu") (needs honolulu_parcel_level.csv) and
after placing monthly export files in data/raw/sales_exports/ as
honolulu_sales_YYYY-MM.xlsx (gitignored -- not self-service, so there's
no fetch_data.py layer for this; the user supplies new months as they're
able to pull them).
"""
import glob
import sys
from pathlib import Path

import openpyxl
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import build_model as bm

SALES_DIR = Path(__file__).parent.parent / "data" / "raw" / "sales_exports"
OUT_PATH = Path(__file__).parent.parent / "data" / "processed" / "honolulu_sales_joined.csv"


def _read_export(path: Path) -> pd.DataFrame:
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    header, data = rows[0], rows[1:]
    df = pd.DataFrame(data, columns=[h.strip() for h in header])
    return df


def load_honolulu_sales() -> pd.DataFrame:
    files = sorted(glob.glob(str(SALES_DIR / "honolulu_sales_20*.xlsx")))
    if not files:
        raise RuntimeError(
            f"No Honolulu sales exports found in {SALES_DIR} -- these are manually pulled "
            "by the user, so there's nothing to fetch automatically. Ask for a fresh "
            "monthly export and place it here as honolulu_sales_YYYY-MM.xlsx."
        )
    frames = [_read_export(Path(f)) for f in files]
    sales = pd.concat(frames, ignore_index=True)

    sales["key8"] = sales["Parcel ID"].astype(str).str[:8]
    sales["sale_price_usd"] = (
        sales["Sale Price"].astype(str).str.replace(r"[$,]", "", regex=True).astype(float)
    )
    sales["sale_date"] = pd.to_datetime(sales["Sale Date"])
    sales["building_sqft"] = pd.to_numeric(sales["Bldg. SF"], errors="coerce")
    sales["parcel_class"] = sales["Parcel  Class"].astype(str).str.strip()
    sales["sale_valid"] = sales["Sale Valid"].astype(str).str.strip()
    sales["price_per_sqft"] = sales["sale_price_usd"] / sales["building_sqft"]
    return sales


def join_to_parcels(sales: pd.DataFrame) -> pd.DataFrame:
    parcel_path = bm.OUT_DIR / "honolulu_parcel_level.csv"
    if not parcel_path.exists():
        raise FileNotFoundError(f"Run build_county('honolulu') first -- {parcel_path} not found")
    parcels = pd.read_csv(parcel_path, dtype={"tmk": str})
    parcels["key8"] = parcels["tmk"].str.zfill(8)

    joined = sales.merge(
        parcels[["key8", "GEOID", "zoning", "taxclass"]], on="key8", how="left"
    )
    unmatched = joined["GEOID"].isna().sum()
    if unmatched:
        print(f"  warning: {unmatched}/{len(joined)} sales records did not match a parcel "
              "(likely a CPR/condo unit on a multi-unit TMK, or a parcel outside this layer's coverage)")
    return joined


def summarize(joined: pd.DataFrame) -> None:
    valid = joined[
        (joined["building_sqft"] > 0)
        & joined["price_per_sqft"].notna()
        & joined["parcel_class"].eq("RESIDENTIAL")
        & joined["sale_valid"].str.startswith("Valid")
    ]
    date_min, date_max = joined["sale_date"].min().date(), joined["sale_date"].max().date()
    print(f"\n{len(joined)} total sales records, {len(valid)} usable valid residential "
          f"land+building sales with building sqft ({date_min} - {date_max})")
    print(f"Median $/building sqft, all usable sales: ${valid['price_per_sqft'].median():,.0f}")
    print("\nBy zoning district (n >= 5):")
    by_zone = valid.groupby("zoning")["price_per_sqft"].agg(["median", "count"])
    print(by_zone[by_zone["count"] >= 5].sort_values("count", ascending=False))
    by_tract = valid.groupby("GEOID")["price_per_sqft"].agg(["median", "count"])
    qualifying = by_tract[by_tract["count"] >= 5]
    print(f"\n{len(qualifying)} of {len(by_tract)} matched tracts have 5+ usable sales "
          f"(max per tract: {by_tract['count'].max() if len(by_tract) else 0})")


def main() -> None:
    sales = load_honolulu_sales()
    joined = join_to_parcels(sales)
    joined.to_csv(OUT_PATH, index=False)
    print(f"{len(joined)} records -> {OUT_PATH}")
    summarize(joined)


if __name__ == "__main__":
    main()
