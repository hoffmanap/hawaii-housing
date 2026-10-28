"""
Second, independent Hawai'i County sales source: user-pulled Zillow
"sold homes" search-result exports, geocoded to a census tract. Fills
the real gap process_hawaii_county_sales_exports.py's own docstring
states plainly -- that vendor's (Regrid) export only covers 2025-01-02
to 2025-05-09, a partial year, with no way to extend it (Hawai'i
County's own qPublic portal has no bulk sales export at all, confirmed
2026-10-27). These Zillow exports, by contrast, span roughly the last
24 months (first batch: 2024-10-28 to 2026-10-27), real spread across
that window, not clustered at one scrape date.

WHY A SEPARATE SCRIPT AND A SEPARATE OUTPUT FILE, not folded into the
Regrid one: the join key is completely different. Regrid's export
carries a parcel number (TMK), joined to this project's own parcel
layer directly. A Zillow listing carries only a street address -- no
parcel ID, no lat/lon, no coordinates of any kind -- so getting a
census tract out of it requires geocoding the address first, which is
its own fallible step with its own real match rate (see below). The
two outputs are concatenated at READ time by build_model.py's
_load_sales_joined() and build_dashboard_data.py's load_county_sales_csv()
instead of at write time here, so each source script stays independently
re-runnable without one clobbering the other's work.

THE MATCH RATE, run 2026-10-28 against the Census Bureau's own batch
geocoder (geocoding.geo.census.gov, Public_AR_Current/Current_Current):
1635 of 1944 parsed rows (84.1%) resolved to an exact address match with
a census tract; 309 (15.9%) did not (293 No_Match, 16 Tie -- an
ambiguous address with no single best candidate) and are carried in the
output with geoid_match=False and GEOID empty rather than being
silently dropped, so downstream code sees exactly how many usable rows
exist, not an inflated count. The lower-than-ideal match rate for this
county specifically is plausible, not a parsing bug: much of rural
Hawai'i Island uses a non-standard "zone-section-plat-parcel"-style
street numbering (e.g. "64-5300 Puukapu St") that the Census geocoder's
TIGER/Line reference address ranges often don't carry for sparse rural
subdivisions -- the same kind of address-matching gap documented for
other counties' non-standard numbering elsewhere in this project.

TWO REAL PARSING BUGS FOUND AND FIXED while building this (both would
have silently corrupted the data, not just thrown an error):
  1. Zillow abbreviates prices at or above $1,000,000 as e.g. "$1.36M"
     (and occasionally "$450K" for smaller amounts) instead of writing
     the full number. A naive digit-only strip turns "$1.36M" into 136 --
     off by a factor of ~10,000. parse_price() below handles the M/K
     suffix explicitly; 203 of 1944 rows (10.4%) needed it.
  2. 12 of 1944 rows have an implausible sub-$20,000 price (as low as
     $1,716) for houses reporting 700-4,268 sqft. Every one of these 12
     is ALSO missing both its broker and its "Sold" date -- a distinct,
     incomplete record shape found in no other row -- strongly
     suggesting a rental listing or a partial/corrupted scrape record
     that Zillow's "Sold" tab leaked in, not a genuine $1,716 home sale.
     Flagged valid_sale=False with a stated exclusion_reason rather than
     silently dropped or silently kept.

BUILDING SQFT IS ZILLOW'S OWN SELF-REPORTED LISTING SQFT, not an
assessor record -- a real, different definition than Regrid's
"LIVING AREA" field for the same county. Stated here, in
docs/DATA_PROVENANCE.md, and in the building_sqft_source column of the
output itself, never silently treated as the same thing.

TAXCLASS IS ASSUMED, NOT JOINED: there is no parcel ID to join against
this project's own taxclass/pittcode field, so every matched row is
assigned pittcode 100 (Residential) -- justified because every row in
this export already passed through Zillow's own "recently sold homes"
search filter (the bds/ba/sqft fields are only ever populated for
house/condo/townhome listings, never commercial), but still an
assumption, not a looked-up fact, and flagged as such in the output's
taxclass_source column.

Run after build_county("hawaii") (needs census_tracts.json, same as any
other geocode-based join in this project) and after placing export
batches in data/raw/sales_exports/hawaii_zillow/ (gitignored, same "user
supplies new batches as they're pulled" pattern as the Regrid script).
Geocoding results are cached by exact input address string in
data/raw/geocode_cache_hawaii_zillow.csv so re-running this script after
a new batch is added only geocodes the NEW addresses, not all of them
again.
"""
import csv
import glob
import re
import sys
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent))

