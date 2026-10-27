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
TMK_ANYWHERE_RE = re.compile(r"\d{9,13}-\d+$")


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


# Ground truth for this-month-only totals, read by hand from each report's
# own year-to-date recap table ("Total # of Permits Total # of Units Total
# Valuation" / "<Month> <permits> <units> <valuation>") -- NOT derived from
# this parser, so they're a real cross-check, not a tautology. Extend this
# as more months are supplied; a month absent here just skips the check.
MONTHLY_GROUND_TRUTH = {
    "2026-01": (84, 49, 73231387.52),
    "2026-02": (93, 52, 32818229.88),
    "2026-03": (77, 54, 29691351.38),
    "2026-04": (67, 39, 26931710.45),
    "2026-05": (177, 72, 75862973.70),
    "2026-06": (91, 72, 48803963.07),
    "2026-07": (140, 208, 79175425.28),
    "2026-08": (74, 26, 48877872.75),
    "2026-09": (78, 36, 50382901.69),
    "2025-01": (133, 145, 104252775.00),
    # 2025-02 deliberately excluded: that month's supplied PDF has a broken
    # text layer (embedded fonts with a corrupt ToUnicode CMap -- confirmed
    # via pdftotext -raw/-layout both returning empty text and pdffonts
    # showing fonts embedded anyway; likely a "Microsoft: Print To PDF"
    # artifact per pdfinfo). An OCR attempt (parse_ocr_report(), tesseract
    # on rasterized pages) cleanly recovered only 36 of 132 rows and
    # produced an implausible aggregate (units summed to 1,296 against a
    # true total of 102) -- too unreliable to trust, so this month is
    # excluded from the dataset entirely rather than fed in corrupted.
    # February's own report total, for reference, not used: (132, 102,
    # 47,670,991.70) -- note this also differs by 1 unit from the 103
    # December's cumulative recap shows for February, the same kind of
    # later-revision discrepancy documented for 2025-09 below.
    # 2025-03's own report footer prints valuation as $117,045,504.97; this
    # parser's summed valuation differs by $1.03 on 109 records (permit and
    # unit counts match exactly) -- the same kind of tiny source-PDF-text-
    # extraction/rounding artifact documented for 2026-05 above, not a
    # parsing bug.
    "2025-03": (109, 121, 117045504.97),
    # 2025-04 and 2025-05's OWN report footers print (156, 138, $85,267,178)
    # and (103, 49, $72,126,635.36) respectively -- matching this parser
    # exactly. December 2025's cumulative year-to-date recap instead shows
    # 139 and 50 units for these months (one more each), the same kind of
    # later-revision discrepancy documented for 2025-09 above. Ground truth
    # here is each month's OWN report, since that's what this parser
    # parses.
    "2025-04": (156, 138, 85267178.00),
    "2025-05": (103, 49, 72126635.36),
    "2025-06": (96, 58, 29911933.00),
    "2025-07": (143, 65, 107874410.00),
    "2025-08": (98, 59, 39105009.00),
    # September 2025's OWN report (2025-09.txt) prints its own total as
    # (111, 76, $51,684,520.66), confirmed by reading that file directly.
    # December 2025's report, in its cumulative year-to-date recap table,
    # instead shows September as (112, 77, $51,834,520.66) -- one more
    # permit and ~$150K more valuation. This isn't a parser discrepancy:
    # the September PDF genuinely lists only 111 permit rows; the 112th
    # permit the December recap counts was evidently entered into the
    # county's system sometime after September's own report ran (a
    # real-world reporting-lag artifact, not a data-entry error this
    # parser could or should correct). Ground truth here is September's
    # OWN report, since that's what this parser is actually parsing.
    "2025-09": (111, 76, 51684520.66),
    "2025-10": (95, 58, 35670831.00),
    "2025-11": (63, 39, 19125072.00),
    "2025-12": (103, 37, 25819392.51),
}

PDF_BOILERPLATE_PREFIXES = (
    "CoM - DSA", "COUNTY OF MAUI", "DEVELOPMENT SERVICES ADMINISTRATION",
    "110 ALAIHI STREET", "KAHULUI, HI", "(808) 270-7250", "BUILDING PERMITS ISSUED",
    "Run Date:", "Permit # Name/Description", "Site Address", "Issue Date",
    "Subtotal Valuation:", "Subtotal # Units:", "Subtotal # Permits:",
    "Total # of Permits", "OVERALL TOTAL:", "TOTAL # PERMITS", "UNITS:",
    "TOTAL VALUATION:",
)
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December")
PDF_ROW_RE = re.compile(r"^(\d+)\s+\$([\d,]+)$")
PDF_PERMIT_RE = re.compile(r"^([A-Z]\d{4}-\d{5})\s*(.*)$")
PDF_DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
HI_ZIP_RE = re.compile(r"HI\s+\d{5}")


