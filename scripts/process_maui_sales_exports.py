"""
Consolidates Maui County Real Property Tax sales exports (manually pulled
by the user from the county's own property-records export tool -- same
situation as Kauai: no self-service bulk/API export exists, so these are
supplied as individual .xlsx files in data/raw/sales_exports/maui/, not
fetched by fetch_data.py) into one table joined to this project's own
parcel/zoning/tract pipeline, and computes real observed $/building-sqft
by zoning district and by tract.

This is the first real sales/feasibility data Maui has had in this
project -- previously Maui had no feasibility pro forma at all (see
build_light_county_features() in build_dashboard_data.py). It does NOT
by itself build a feasibility gate the way Kauai's/Honolulu's
tract_scaled_development_fee_reduction_feasibility()-style functions do
(that needs a sourced construction-cost pro forma, not just sales
comps) -- see this module's summarize() output and
docs/DATA_PROVENANCE.md for what this step does and doesn't establish.

PARCEL ID JOIN KEY, confirmed 2026-10-26, same convention as Kauai's
(see process_sales_exports.py's docstring): these exports' 12-digit
"Parcel ID" has NO leading zone digit -- Section(2) + Plat(3) +
Parcel(3) + CPR/unit(4). build_model.py's parcel_level tmk_txt is
Zone(1) + Section(2) + Plat(3) + Parcel(3), 9 digits, zone always "2"
for Maui. tmk_txt[1:] (8 digits) == Parcel ID[:8] (8 digits) is the
join key -- confirmed exact match against a sample of
data/processed/maui_parcel_level.csv's real tmk_txt values before
writing this script.

VALID-SALE FILTER, per the user's direct instruction when this script
was first written (2026-10-16, "this data is a lot messier than the
other counties and we should only include valid sales"): Maui's export
carries a "Valid Sale or Other Reason" column that Kaua'i's and
Honolulu's exports don't expose in the same form. Only rows flagged
"Valid Sale", "Valid mult bldgs", or "Valid sale w/ adj price" are the
CAMA system's own "this is a real arm's-length transaction"
determination and are kept; a blank/missing flag (the overwhelming
majority of raw rows -- almost all $0.00 non-sale transfers: quitclaims,
trust transfers, name changes) and every other named reason (Other,
Related individuals, Foreclosure, REO/Short sale, Developer sale,
Involved add'l parcels, partial interest, Leasehold unadj, Unvalidated,
Abnormal financing, leased fee, Changed after sale) are each a specific
non-arm's-length/unreliable reason and are excluded, same principle as
Kaua'i/Honolulu's taxclass filters. This REPLACES an earlier,
less-careful version of this exact filter (`== "Valid Sale"` only) that
this file was briefly rewritten to on 2026-10-26 before being corrected
back to the original three-reason set.

The "Parcel  Class" field here (NON-OWNER-OCCUPIED/RESIDENTIAL,
OWNER-OCCUPIED/HOMEOWNER, LONG TERM RENTAL, APARTMENT) is a separate,
TEXT-based classification from the GIS parcel layer's numeric
`pittcode` field -- it's not itself a confirmation of the pittcode x100
decode (still an unconfirmed hypothesis for Maui per
docs/MODEL_AND_ASSUMPTIONS.md), but its category NAMES line up with
what that hypothesis predicts (100~Residential, 900~Homeowner,
200~Apartment), which is corroborating, not confirming, evidence.

Run after build_county("maui") (needs maui_parcel_level.csv) and after
placing export files in data/raw/sales_exports/maui/ (gitignored -- not
self-service, so there's no fetch_data.py layer for this; the user
supplies new batches as they're able to pull them).
"""
import glob
import sys
from pathlib import Path

import openpyxl
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import build_model as bm

SALES_DIR = Path(__file__).parent.parent / "data" / "raw" / "sales_exports" / "maui"
OUT_PATH = Path(__file__).parent.parent / "data" / "processed" / "maui_sales_joined.csv"

# See module docstring's "VALID-SALE FILTER" section.
VALID_SALE_REASONS = {"Valid Sale", "Valid mult bldgs", "Valid sale w/ adj price"}


def _read_export(path: Path) -> pd.DataFrame:
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    header, data = rows[0], rows[1:]
    df = pd.DataFrame(data, columns=[h.strip() for h in header])
    return df


def load_maui_sales() -> pd.DataFrame:
    files = sorted(glob.glob(str(SALES_DIR / "*.xlsx")))
    if not files:
        raise RuntimeError(
            f"No Maui sales exports found in {SALES_DIR} -- these are manually pulled from "
            "the county's property-records export tool (no bulk self-service export exists), "
            "so there's nothing to fetch automatically. Ask for a fresh export and place it here."
        )
    frames = [_read_export(Path(f)) for f in files]
    sales = pd.concat(frames, ignore_index=True)
    sales = sales.drop_duplicates()  # the two supplied batches may genuinely overlap in date range

    sales["key8"] = sales["Parcel ID"].astype(str).str[:8]
    sales["sale_price_usd"] = (
        sales["Sale Price"].astype(str).str.replace(r"[$,]", "", regex=True).astype(float)
    )
    sales["sale_date"] = pd.to_datetime(sales["Sale Date"])
    sales["building_sqft"] = pd.to_numeric(sales["Square Ft"], errors="coerce")
    sales["price_per_sqft"] = sales["sale_price_usd"] / sales["building_sqft"]
    sales["valid_sale"] = sales["Valid Sale  or Other Reason"].astype(str).str.strip().isin(VALID_SALE_REASONS)
    return sales


def join_to_parcels(sales: pd.DataFrame) -> pd.DataFrame:
    parcel_path = bm.OUT_DIR / "maui_parcel_level.csv"
    if not parcel_path.exists():
        raise FileNotFoundError(f"Run build_county('maui') first -- {parcel_path} not found")
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
    print(f"\n{len(joined)} total sales records (after de-dup), {len(valid)} usable valid-sale "
          f"records with building sqft and a matched parcel")
    print(f"Sale date range: {joined['sale_date'].min().date()} to {joined['sale_date'].max().date()}")
    print(f"Median $/building sqft, all usable sales: ${valid['price_per_sqft'].median():,.0f}")
    print("\nBy Parcel Class (n >= 5):")
    by_class = valid.groupby("Parcel  Class")["price_per_sqft"].agg(["median", "count"])
    print(by_class[by_class["count"] >= 5].sort_values("count", ascending=False))
    print("\nBy tract (n >= 5):")
    by_tract = valid.groupby("GEOID")["price_per_sqft"].agg(["median", "count"])
    qualifying = by_tract[by_tract["count"] >= 5]
    print(qualifying if len(qualifying) else "  none -- no tract has 5+ usable sales over the full window")
    print(f"\n{len(by_tract)} tracts have ANY usable sale; "
          f"max per tract: {by_tract['count'].max() if len(by_tract) else 0}")


def main() -> None:
    sales = load_maui_sales()
    joined = join_to_parcels(sales)
    joined.to_csv(OUT_PATH, index=False)
    print(f"{len(joined)} records -> {OUT_PATH}")
    summarize(joined)


if __name__ == "__main__":
    main()
