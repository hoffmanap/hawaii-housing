"""
Tests for build_model.py that don't require the raw GIS pulls to be
present (those are gitignored -- see data/README.md). Full-pipeline
correctness is checked by hand against live data for Kauai; see
docs/MODEL_AND_ASSUMPTIONS.md for the verified run (25,119 parcels in,
25,119 out, no duplicates, plausible coverage-ratio distribution).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import build_model  # noqa: E402


def test_lot_size_bands_and_labels_match():
    # pd.cut needs exactly len(bins) - 1 labels
    assert len(build_model.LOT_SIZE_LABELS) == len(build_model.LOT_SIZE_BANDS) - 1


def test_coverage_bands_and_labels_match():
    assert len(build_model.COVERAGE_LABELS) == len(build_model.COVERAGE_BANDS) - 1


def test_coverage_bands_span_0_to_100_percent():
    assert build_model.COVERAGE_BANDS[0] == 0
    assert build_model.COVERAGE_BANDS[-1] == float("inf")


def test_county_config_has_required_keys():
    required = {
        "parcels_file", "zoning_file", "parcel_id_field", "zoning_field",
        "taxclass_field", "lot_area_field", "existing_residential_taxclasses",
    }
    for county, cfg in build_model.COUNTY_CONFIG.items():
        missing = required - cfg.keys()
        assert not missing, f"{county} config missing {missing}"
        assert isinstance(cfg["existing_residential_taxclasses"], set)
        assert len(cfg["existing_residential_taxclasses"]) > 0


def test_placeholder_assumptions_are_documented_not_silently_real():
    """
    Both constants are explicitly flagged PLACEHOLDER/unconfirmed in
    build_model.py's comments and in docs/MODEL_AND_ASSUMPTIONS.md's Core
    Assumptions table -- this test just guards against someone quietly
    deleting that flag while changing the value.
    """
    assert 0 < build_model.ABSORPTION_RATE_PLACEHOLDER < 1
    assert build_model.ADU_MIN_LOT_SQFT_PLACEHOLDER > 0