def _lines_from_pdf_raw_text(path: Path) -> list[str]:
    raw_lines = [l.strip() for l in path.read_text().split("\n") if l.strip()]
    lines = []
    for l in raw_lines:
        if l.startswith(PDF_BOILERPLATE_PREFIXES) or l.startswith(MONTH_NAMES):
            continue
        # a lone month-range line, e.g. "February 01, 2026   to   February 28, 2026"
        if re.match(r"^[A-Za-z]+ \d{1,2}, \d{4}\s+to\s+[A-Za-z]+ \d{1,2}, \d{4}$", l):
            continue
        lines.append(l)
    return lines


def parse_pdf_report(path: Path) -> pd.DataFrame:
    """Parses a monthly report extracted with `pdftotext -raw` (a different,
    more scrambled field order than the HTML transcript's one-field-per-line
    layout -- see module docstring's "PDF (-raw) FORMAT" note). Reusing the
    HTML parser's line-token approach isn't possible: -raw emits each
    record's fields in the fixed but non-reading-order sequence
    row+valuation, units, description (multi-line), issue date, permit#+
    builder-first-fragment, then an unlabeled run of further builder lines
    and 1-2 site-address blocks (order between them not separately marked),
    then 1-2 TMK lines -- discovered by inspecting several categories
    including one ("Two Unit Buildings") where a single permit carries TWO
    site addresses sharing ONE TMK, and another where a single address
    carries TWO TMK lines (a multi-unit TMK's CPR-suffixed children)."""
    lines = _lines_from_pdf_raw_text(path)
    month = _month_from_filename(path)
    records = []
    category = None
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = PDF_ROW_RE.match(line)
        if m:
            row_num, valuation = m.group(1), m.group(2)
            # the "# Units" field is sometimes omitted outright (confirmed:
            # rows 91-92 of the 2026-02 report have no units line at all --
            # the description starts immediately after row+valuation), so
            # only consume the next line as units if it's a bare integer.
            if i + 1 < n and re.match(r"^\d+$", lines[i + 1]):
                units = lines[i + 1]
                j = i + 2
            else:
                units = "0"
                j = i + 1
            # description: collect lines until the issue-date line
            desc_parts = []
            while j < n and not PDF_DATE_RE.match(lines[j]):
                desc_parts.append(lines[j])
                j += 1
                if j - i > 10:
                    break
            if j >= n:
                i += 1
                continue
            issue_date = lines[j]
            j += 1
            pm = PDF_PERMIT_RE.match(lines[j]) if j < n else None
            if not pm:
                i += 1
                continue
            permit_no = pm.group(1)
            builder_first = pm.group(2)
            j += 1
            # collect everything up to (but not including) the first TMK
            # line. A TMK line can have a stray leading ", " glued onto it
            # when the row's Site Address field is otherwise blank
            # (confirmed: row 92 of the 2026-02 report: ", 2220050040000-
            # 5338") -- so search for the TMK pattern anywhere in the line,
            # not just as a full-line match, via TMK_ANYWHERE_RE.
            pre_tmk = []
            while j < n and not TMK_ANYWHERE_RE.search(lines[j]):
                # a record with NO address/TMK at all (confirmed: permit
                # B2026-00593, "COUNTY OF MAUI - TESTER" in the 2026-06
                # report, has no site address or TMK whatsoever -- a
                # placeholder/test permit) must not run away consuming the
                # rest of the file waiting for a TMK that never comes.
                # Detect that we've reached the NEXT record's start instead
                # of using an arbitrary line-count cap (legitimate
                # builder+address runs vary in length): the next record
                # always starts with its own "<row> $<valuation>" line,
                # a pattern nothing in a builder/address field matches.
                if PDF_ROW_RE.match(lines[j]):
                    break
                pre_tmk.append(lines[j])
                j += 1
                if j - i > 30:  # last-resort safety valve against a truly malformed file
                    break
            if j < n and TMK_ANYWHERE_RE.search(lines[j]):
                tmk = TMK_ANYWHERE_RE.search(lines[j]).group(0)  # strips any glued prefix
                j += 1      # right after is a sub-unit of the same parcel, skip it
                while j < n and TMK_RE.match(lines[j]):
                    j += 1
            elif not any(HI_ZIP_RE.search(pl) for pl in pre_tmk):
                # no TMK found, and nothing in pre_tmk contains an address's
                # "HI #####" pattern either -- a genuine TMK-less record,
                # not a parsing failure (what IS in pre_tmk at this point is
                # leftover orphaned numeric fragments from the report's own
                # split subtotal/grand-total labels, e.g. a lone "1" or
                # "72 $48,803,963.07" -- harmless noise, not an address).
                # Keep the record (it still counts toward the month's
                # permit/unit/valuation totals) with a blank TMK, which will
                # simply fail to join to a parcel downstream.
                tmk = ""
                pre_tmk = []  # discard that leftover noise -- not builder/address text
            else:
                i += 1
                continue
            # split pre_tmk into (builder continuation) + (address block(s)):
            # address starts at the first line beginning with a digit that
            # is itself, or within the next couple of lines, followed by a
            # "HI #####" pattern -- confirmed against the dual-address and
            # 3-line-street-wrap cases (see docstring).
            addr_start = len(pre_tmk)
            for k, pl in enumerate(pre_tmk):
                if re.match(r"^\d", pl) and any(HI_ZIP_RE.search(x) for x in pre_tmk[k:k + 3]):
                    addr_start = k
                    break
            builder_lines = pre_tmk[:addr_start]
            site_address = " ".join(pre_tmk[addr_start:])
            builder = " ".join([builder_first] + builder_lines) if builder_lines else builder_first
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
                "valuation_raw": "$" + valuation,
                "builder": builder,
            })
            i = j
            continue
        if line in MONTH_NAMES:
            i += 1
            continue
        # category header: a line containing at least one letter, not itself
        # boilerplate, immediately followed by a record's row+valuation
        # line. Requiring a letter excludes an orphaned numeric fragment
        # left over from a split subtotal label (confirmed: a lone "6" --
        # really "Subtotal # Permits: 6" with its label filtered out as
        # boilerplate -- sitting between one category's last TMK and the
        # next category's first record, which would otherwise be
        # misread as that next category's name).
        if i + 1 < n and PDF_ROW_RE.match(lines[i + 1]) and re.search(r"[A-Za-z]", line):
            category = line
        i += 1
    return pd.DataFrame(records)


