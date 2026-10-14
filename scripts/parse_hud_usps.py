"""
Parse HUD's Aggregated USPS Administrative Data on Address Vacancies into
the Hawaii-only CSV used by build_model.py.

This is a manual-input step, not a network pull like fetch_data.py: the
source .zip requires a HUD USER login to download (see
data/DATA_SOURCES.md), so it can't be fetched automatically. Once you have
it, this script does the reproducible part -- unzip, parse the .dbf,
filter to Hawaii, write a plain CSV -- so the transformation itself is
still scripted and auditable, even though the download isn't.

Usage:
    python scripts/parse_hud_usps.py path/to/usps_vac_YYYYMM_tractsum_2kx.zip

Hawaii tracts are identified by a 2020-census GEOID starting with the
state FIPS code "15". Output: data/raw/hud_usps_tract_hi.csv (gitignored,
same as every other raw pull).
"""
import argparse
import csv
import sys
import zipfile
from pathlib import Path

from dbfread import DBF

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
HI_STATE_FIPS_PREFIX = "15"


def parse(zip_path: Path, out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        dbf_names = [n for n in zf.namelist() if n.lower().endswith(".dbf")]
        if not dbf_names:
            raise RuntimeError(f"No .dbf file found inside {zip_path}")
        zf.extractall(out_path.parent, members=dbf_names)
        dbf_path = out_path.parent / dbf_names[0]

    table = DBF(str(dbf_path))
    hi_rows = [r for r in table if str(r["geoid"]).startswith(HI_STATE_FIPS_PREFIX)]
    if not hi_rows:
        raise RuntimeError(
            f"No rows with geoid starting '{HI_STATE_FIPS_PREFIX}' found -- "
            "check this is a tract-summary file with a 'geoid' field."
        )

    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(hi_rows[0].keys()))
        writer.writeheader()
        writer.writerows(hi_rows)

    dbf_path.unlink()  # don't leave the full-nation .dbf sitting in data/raw/
    return len(hi_rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_path", type=Path, help="Path to the HUD USPS tract-summary .zip")
    args = parser.parse_args()

    out_path = RAW_DIR / "hud_usps_tract_hi.csv"
    count = parse(args.zip_path, out_path)
    print(f"{count} Hawaii tracts -> {out_path}")

    # Quick sanity check printed on every run, not just in tests: a
    # statewide total active-residential-address count wildly outside
    # Hawaii's known housing stock (~550k-650k units) means either the
    # wrong file was passed, or a future HUD schema change broke the
    # 'ams_res' field name.
    import csv as _csv

    with out_path.open() as f:
        total_res = sum(int(row["ams_res"]) for row in _csv.DictReader(f))
    print(f"  sanity check: statewide active residential addresses = {total_res:,}")
    if not (400_000 <= total_res <= 800_000):
        print(
            "  WARNING: this is outside the expected ~400k-800k range for "
            "Hawaii's housing stock -- double-check the input file.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
