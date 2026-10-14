"""
Early smoke tests — exercised now so CI is green from commit one. These
will grow into real coverage of eligibility/yield logic (the "automated
tests covering core analytical logic" gate) as build_model.py is written.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import fetch_data  # noqa: E402


def test_layer_map_covers_all_four_counties():
    county_layers = {
        k: v for k, v in fetch_data.LAYERS.items() if k.startswith("parcels_")
    }
    assert len(county_layers) == 5  # statewide TMK + 4 counties


def test_layer_ids_are_unique():
    ids = list(fetch_data.LAYERS.values())
    assert len(ids) == len(set(ids))
