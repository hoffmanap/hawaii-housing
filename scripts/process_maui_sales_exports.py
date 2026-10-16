"""
Consolidates Maui County Real Property Tax sales exports (same manual-pull
qPublic/Schneider Corp CAMA pattern as Kauai and Honolulu -- 20 monthly
files, Feb 2025-Sep 2026, supplied 2026-10-16) into one cleaned table of
genuinely usable, arm's-length sales.

PER THE USER'S DIRECT INSTRUCTION ("this data is a lot messier than the
other counties and we should only include valid sales"): Maui's export
carries a "Valid Sale or Other Reason" column that Kauai's and
Honolulu's exports don't expose in the same form. Of 13,277 raw rows,
only 2,029 are flagged as a real valid sale:
  - "Valid Sale" (1,920), "Valid mult bldgs" (107), "Valid sale w/ adj
    price" (2) -- KEPT, these are the CAMA system's own "this is a real
    arm's-length transaction" determination.
  - None/blank (9,615) -- the overwhelming majority of rows, almost all
    $0.00 sale price (quitclaims, trust transfers, name changes -- not
    sales at all). EXCLUDED.
  - "Other", "Involved add'l parcels", "Related individuals", "Leasehold
    unadj", "Developer sale", "partial interest", "Foreclosure",
    "REO/Short sale", "Unvalidated", "Abnormal financing", "leased fee",
    "Changed after sale" -- each names a specific reason the price isn't
    a clean market signal (non-arm's-length, distressed, multi-parcel,
    etc.). EXCLUDED, same principle as Kauai/Honolulu's taxclass filters.

Building square footage and a positive, non-null sale price are also
required, same as the other two counties' usable-sales tests.

NOT YET JOINED TO A TRACT/PARCEL LAYER: unlike Kauai and Honolulu, Maui
has no build_model.py parcel pipeline yet (no parcel_level.csv, no ACS
pull, no zoning/tract layer fetched for Maui) -- this script only
produces the cleaned, county-wide sales table. Wiring Maui into the
dashboard as a third built-out county is a separate, larger task (the
full fetch_data.py -> build_model.py -> build_dashboard_data.py chain
Kauai and Honolulu already went through), not done here.
"""
import glob
import sys
from pathlib import Path

import openpyxl
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

SALES_DIR = Path(__file__).parent.parent / "data" / "raw" / "sales_exports" / "maui"
OUT_PATH = Path(__file__).parent.parent / "data" / "processed" / "maui_sales_cleaned.csv"

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
        raise RuntimeError(f"No Maui sales exports found in {SALES_DIR}")
    frames = [_read_export(Path(f)) for f in files]
    sales = pd.concat(frames, ignore_index=True)

    sales["sale_price_usd"] = (
        sales["Sale Price"].astype(str).str.replace(r"[$,]", "", regex=True).astype(float)
    )
    sales["sale_date"] = pd.to_datetime(sales["Sale Date"])
    sales["building_sqft"] = pd.to_numeric(sales["Square Ft"], errors="coerce")
    sales["price_per_sqft"] = sales["sale_price_usd"] / sales["building_sqft"]
    sales["valid_sale_reason"] = sales["Valid Sale  or Other Reason"]
    sales["parcel_class"] = sales["Parcel  Class"].astype(str).str.strip()
    return sales


def filter_usable(sales: pd.DataFrame) -> pd.DataFrame:
    usable = sales[
        sales["valid_sale_reason"].isin(VALID_SALE_REASONS)
        & (sales["sale_price_usd"] > 0)
        & (sales["building_sqft"] > 0)
        & sales["price_per_sqft"].notna()
    ].copy()
    return usable


def main() -> None:
    sales = load_maui_sales()
    usable = filter_usable(sales)
    usable.to_csv(OUT_PATH, index=False)
    print(f"{len(sales)} raw sales records across {len(glob.glob(str(SALES_DIR / '*.xlsx')))} monthly exports")
    print(f"{sales['valid_sale_reason'].isna().sum()} rows have no valid-sale flag at all "
          f"(almost all $0.00 non-sale transfers) -- excluded")
    print(f"{len(usable)} genuinely usable, valid arm's-length sales with building sqft -> {OUT_PATH}")
    print(f"\nBy parcel class (n >= 5):")
    by_class = usable.groupby("parcel_class")["price_per_sqft"].agg(["median", "count"])
    print(by_class[by_class["count"] >= 5].sort_values("count", ascending=False))
    print(f"\nMedian $/building sqft, all usable valid sales: ${usable['price_per_sqft'].median():,.0f}")
    print(
        "\nNo parcel/GEOID join yet -- Maui has no build_model.py parcel/tract pipeline "
        "built (see this script's docstring). This is a cleaned county-wide table only."
    )


if __name__ == "__main__":
    main()
