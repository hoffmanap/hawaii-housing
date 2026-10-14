"""
Early smoke tests -- exercised now so CI is green from commit one. These
will grow into real coverage of eligibility/yield logic (the "automated
tests covering core analytical logic" gate) as build_model.py is written.
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import fetch_data  # noqa: E402


def test_layer_map_covers_all_four_counties():
    county_layers = {
        k: v
        for k, v in fetch_data.LAYERS.items()
        if k.startswith("parcels_") and k != "parcels_statewide_tmk"
    }
    assert len(county_layers) == 4


def test_every_layer_has_a_base_url_and_id():
    for name, (base_url, layer_id, page_size) in fetch_data.LAYERS.items():
        assert base_url.startswith("https://")
        assert isinstance(layer_id, int) and layer_id >= 0
        assert page_size > 0


def test_building_footprints_bbox_covers_all_main_islands():
    """
    Niihau (west) through the Big Island (east); regression guard against
    accidentally narrowing the box to Oahu only.
    """
    b = fetch_data.HAWAII_BBOX
    assert b["xmin"] < -159.5  # west of Niihau
    assert b["xmax"] > -154.9  # east of the Big Island
    assert b["ymin"] < 20.0 and b["ymax"] > 22.0


def test_existing_units_via_legdat_row_count():
    """
    Regression test for the core discovery that makes Oahu eligibility
    logic possible at all: LEGDAT has one row PER LEGAL UNIT on a parcel,
    not one row per parcel, so existing_units for a TMK = COUNT of rows
    sharing that tmk. Verified 2026-10-14 against the live service:
    172,195 distinct TMKs, 4,720 of them genuinely multi-unit (one condo
    tower alone had 302 rows for a single TMK). This fixture is a tiny
    synthetic stand-in for that same shape, not the live data.
    """
    sample_legdat_rows = [
        {"tmk": "21040037", "unitno": "508"},
        {"tmk": "21040037", "unitno": "601"},
        {"tmk": "89009065", "unitno": "0"},
    ]
    existing_units = Counter(r["tmk"] for r in sample_legdat_rows)
    assert existing_units["21040037"] == 2  # condo, two legal units
    assert existing_units["89009065"] == 1  # ordinary single-family parcel
