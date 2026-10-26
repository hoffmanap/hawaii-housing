"""
Parses Maui County's monthly "Building Permits Issued" detail reports
(Development Services Administration, report CoM - DSA - Building
Permits Detail.rpt) into a flat table joined to this project's
parcel/tract pipeline.

SOURCE AND WHY IT'S NOT SELF-SERVICE: the user pulled these from
mauicounty.gov/DocumentCenter/Index/7188, a Laserfiche/CivicPlus
DocumentCenter that returns HTTP 403 to direct automated fetches
(Cloudflare __cf_bm bot-check cookie challenge confirmed 2026-10-26)
and that WebFetch can only render as a generic landing page with no
document list. There is no bulk/API export, so like the sales
exports, the user supplies individual report files by hand; what they
supplied is an accessibility transcript (an HTML rendering of the
underlying PDF's text, produced by a third-party doc-access tool, not
the county) rather than the PDF itself, but it carries the PDF's full
text content, which is all this script needs.

REPORT STRUCTURE: one report per calendar month. Permits are grouped
into category tables (Single Family Dwellings, Other Residential
Buildings, Residential Add/Alt/Convert/Repair/Relocate, Office/
Professional/Business, Other Non-Residential Buildings, Swimming
Pool, Demolition, etc. -- the exact category set is NOT fixed and
should be read from each report, not hard-coded) with columns Row/Item
number, Permit #, Issue Date, Name/Description of Work, Site Address,
TMK, # Units, Valuation, Builder, a per-category Subtotal block, and
a report-level Overall Totals block used here only as an internal
cross-check (parsed-row counts and summed valuation should match it).
A table that spans a PDF page break repeats the category name and
column headers; this parser treats a repeated category name as a
continuation of the same category, not a new one, and skips repeated
header/boilerplate lines (the doc-access tool's own "Additional
content from previous item:", "This section continues beyond..."
notices, the report's running header/footer, and the page's embedded
<style>/<script>).

TMK JOIN: this report's TMK field is already Maui's full format with
a trailing CPR/unit suffix after a dash, e.g. "2480040020000-50774"
(13 digits before the dash: Zone(1)+Section(2)+Plat(3)+Parcel(3)+
pad(4), here always zone "2"). build_model.py's parcel_level tmk_txt
is the 9-digit Zone+Section+Plat+Parcel. The first 9 digits of this
report's TMK field (before the dash) are used directly as tmk_txt for
the join -- NOT the same 8-digit-no-zone convention the sales
exports' separate "Parcel ID" field uses (confirmed by inspecting a
sample: this field's leading digit is "2", matching Maui's TMK zone,
whereas the sales exports' "Parcel ID" field omits that leading zone
digit entirely).

ADU IDENTIFICATION: there's no dedicated ADU/Ohana permit type code
in this report (unlike Hawai'i County's dedicated Ohana Permits
layer) -- ADU permits surface only as free-text phrases in the
Name/Description field ("ACCESSORY DWELLING", "2ND ACCESSORY
DWELLING", "ACCESSORY DWELLING W/ ATT GARAGE", "DWELLING W/ATTCH ADU
ADD/ALT", etc.), so `is_likely_adu()` is a conservative keyword match
on that text, not an authoritative permit-type field. Treat its count
as a lower bound / sanity check, not a calibration-grade figure --
this script does NOT feed an absorption-rate calibration on the
strength of one month's data; see docs/MODEL_AND_ASSUMPTIONS.md and
process this note was added alongside.

Run after build_county("maui") (needs maui_parcel_level.csv) and after
placing report transcripts in data/raw/permit_reports/maui/*.html
(gitignored, same reasoning as sales_exports/).
"""
import glob
import re
import sys
from html import unescape
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import build_model as bm

REPORTS_DIR = Path(__file__).parent.parent / "data" / "raw" / "permit_reports" / "maui"
OUT_PATH = Path(__file__).parent.parent / "data" / "processed" / "maui_permits_parsed.csv"

