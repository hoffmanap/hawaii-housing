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


def test_density_parsed_directly_from_rdistrict_number():
    """
    Kauai County Code §8-4.2(b): "the number portion of each Residential
    Density District establishes the maximum number of dwelling units
    ... per acre." This should work for ANY R-N value via regex, not a
    hardcoded per-district lookup table -- confirms R-15 (not individually
    enumerated anywhere) still parses to 15.0 units/acre.
    """
    assert build_model.max_density_units_per_acre("R-6") == 6.0
    assert build_model.max_density_units_per_acre("R-15") == 15.0
    assert build_model.max_density_units_per_acre("R-4/ST-P") == 4.0  # combo/overlay code
    assert build_model.max_density_units_per_acre("AG") is None
    assert build_model.max_density_units_per_acre(None) is None


def test_max_units_rounds_up_at_65_percent_per_code():
    """Kauai County Code §8-4.6(f): round up when the fractional result
    is 65% or more of a unit."""
    # Literal sqft chosen so parcel_area_sqft / 43,560 is exact in binary
    # floating point (avoids an unrelated float-precision false failure
    # from e.g. 0.6 * 43560 / 43560 != 0.6 exactly).
    # 161,172 sqft / 43,560 x 1.0 units/acre = 3.7 -> rounds up to 4
    assert build_model.max_units_for_parcel(161_172, 1.0) == 4
    # 130,680 sqft / 43,560 x 1.0 units/acre = 3.0 exactly -> stays 3
    assert build_model.max_units_for_parcel(130_680, 1.0) == 3
    # 143,748 sqft / 43,560 x 1.0 units/acre = 3.3 -> below 65%, stays 3
    assert build_model.max_units_for_parcel(143_748, 1.0) == 3


def test_missing_middle_caps_at_challenge_benchmark_of_6_units():
    assert build_model.MISSING_MIDDLE_MAX_UNITS == 6


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


def test_kauai_adu_eligible_zones_use_numeric_ranges_not_string_prefixes():
    """
    Regression guard for two corrections made 2026-10-14: ADU eligibility
    should come from Kauai's confirmed ordinance zoning rule (County Code
    Ch. 8, Art. 15), not the earlier State-Land-Use-District ("Urban")
    proxy it replaced -- and "R-1 to R-6, R-10 to R-20" is a NUMERIC
    RANGE (an earlier version matched literal strings and wrongly
    excluded R-15, which falls inside the 10-20 range).
    """
    cfg = build_model.COUNTY_CONFIG["kauai"]
    assert "A" in cfg["adu_eligible_non_r_zoning_prefixes"]  # Agricultural -- the old proxy excluded it
    assert build_model.is_adu_eligible_zone("R-15", cfg) is True
    assert build_model.is_adu_eligible_zone("R-8", cfg) is False  # the gap between the two ranges
    assert build_model.is_adu_eligible_zone("R-40", cfg) is False


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