OCR_ROW_START_RE = re.compile(r"^(\d+)\.?\s+([A-Z]\d{4}-\d{5})\s+(\d{1,2}/\d{1,2}/\d{4})\s+(.*)$")
OCR_TMK_RE = re.compile(r"(\d(?:[\d ]{8,15})-\d+)")
OCR_VALUATION_RE = re.compile(r"\$([\d,]+(?:\.\d+)?)")
OCR_UNITS_BEFORE_VALUATION_RE = re.compile(r"(?:^|\s)(\d+)\s*$")


def parse_ocr_report(path: Path) -> pd.DataFrame:
    """Parses a monthly report OCR'd with tesseract (from `pdftoppm` page
    images), used ONLY for the one supplied PDF (2025-02) whose embedded
    text layer is empty despite having embedded fonts -- confirmed via
    `pdftotext -raw/-layout` both returning blank text and `pdffonts`
    showing embedded CID TrueType fonts anyway; `pdfinfo` shows "Producer:
    Microsoft: Print To PDF", a known source of broken ToUnicode CMaps
    that make a font's glyphs unmappable to text even though pdftotext
    has no way to know that in advance. OCR text is noisy in ways the
    other two parsers' sources aren't (extra/missing spaces inside
    numbers, inserted stray characters like "=" or "—", garbled
    "Subtotal # Permits;" punctuation, "TK" for "TMK"), so this parser
    takes a field-by-field extraction approach (search for each field's
    own pattern anywhere on the row-start line) rather than a strict
    positional grammar the way the other two parsers can afford to.
    It does NOT attempt to reconstruct the full multi-line site address
    (the city/state/zip often lands on a separate OCR line, sometimes
    reordered around a page break) -- only the TMK, units, valuation,
    and description (for ADU keyword matching) are extracted, which is
    everything the absorption-rate calibration actually needs; site
    address exists in the output only to the extent it appears on each
    record's own first line."""
    raw_lines = [l.rstrip() for l in path.read_text().split("\n")]
    month = _month_from_filename(path)
    records = []
    category = None
    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in ("Permit # Issue Date Name/Description of Work Site Address TMK # Units Valuation Builder",):
            continue
        if re.match(r"^[A-Za-z ,'\.]+$", stripped) and len(stripped) < 60 and not stripped.startswith("Options"):
            # a plausible category header: short, all-letters-and-punctuation
            category = stripped
            continue
        m = OCR_ROW_START_RE.match(stripped)
        if not m:
            continue
        row_num, permit_no, issue_date, rest = m.groups()
        tmk_m = OCR_TMK_RE.search(rest)
        if not tmk_m:
            continue  # can't join or validate without a TMK; drop rather than guess
        tmk = re.sub(r"\s+", "", tmk_m.group(1))
        before_tmk = rest[: tmk_m.start()]
        after_tmk = rest[tmk_m.end():]
        val_m = OCR_VALUATION_RE.search(after_tmk)
        if not val_m:
            continue
        valuation = val_m.group(1)
        units_m = OCR_UNITS_BEFORE_VALUATION_RE.search(after_tmk[: val_m.start()])
        units = units_m.group(1) if units_m else "0"
        builder = after_tmk[val_m.end():].strip()
        description = before_tmk.strip().rstrip(",")
        records.append({
            "month": month,
            "category": category,
            "row_num": row_num,
            "permit_no": permit_no,
            "issue_date": issue_date,
            "description": description,
            "site_address": "",  # not reconstructed -- see docstring
            "tmk_raw": tmk,
            "units": units,
            "valuation_raw": "$" + valuation,
            "builder": builder,
        })
    return pd.DataFrame(records)