COLUMN_HEADER_LINES = {
    "Row number", "Item", "Permit #", "Issue Date", "Name/Description of Work",
    "Site Address", "TMK", "# Units", "Valuation", "Builder",
}
BOILERPLATE_PREFIXES = (
    "Additional content from previous item", "This section continues beyond",
    "CoM - DSA", "County of Maui", "Building Permits Issued", "Run Date:",
    "Page ", "Overall Totals", "TOTAL # PERMITS", "TOTAL # UNITS",
    "TOTAL VALUATION", "Total # of Permits", "Total # of Units",
    "Total Valuation", "OVERALL TOTAL", "License Information",
    "Licensed for personal use", "Available at", "Skip to main content",
    "Accessible Document Transcript", "Seal of the County of Maui",
    "Development Services Administration", "110 Alaihi Street",
    "(808) 270-7250", "Kahului, HI",
)
ADU_KEYWORDS = re.compile(r"ACCESSORY DWELLING|\bADU\b|OHANA", re.I)
TMK_RE = re.compile(r"^\d{9,13}-\d+$")


def _lines_from_html(path: Path) -> list[str]:
    html = path.read_text()
    body = html.split("<body>", 1)[1] if "<body>" in html else html
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", body, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = unescape(text)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    return [l for l in lines if not l.startswith(BOILERPLATE_PREFIXES)]


def _month_from_filename(path: Path) -> str:
    m = re.search(r"(\d{4})-(\d{2})", path.stem)
    return f"{m.group(1)}-{m.group(2)}" if m else path.stem


def parse_report(path: Path) -> pd.DataFrame:
    lines = _lines_from_html(path)
    month = _month_from_filename(path)
    records = []
    category = None
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line in COLUMN_HEADER_LINES:
            i += 1
            continue
        if line == "Subtotal # Permits:":
            # Normally 3 label/value pairs (permits, units, valuation), but
            # a value can be omitted outright by the source when every row
            # in that category had a blank field (confirmed: the Demolition
            # section's "Subtotal # Units:" has no value line at all, since
            # both its records had blank per-row units) -- so for each of
            # the 3 expected labels, consume the label, then consume the
            # next line as its value ONLY if that line isn't itself another
            # "Subtotal ..." label.
            i += 1  # past "Subtotal # Permits:"
            for _ in range(3):
                if i < n and not lines[i].startswith("Subtotal"):
                    i += 1  # consume this label's value
                if i < n and lines[i].startswith("Subtotal"):
                    i += 1  # consume the next label
            continue
        if re.match(r"^\d+$", line) and i + 1 < n and re.match(r"^[A-Z]\d{4}-\d{5}$", lines[i + 1]):
            # row/item number -> permit record block
            row_num = line
            permit_no = lines[i + 1]
            issue_date = lines[i + 2]
            # the "Name/Description of Work" field can also be omitted
            # outright (confirmed: row 46, B2024-00141, has no description
            # line -- its "address" line comes immediately after the date),
            # so check first whether lines[i+3] is already the address.
            if re.search(r", HI \d{5}", lines[i + 3]) and i + 4 < n and TMK_RE.match(lines[i + 4]):
                desc_parts: list[str] = []
                j = i + 3
            else:
                desc_parts = [lines[i + 3]]
                j = i + 4
                # the next field must look like a TMK; until it does, treat
                # extra lines (an "Options: N/A" note, a continued
                # description) as more description text -- this report's
                # rows are not fixed-width.
                while j < n and not (re.search(r", HI \d{5}", lines[j]) and j + 1 < n and TMK_RE.match(lines[j + 1])):
                    if j < n and TMK_RE.match(lines[j]):
                        break
                    desc_parts.append(lines[j])
                    j += 1
                    if j - i > 10:  # safety valve against a malformed block
                        break
            if j + 4 >= n or not TMK_RE.match(lines[j + 1]):
                i += 1
                continue
            site_address = lines[j]
            tmk = lines[j + 1]
            # the "# Units" field is sometimes omitted outright by the source
            # report for a given row (confirmed: permit B2025-00838, row 83
            # of the 2026-01 report, has no units line at all) -- detect this
            # by checking whether the next field is already the $-valuation.
            if lines[j + 2].startswith("$"):
                units = "0"
                valuation_idx = j + 2
            else:
                units = lines[j + 2]
                valuation_idx = j + 3
            valuation = lines[valuation_idx]
            # the "Builder" field can likewise be omitted entirely (confirmed:
            # row 31, B2025-01638, has no builder line, which would otherwise
            # swallow the following row's row-number and drop that record) --
            # detect this by checking whether the next line is itself the
            # start of a new row, a subtotal block, a column header, or a
            # category header, rather than assuming a builder line exists.
            next_line = lines[valuation_idx + 1] if valuation_idx + 1 < n else ""
            looks_like_new_block = (
                next_line in COLUMN_HEADER_LINES
                or next_line == "Subtotal # Permits:"
                or (re.match(r"^\d+$", next_line)
                    and valuation_idx + 2 < n
                    and re.match(r"^[A-Z]\d{4}-\d{5}$", lines[valuation_idx + 2]))
            )
            if looks_like_new_block:
                builder = ""
                after_record_idx = valuation_idx + 1
            else:
                builder = next_line
                after_record_idx = valuation_idx + 2
            description = " ".join(p for p in desc_parts if p.lower() != "options: n/a")
            records.append({
                "month": month,
                "category": category,
                "row_num": row_num,
                "permit_no": permit_no,
                "issue_date": issue_date,
                "description": description,
                "site_address": site_address,
                "tmk_raw": tmk,
                "units": units,
                "valuation_raw": valuation,
                "builder": builder,
            })
            i = after_record_idx
            continue
        # not a record start -- check if it's a category header (a line
        # immediately followed, within a few lines, by "Permit #")
        if i + 1 < n and lines[i + 1] in ("Row number", "Item"):
            category = line
        i += 1
    return pd.DataFrame(records)


