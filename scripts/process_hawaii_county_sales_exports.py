"""
Consolidates Hawai'i County parcel-sales data into one table joined to
this project's own parcel/zoning/tract pipeline, and computes real
observed $/building-sqft by zoning type and by tract -- the same role
process_maui_sales_exports.py plays for Maui.

SOURCE IS DIFFERENT FROM MAUI'S, THOUGH: Hawai'i County's own qPublic
(Schneider Corp "Beacon") portal has no bulk/self-service sales export
and no public REST endpoint for its "Yearly Sales" map layer -- confirmed
2026-10-27 by inspecting that portal directly (its sales layer is
rendered through a session-token-gated internal API with no public
query capability, and its Search page only supports address/parcel/
legal/condo lookups, not a sales-date-range export). So unlike Maui
(county's own property-records export tool) or Kaua'i/Honolulu, this
county's sales come from a third-party parcel-data vendor (Regrid,
app.regrid.com) export the user supplied directly, covering sales from
2025-01-02 through 2025-05-09 (so a partial year, not a full calendar
year like Honolulu/Kaua'i's benchmarks -- see summarize() output and
docs/DATA_PROVENANCE.md for what this does and doesn't establish).

NO ARM'S-LENGTH / VALID-SALE FLAG EXISTS IN THIS EXPORT. Maui's county
export carries its own CAMA "Valid Sale or Other Reason" field (see
process_maui_sales_exports.py's docstring) that lets non-arm's-length
transfers (quitclaims, trust changes, foreclosures, etc.) be excluded
with the county's own determination. Regrid's export has no equivalent
column -- there is nothing here that flags a sale as a gift, trust
transfer, or related-party deal. The only floor applied is saleprice >
0 (confirmed: every one of the 1,364 supplied records already has a
nonzero saleprice, minimum $10,000), which is NOT the same thing as an
arm's-length determination. This is a real, stated gap: some of these
observed $/sqft figures may be inflated or deflated by non-market
transfers this script has no way to detect or exclude. Flagged here
rather than silently assumed away.

PARCEL ID JOIN KEY, confirmed 2026-10-27 by testing against this
project's own hawaii_parcel_level.csv (100% match on 1,364/1,364
supplied records): Regrid's 12-digit "parcelnumb_no_formatting" is
Zone(1) + Section(1) + Plat(3) + Parcel(3) + CPR/unit(4, always "0000"
in this export -- no condo sales supplied). build_model.py's
parcel-level tmk_txt is Island(1, always "3" for Hawai'i island) +
Zone(1) + Section(1) + Plat(3) + Parcel(3), 9 digits. So
parcelnumb_no_formatting[:8] == tmk_txt[1:] (8 digits, dropping the
leading island digit) is the join key.

BUILDING SQFT: Regrid's "area_building" column carries a parallel
"area_building_definition" field distinguishing "LIVING AREA" (636 of
1,364 records), "TOTAL AREA" (97), and "UNKNOWN OR NOT PROVIDED" (19);
612 records have neither (vacant/land-only parcels, mostly the
Agriculture-zoned sales that dominate this export). Only "LIVING AREA"
rows are used for $/building-sqft -- the same single, consistent
definition Maui's "Square Ft" column represents -- so a $/sqft figure
here is never a mix of two different area definitions. This also means
most of this export's 896 Agriculture-zoned sales (land with no
residential structure) are excluded from the $/sqft comp entirely, not
zero-filled; see summarize()'s counts.

Run after build_county("hawaii") (needs hawaii_parcel_level.csv) and
after placing export files in data/raw/sales_exports/hawaii/
(gitignored -- not self-service, so there's no fetch_data.py layer for
this; the user supplies new batches as they're able to pull them from
Regrid or wherever the county eventually makes one available).
"""
import glob
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import build_model as bm

SALES_DIR = Path(__file__).parent.parent / "data" / "raw" / "sales_exports" / "hawaii"
OUT_PATH = Path(__file__).parent.parent / "data" / "processed" / "hawaii_sales_joined.csv"

VALID_AREA_DEFINITIONS = {"LIVING AREA"}