SALES_DIR = Path(__file__).parent.parent / "data" / "raw" / "sales_exports" / "hawaii_zillow"
GEOCODE_CACHE_PATH = Path(__file__).parent.parent / "data" / "raw" / "geocode_cache_hawaii_zillow.csv"
OUT_PATH = Path(__file__).parent.parent / "data" / "processed" / "hawaii_zillow_sales_joined.csv"

CENSUS_BATCH_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/geographies/addressbatch"
ASSUMED_RESIDENTIAL_TAXCLASS = 100  # Hawai'i County pittcode for "Residential" -- see docstring


def parse_price(raw: str):
    """'$890,000' -> 890000; '$1.36M' -> 1360000; '$450K' -> 450000."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    m = re.match(r"^\$?([\d,.]+)\s*([MK])?$", raw.strip(), re.IGNORECASE)
    if not m:
        return None
    num_str, suffix = m.groups()
    try:
        num = float(num_str.replace(",", ""))
    except ValueError:
        return None
    if suffix:
        num *= 1_000_000 if suffix.upper() == "M" else 1_000
    return int(round(num))


def load_and_parse_raw_exports() -> pd.DataFrame:
    """Parses every raw Zillow search-result CSV in SALES_DIR into one
    flat table of sold-listing records, deduped by Zillow's own zpid
    (the stable per-listing ID embedded in each listing's URL)."""
    files = sorted(glob.glob(str(SALES_DIR / "*.csv")))
    if not files:
        raise RuntimeError(
            f"No Zillow export batches found in {SALES_DIR} -- place CSV exports from Zillow's "
            "'sold homes' search results there first."
        )
    rows, seen_urls = [], set()
    skipped_dup = skipped_no_price = 0
    for fname in files:
        with open(fname, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader)  # header
            for rec in reader:
                if not rec or len(rec) < 11:
                    continue
                url = rec[0]
                if url in seen_urls:
                    skipped_dup += 1
                    continue
                seen_urls.add(url)

                price = parse_price(rec[1])
                if not price:
                    skipped_no_price += 1
                    continue

                sold_field = next((c for c in rec if isinstance(c, str) and c.startswith("Sold ")), None)
                sale_date = None
                if sold_field:
                    m = re.search(r"(\d{2})/(\d{2})/(\d{2})", sold_field)
                    if m:
                        mm, dd, yy = m.groups()
                        sale_date = f"{int(yy) + 2000:04d}-{mm}-{dd}"

                zpid_m = re.search(r"/(\d+)_zpid/", url)
                rows.append({
                    "zpid": zpid_m.group(1) if zpid_m else None,
                    "address": rec[9],
                    "sale_price_usd": price,
                    "sale_date": sale_date,
                    "building_sqft": pd.to_numeric(re.sub(r"[^\d]", "", rec[6] or ""), errors="coerce"),
                    "beds": rec[2],
                    "baths": rec[4],
                    "broker": rec[10] or None,
                    "source_file": Path(fname).name,
                })
    print(f"  parsed {len(rows)} rows from {len(files)} batch file(s); "
          f"skipped {skipped_dup} duplicate zpids, {skipped_no_price} unparseable prices")
    return pd.DataFrame(rows)


def flag_valid_sales(df: pd.DataFrame) -> pd.DataFrame:
    """12/1944 rows in the first batch had an implausible sub-$20k price
    paired with no broker and no sold-date -- a distinct, incomplete
    record shape (see module docstring). Generalized as a rule so future
    batches get the same check automatically."""
    suspect = df["broker"].isna() & df["sale_date"].isna() & (df["sale_price_usd"] < 20_000)
    df["valid_sale"] = ~suspect
    df["exclusion_reason"] = ""
    df.loc[suspect, "exclusion_reason"] = (
        "incomplete scrape record (no broker, no sold-date) with implausible sub-$20k price -- "
        "likely a rental listing or partial record misclassified as Sold"
    )
    return df


def geocode_addresses(addresses: list[str]) -> pd.DataFrame:
    """Batch-geocodes a list of address strings via the Census Bureau's
    free public geocoder, returning tract GEOID directly (the
    'geographies' endpoint, not just lat/lon) -- avoids a second spatial-
    join step. Results are cached by address so a re-run only geocodes
    addresses not already in the cache."""
    cache = pd.DataFrame(columns=["address", "GEOID", "match_status", "lat", "lon"])
    if GEOCODE_CACHE_PATH.exists():
        cache = pd.read_csv(GEOCODE_CACHE_PATH, dtype={"GEOID": str})

    to_geocode = [a for a in addresses if a not in set(cache["address"])]
    print(f"  {len(addresses) - len(to_geocode)} addresses already cached; geocoding {len(to_geocode)} new")

    if to_geocode:
        batch_path = Path("/tmp/hawaii_zillow_geocode_batch.csv")
        with open(batch_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for i, addr in enumerate(to_geocode):
                m = re.match(r"^(.*),\s*([^,]+),\s*HI\s*(\d{5})", addr)
                if m:
                    street, city, zipc = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
                else:
                    street, city, zipc = addr, "", ""
                writer.writerow([i, street, city, "HI", zipc])

        resp = requests.post(
            CENSUS_BATCH_GEOCODER_URL,
            files={"addressFile": ("batch.csv", open(batch_path, "rb"), "text/csv")},
            data={"benchmark": "Public_AR_Current", "vintage": "Current_Current"},
            timeout=300,
        )
        resp.raise_for_status()
        cols = ["id", "input_addr", "match_status", "match_type", "matched_addr",
                "latlon", "tigerlineid", "side", "state_fips", "county_fips", "tract", "block"]
        result = pd.read_csv(pd.io.common.StringIO(resp.text), names=cols, dtype=str)
        result["id"] = result["id"].astype(int)
        new_rows = []
        for i, addr in enumerate(to_geocode):
            r = result[result["id"] == i]
            if len(r) and r.iloc[0]["match_status"] == "Match":
                row = r.iloc[0]
                geoid = f"{row['state_fips']}{row['county_fips']}{row['tract']}"
                lat, lon = (row["latlon"].split(",")[::-1] if pd.notna(row["latlon"]) else (None, None))
                new_rows.append({"address": addr, "GEOID": geoid, "match_status": "Match", "lat": lat, "lon": lon})
            else:
                status = r.iloc[0]["match_status"] if len(r) else "No_Match"
                new_rows.append({"address": addr, "GEOID": None, "match_status": status, "lat": None, "lon": None})
        cache = pd.concat([cache, pd.DataFrame(new_rows)], ignore_index=True)
        cache.to_csv(GEOCODE_CACHE_PATH, index=False)

    return cache


def main() -> None:
    print("Parsing raw Zillow export batches...")
    df = load_and_parse_raw_exports()
    df = flag_valid_sales(df)

    print("Geocoding addresses (cached by exact address string)...")
    geo = geocode_addresses(df["address"].tolist())
    df = df.merge(geo, on="address", how="left")
    df["geoid_match"] = df["match_status"] == "Match"

    df["price_per_sqft"] = df["sale_price_usd"] / df["building_sqft"]
    df.loc[df["building_sqft"] <= 0, "price_per_sqft"] = pd.NA
    df["building_sqft_source"] = "zillow_self_reported_listing_sqft"
    df["taxclass"] = ASSUMED_RESIDENTIAL_TAXCLASS
    df["taxclass_source"] = "assumed_residential_no_parcel_join_possible_from_address_alone"
    df["data_source"] = "zillow_sold_listing_scrape"

    df.to_csv(OUT_PATH, index=False)

    n = len(df)
    n_geo = int(df["geoid_match"].sum())
    n_valid = int(df["valid_sale"].sum())
    n_usable = int((df["geoid_match"] & df["valid_sale"] & df["building_sqft"].gt(0)).sum())
    print(f"\n{n} total parsed rows -> {OUT_PATH}")
    print(f"  {n_geo}/{n} ({100*n_geo/n:.1f}%) geocoded to a census tract")
    print(f"  {n_valid}/{n} ({100*n_valid/n:.1f}%) flagged valid_sale")
    print(f"  {n_usable}/{n} ({100*n_usable/n:.1f}%) usable for $/sqft comps "
          "(geocoded + valid_sale + building_sqft > 0)")
    if df["sale_date"].notna().any():
        print(f"  Sale date range: {df['sale_date'].min()} to {df['sale_date'].max()}")
    print(f"  {df['sale_date'].isna().sum()} rows missing a parsed sold-date (Zillow didn't supply one)")


if __name__ == "__main__":
    main()