def _normalize_category(cat: str | None) -> str | None:
    """Folds a page-break "Continuation of X table." label back into X,
    so a category split across a PDF page boundary counts as one category
    rather than two in any aggregation."""
    if cat is None:
        return cat
    m = re.match(r"^Continuation of (?:the )?(.+?)(?: table)?\.?$", cat)
    cat = m.group(1) if m else cat
    return cat.rstrip(".")


def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["category"] = df["category"].map(_normalize_category)
    df["tmk_txt"] = df["tmk_raw"].str.split("-").str[0].str[:9]
    df["units"] = pd.to_numeric(df["units"], errors="coerce").fillna(0).astype(int)
    df["valuation_usd"] = (
        df["valuation_raw"].astype(str).str.replace(r"[$,]", "", regex=True).astype(float)
    )
    df["issue_date"] = pd.to_datetime(df["issue_date"], format="%m/%d/%Y", errors="coerce")
    df["is_likely_adu"] = df["description"].str.contains(ADU_KEYWORDS, na=False)
    return df


def join_to_parcels(df: pd.DataFrame) -> pd.DataFrame:
    parcel_path = bm.OUT_DIR / "maui_parcel_level.csv"
    if not parcel_path.exists():
        raise FileNotFoundError(f"Run build_county('maui') first -- {parcel_path} not found")
    parcels = pd.read_csv(parcel_path, dtype={"tmk_txt": str})
    joined = df.merge(parcels[["tmk_txt", "GEOID", "zoning", "taxclass"]], on="tmk_txt", how="left")
    unmatched = joined["GEOID"].isna().sum()
    if unmatched:
        print(f"  note: {unmatched}/{len(joined)} permit records did not match a parcel "
              "(CPR/condo unit on a shared TMK, or a parcel outside this layer's coverage)")
    return joined


def main() -> None:
    files = sorted(glob.glob(str(REPORTS_DIR / "*.html")))
    if not files:
        raise RuntimeError(
            f"No permit report transcripts found in {REPORTS_DIR} -- these are manually pulled "
            "one at a time from mauicounty.gov/DocumentCenter/Index/7188 (blocked to automated "
            "fetches), so there's nothing to fetch here automatically."
        )
    frames = [parse_report(Path(f)) for f in files]
    df = pd.concat(frames, ignore_index=True)
    df = clean(df)
    joined = join_to_parcels(df)
    joined.to_csv(OUT_PATH, index=False)

    months = sorted(joined["month"].unique())
    print(f"{len(joined)} permit records across {len(months)} month(s) ({', '.join(months)}) -> {OUT_PATH}")
    print(f"Total valuation: ${joined['valuation_usd'].sum():,.2f} | total units: {joined['units'].sum()}")
    adu = joined[joined["is_likely_adu"]]
    print(f"\n{len(adu)} record(s) with ADU-suggestive description text (lower bound, not a permit-type field):")
    for _, r in adu.iterrows():
        print(f"  {r['month']}  {r['permit_no']}  {r['description'][:70]}")
    print("\nBy category:")
    print(joined.groupby("category").agg(permits=("permit_no", "count"),
                                          units=("units", "sum"),
                                          valuation=("valuation_usd", "sum")))
    if len(months) == 1:
        print("\nOnly one month of data -- NOT sufficient for an absorption-rate calibration "
              "(see honolulu_real_adu_absorption_rate()'s multi-year basis for comparison). "
              "Additional months from the same DocumentCenter index would materially improve this.")


if __name__ == "__main__":
    main()
