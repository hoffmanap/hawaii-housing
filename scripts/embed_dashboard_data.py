"""
Re-inlines data/sample/dashboard.geojson into index.html as
window.DASHBOARD_DATA, so the page works standalone (opened directly
from disk, not just served over http/https) without a fetch() call that
browsers block under the file:// origin. Run this after
build_dashboard_data.py whenever the dashboard data changes.

MULTI-COUNTY, 2026-10-15: renamed from window.KAUAI_DASHBOARD_DATA now
that the file holds both Kauai and Honolulu features (see
build_dashboard_data.py's docstring) -- the old name no longer described
what's embedded. index.html's single read site was updated to match.

Idempotent: replaces a previously-embedded block if one already exists.
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
HTML_PATH = ROOT / "index.html"
DATA_PATH = ROOT / "data" / "sample" / "dashboard.geojson"

START = "<script>\nwindow.DASHBOARD_DATA = "
END = ";\n</script>\n"
PATTERN = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)


OLD_START = "<script>\nwindow.KAUAI_DASHBOARD_DATA = "
OLD_PATTERN = re.compile(re.escape(OLD_START) + r".*?" + re.escape(END), re.DOTALL)


def main() -> None:
    html = HTML_PATH.read_text()
    data = DATA_PATH.read_text()
    block = f"{START}{data}{END}"

    # One-time cleanup: drop any leftover pre-rename KAUAI_DASHBOARD_DATA
    # block so it isn't embedded a second time alongside the new one.
    if OLD_PATTERN.search(html):
        html = OLD_PATTERN.sub("", html, count=1)

    if PATTERN.search(html):
        html = PATTERN.sub(lambda _: block, html, count=1)
    else:
        marker = "<script>\nconst COLORS"
        if marker not in html:
            raise RuntimeError(f"Expected marker {marker!r} not found in {HTML_PATH} -- can't embed data.")
        html = html.replace(marker, block + marker, 1)

    HTML_PATH.write_text(html)
    print(f"Embedded {len(data)} bytes of dashboard data into {HTML_PATH}")


if __name__ == "__main__":
    main()