def load_hawaii_county_sales() -> pd.DataFrame:
    files = sorted(glob.glob(str(SALES_DIR / "*.csv")))
    if not files:
        raise RuntimeError(
            f"No Hawai'i County sales exports found in {SALES_DIR} -- this county's qPublic "
            "portal has no bulk/self-service sales export and no public REST endpoint for its "
            "\"Yearly Sales\" layer (confirmed 2026-10-27), so there's nothing to fetch "
            "automatically. Ask for a fresh Regrid (or equivalent vendor) export and place it here."
        )
    frames = [pd.read_csv(f, dtype=str) for f in files]
    sales = pd.concat(frames, ignore_index=True)
    sales = sales.drop_duplicates(subset=["ogc_fid"]) if "ogc_fid" in sales.columns else sales.drop_duplicates()

    sales["key8"] = sales["parcelnumb_no_formatting"].astype(str).str[:8]
    sales["sale_price_usd"] = pd.to_numeric(sales["saleprice"], errors="coerce")
    sales["sale_date"] = pd.to_datetime(sales["saledate"], errors="coerce")
    sales["building_sqft"] = pd.to_numeric(sales["area_building"], errors="coerce")
    sales.loc[~sales["area_building_definition"].isin(VALID_AREA_DEFINITIONS), "building_sqft"] = pd.NA
    sales["price_per_sqft"] = sales["sale_price_usd"] / sales["building_sqft"]
    # No arm's-length flag exists in this vendor's export -- see module
    # docstring. The only floor is a nonzero sale price (already true of
    # every supplied record); this is NOT an arm's-length determination.
    sales["valid_sale"] = sales["sale_price_usd"] > 0
    return sales


def join_to_parcels(sales: pd.DataFrame) -> pd.DataFrame:
    parcel_path = bm.OUT_DIR / "hawaii_parcel_level.csv"
    if not parcel_path.exists():
        raise FileNotFoundError(f"Run build_county('hawaii') first -- {parcel_path} not found")
    parcels = pd.read_csv(parcel_path, dtype={"tmk_txt": str})
    parcels["key8"] = parcels["tmk_txt"].str[1:]

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
        joined["valid_sale"]
        & (joined["building_sqft"] > 0)
        & joined["price_per_sqft"].notna()
        & joined["GEOID"].notna()
    ]
    print(f"\n{len(joined)} total sales records (after de-dup), {len(valid)} usable "
          f"records with a LIVING AREA building sqft and a matched parcel")
    print(f"Sale date range: {joined['sale_date'].min().date()} to {joined['sale_date'].max().date()} "
          "-- a partial year, not a full calendar year")
    print(f"Median $/building sqft, all usable sales: ${valid['price_per_sqft'].median():,.0f}")
    print("\nBy zoning_type (n >= 5):")
    by_zt = joined.loc[joined["GEOID"].notna()].groupby("zoning_type") if "zoning_type" in joined.columns else None
    by_class = valid.groupby("zoning_type")["price_per_sqft"].agg(["median", "count"]) if "zoning_type" in valid.columns else pd.DataFrame()
    if len(by_class):
        print(by_class[by_class["count"] >= 5].sort_values("count", ascending=False))
    print("\nBy tract (n >= 5):")
    by_tract = valid.groupby("GEOID")["price_per_sqft"].agg(["median", "count"])
    qualifying = by_tract[by_tract["count"] >= 5]
    print(qualifying if len(qualifying) else "  none -- no tract has 5+ usable sales over the full window")
    print(f"\n{len(by_tract)} tracts have ANY usable sale; "
          f"max per tract: {by_tract['count'].max() if len(by_tract) else 0}")
    print(f"\n{(joined['GEOID'].notna() & (joined.get('building_sqft', pd.Series(dtype=float)).isna() | (joined.get('building_sqft', 0) <= 0))).sum()} "
          "matched sales excluded from $/sqft entirely (no LIVING AREA building-sqft figure -- "
          "mostly vacant/agricultural land, not zero-filled)")


def main() -> None:
    sales = load_hawaii_county_sales()
    joined = join_to_parcels(sales)
    joined.to_csv(OUT_PATH, index=False)
    print(f"{len(joined)} records -> {OUT_PATH}")
    summarize(joined)


if __name__ == "__main__":
    main()
