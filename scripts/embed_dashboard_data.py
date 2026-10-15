"""
Re-inlines data/sample/kauai_dashboard.geojson into index.html as
window.KAUAI_DASHBOARD_DATA, so the page works standalone (opened directly
from disk, not just served over http/https) without a fetch() call that
browsers block under the file:// origin. Run this after
build_dashboard_data.py whenever the dashboard data changes.

Idempotent: replaces a previously-embedded block if one already exists.
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
HTML_PATH = ROOT / "index.html"
DATA_PATH = ROOT / "data" / "sample" / "kauai_dashboard.geojson"

START = "<script>\nwindow.KAUAI_DASHBOARD_DATA = "
END = ";\n</script>\n"
PATTERN = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)


def main() -> None:
    html = HTML_PATH.read_text()
    data = DATA_PATH.read_text()
    block = f"{START}{data}{END}"

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