def _normalize_category(cat: str | None) -> str | None:
    """Folds a page-break "Continuation of X table." label back into X, and
    collapses whitespace, so a category split across a PDF page boundary,
    or spelled with inconsistent spacing by the source report itself
    (confirmed: some months print "Repair, Relocate", others "Repair,
    Relocate" with no space before "Relocate"), counts as one category
    rather than two in any aggregation."""
    if cat is None:
        return cat
    m = re.match(r"^Continuation of (?:the )?(.+?)(?: table)?\.?$", cat)
    cat = m.group(1) if m else cat
    cat = re.sub(r",(\S)", r", \1", cat)  # "Repair,Relocate" -> "Repair, Relocate"
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
    html_files = sorted(glob.glob(str(REPORTS_DIR / "*.html")))
    txt_files = sorted(glob.glob(str(REPORTS_DIR / "*.txt")))
    if not html_files and not txt_files:
        raise RuntimeError(
            f"No permit report files found in {REPORTS_DIR} -- these are manually pulled "
            "one at a time from mauicounty.gov/DocumentCenter/Index/7188 (blocked to automated "
            "fetches), so there's nothing to fetch here automatically."
        )
    frames = [parse_report(Path(f)) for f in html_files] + [parse_pdf_report(Path(f)) for f in txt_files]
    df = pd.concat(frames, ignore_index=True)
    df = clean(df)
    joined = join_to_parcels(df)
    joined.to_csv(OUT_PATH, index=False)

    months = sorted(joined["month"].unique())
    print(f"{len(joined)} permit records across {len(months)} month(s) ({', '.join(months)}) -> {OUT_PATH}")
    print(f"Total valuation: ${joined['valuation_usd'].sum():,.2f} | total units: {joined['units'].sum()}")

    print("\nReconciliation against each report's own printed month total "
          "(MONTHLY_GROUND_TRUTH, read by hand, not derived from this parser):")
    all_match = True
    for month in months:
        mdf = joined[joined["month"] == month]
        parsed = (len(mdf), int(mdf["units"].sum()), round(float(mdf["valuation_usd"].sum()), 2))
        truth = MONTHLY_GROUND_TRUTH.get(month)
        if truth is None:
            print(f"  {month}: parsed {parsed} -- no ground truth on file, not checked")
            continue
        ok = parsed[0] == truth[0] and parsed[1] == truth[1] and abs(parsed[2] - truth[2]) < 1.0
        all_match &= ok
        flag = "OK" if ok else "MISMATCH"
        print(f"  {month}: parsed {parsed} vs report {truth}  [{flag}]")
    if not all_match:
        print("\n  WARNING: at least one month did not reconcile exactly. As of 2026-10-27 the "
              "only known cases are tiny valuation-only rounding gaps (2025-03: $1.03 on 109 "
              "records; 2026-05: $2.70 on 68 records in one category), both verified by hand-"
              "summing the raw extracted digit strings independently of this parser and getting "
              "this same figure, pointing to the source PDF's own text extraction (not a parsing "
              "bug). Permit counts and unit counts match exactly for every month. Any OTHER "
              "mismatch, or any permit/unit-count mismatch, is new and should be investigated "
              "before trusting this run.")

    adu = joined[joined["is_likely_adu"]]
    print(f"\n{len(adu)} record(s) with ADU-suggestive description text (lower bound, not a permit-type field):")
    for _, r in adu.iterrows():
        print(f"  {r['month']}  {r['permit_no']}  {r['description'][:70]}")
    print("\nBy category (all months combined):")
    print(joined.groupby("category").agg(permits=("permit_no", "count"),
                                          units=("units", "sum"),
                                          valuation=("valuation_usd", "sum")))
    print("\nADU-suggestive share of all permits, by month:")
    print(joined.assign(is_adu=joined["is_likely_adu"]).groupby("month")["is_adu"].agg(["sum", "count"]))


if __name__ == "__main__":
    main()
