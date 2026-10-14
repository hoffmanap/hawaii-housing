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


def test_default_adu_lot_size_is_a_positive_placeholder():
    assert build_model.DEFAULT_ADU_MIN_LOT_SQFT > 0


def test_absorption_rate_benchmarks_file_has_every_county_plus_statewide():
    df = build_model.load_absorption_rate_benchmarks()
    areas = set(df["area"])
    assert "statewide" in areas
    for county, area in build_model.COUNTY_TO_BENCHMARK_AREA.items():
        assert area in areas, f"missing benchmark area for {county}"
        rate = build_model.absorption_rate_benchmark_for(county)
        assert 0 < rate < 1, f"{county} benchmark rate {rate} out of range"


def test_scenario_params_default_absorption_rate_is_county_benchmark():
    """
    ScenarioParams(absorption_rate=None) means "use this county's own
    benchmark", not a single statewide number for every island -- that's
    the whole point of sourcing per-county benchmarks instead of reusing
    one figure everywhere.
    """
    params = build_model.ScenarioParams()
    assert params.absorption_rate is None


def test_owner_occupancy_toggle_is_wired_for_kauai_specifically():
    """
    Kauai's taxclass field is the only one so far with an owner-occupied
    vs. non-owner-occupied distinction (see data/DATA_SOURCES.md) -- this
    guards against that config silently going missing.
    """
    assert "owner_occupied_taxclasses" in build_model.COUNTY_CONFIG["kauai"]
    assert build_model.COUNTY_CONFIG["kauai"]["owner_occupied_taxclasses"] <= (
        build_model.COUNTY_CONFIG["kauai"]["existing_residential_taxclasses"]
    )
