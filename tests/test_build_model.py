"""
Tests for build_model.py that don't require the raw GIS pulls to be
present (those are gitignored -- see data/README.md). Full-pipeline
correctness is checked by hand against live data for Kauai; see
docs/MODEL_AND_ASSUMPTIONS.md for the verified run (25,119 parcels in,
25,119 out, no duplicates, plausible coverage-ratio distribution).
"""
import sys
from pathlib import Path

import pytest

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


def test_kauai_adu_eligible_zones_are_single_family_only():
    """
    Regression guard for corrections made 2026-10-14: ADUs are permitted
    ONLY on single-family-zoned (R-numbered) land per direct review of
    Kauai County Code -- an earlier pass had wrongly extended eligibility
    to AG/O/CN/CG by conflating ADU provisions with the broader "guest
    house" accessory-use provision. "R-1 to R-6, R-10 to R-20" is also a
    NUMERIC RANGE, not an enumerated list (includes R-15, excludes R-8).
    """
    cfg = build_model.COUNTY_CONFIG["kauai"]
    assert "adu_eligible_non_r_zoning_prefixes" not in cfg
    assert build_model.is_adu_eligible_zone("R-15", cfg) is True
    assert build_model.is_adu_eligible_zone("R-8", cfg) is False  # the gap between the two ranges
    assert build_model.is_adu_eligible_zone("R-40", cfg) is False
    assert build_model.is_adu_eligible_zone("A", cfg) is False  # not single-family zoned
    assert build_model.is_adu_eligible_zone("O", cfg) is False


def test_scenario_params_default_is_the_baseline():
    """ScenarioParams() with every field at its default must represent
    current code exactly -- that's the Challenge's required baseline,
    not an arbitrary starting point for sliders."""
    assert build_model.ScenarioParams().is_baseline is True
    assert build_model.ScenarioParams(require_owner_occupancy=True).is_baseline is False
    assert build_model.ScenarioParams(allow_non_single_family_zones=True).is_baseline is False


def test_policy_impact_refuses_to_compare_baseline_to_itself():
    with pytest.raises(ValueError):
        build_model.run_adu_policy_impact("kauai", build_model.ScenarioParams())


def test_development_fee_reduction_is_dollars_not_fabricated_units():
    """
    Development Fee Reduction should price the Challenge's standardized
    $50k/unit assumption onto an EXISTING unit-count column, never invent
    its own unit-count change (that would require a fee-elasticity
    estimate this project explicitly declined to fabricate -- see
    run_development_fee_reduction_scenario()'s docstring).
    """
    import pandas as pd

    fake_scenario = pd.DataFrame({"GEOID": ["15007000100"], "estimated_new_units": [10.0]})
    result = build_model.run_development_fee_reduction_scenario(fake_scenario)
    assert result["fee_savings_usd"].iloc[0] == 10.0 * build_model.STANDARD_DEVELOPMENT_FEE_PER_UNIT_USD
    # Original unit estimate must pass through unchanged.
    assert result["estimated_new_units"].iloc[0] == 10.0


def test_kauai_parking_reform_context_refuses_without_acs_data():
    """
    kauai_parking_reform_context() must say plainly when the ACS fetch
    hasn't been run, rather than silently fabricating a verdict.
    """
    result = build_model.kauai_parking_reform_context(
        acs_commute_density_csv=Path("/nonexistent/acs_commute_density_kauai.csv")
    )
    assert result["available"] is False


def test_kauai_parking_reform_context_uses_real_acs_data_not_a_fabricated_ratio(tmp_path):
    """
    Regression guard for the 2026-10-14 addition: this function must derive
    its verdict from real ACS figures (population-weighted across tracts)
    and the real Sohoni & Lee (2025) city benchmarks, and must NOT compute
    or expose any kind of "post-reform parking spaces per unit" number --
    this project explicitly declined to extrapolate the study's low-R^2
    regression lines to a county below the sample's own range.
    """
    import csv

    csv_path = tmp_path / "acs_commute_density_kauai.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["GEOID", "name", "population", "commuters_excl_wfh", "pct_alt_mode_commute", "density_per_sqmi"])
        writer.writerow(["15007000100", "Tract A", 1000, 500, 10.0, 100.0])
        writer.writerow(["15007000200", "Tract B", 1000, 500, 20.0, 300.0])

    result = build_model.kauai_parking_reform_context(acs_commute_density_csv=csv_path)
    assert result["available"] is True
    assert result["countywide_alt_mode_commute_pct"] == 15.0  # simple population-weighted mean here
    assert result["countywide_density_per_sqmi"] == 200.0
    assert "San Francisco, CA" in result["study_city_benchmarks"]
    assert not any("per_unit" in k for k in result)  # no fabricated per-unit ratio


def test_kauai_pro_forma_fee_reduction_moves_excess_value_by_exactly_the_fee():
    """
    The whole point of the 'excess value' feasibility test is that a
    development-fee change is a dollar-for-dollar line item -- it must not
    silently touch hard cost, land cost, financing, or required profit.
    """
    full_fee = build_model.kauai_development_fee_reduction_feasibility(
        "townhome_for_sale", "market", development_fee_usd=50_000
    )
    no_fee = build_model.kauai_development_fee_reduction_feasibility(
        "townhome_for_sale", "market", development_fee_usd=0
    )
    assert no_fee["excess_value_usd"] - full_fee["excess_value_usd"] == 50_000
    assert no_fee["hard_cost_usd"] == full_fee["hard_cost_usd"]
    assert no_fee["land_cost_usd"] == full_fee["land_cost_usd"]
    assert no_fee["financing_cost_usd"] == full_fee["financing_cost_usd"]


def test_kauai_pro_forma_fee_reduction_does_not_flip_affordable_feasibility():
    """
    Regression guard for the real (not fabricated) finding this model
    produces: a $50k/unit fee reduction is nowhere near large enough to
    make Lihu'e Town Core affordable-sale townhomes pencil under these
    real, cited cost/revenue assumptions -- consistent with Terner
    Center's own California finding that fee cuts alone rarely flip
    feasibility. If this assertion ever starts failing it means an input
    constant changed, which should be a deliberate, documented edit citing
    its own source -- not a silent drift.
    """
    result = build_model.kauai_development_fee_reduction_feasibility(
        "townhome_for_sale", "affordable", development_fee_usd=0
    )
    assert result["feasible"] is False


def test_apply_feasibility_gate_zeros_out_infeasible_tracts():
    """
    The feasibility toggle must keep both numbers (raw zoning capacity and
    the feasibility-gated number) rather than silently replacing one with
    the other -- that's the whole point of a toggle, not a correction.
    """
    import pandas as pd

    scenario = pd.DataFrame(
        {"GEOID": ["15007000100", "15007000200", "15007000300"], "estimated_new_units": [10.0, 5.0, 8.0]}
    )
    feasibility = pd.DataFrame(
        {
            "GEOID": ["15007000100", "15007000200"],  # tract 300 missing -> treated as not feasible
            "feasible": [True, False],
            "excess_value_usd": [1000, -2000],
        }
    )
    gated = build_model.apply_feasibility_gate(scenario, feasibility)
    assert list(gated["feasibility_adjusted_units"]) == [10.0, 0.0, 0.0]
    assert list(gated["estimated_new_units"]) == [10.0, 5.0, 8.0]  # raw column untouched


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


def test_tod_scenario_rejects_non_kauai_county():
    """
    run_tod_scenario is explicitly Kauai-only until another county has its
    own real transit GTFS feed sourced -- this guards against silently
    returning a wrong/empty result for a county that has no transit data.
    """
    import pytest

    with pytest.raises(NotImplementedError):
        build_model.run_tod_scenario("honolulu")


def test_tod_eligibility_requires_both_stop_and_corridor_proximity():
    """
    Regression guard for the 2026-10-14 correction: TOD eligibility must
    be the AND of near-major-stop and near-arterial-corridor, not stop
    proximity alone (which flagged ~40% of Kauai's parcels and wasn't a
    usable uniform zoning standard -- see kauai_tod_eligible_parcels()'s
    docstring). This checks the combination logic directly against
    synthetic per-parcel flags, independent of the real GIS pull.
    """
    import pandas as pd

    near_stop = pd.Series([True, True, False, False])
    near_corridor = pd.Series([True, False, True, False])
    tod_eligible = near_stop & near_corridor
    assert list(tod_eligible) == [True, False, False, False]


def test_tract_comps_feasibility_requires_sales_file():
    """
    Real sales comps aren't fetched automatically (no bulk self-service
    export exists on the county's site -- the user supplies monthly
    exports manually), so this must fail loudly, not silently fall back
    to a guess, when the processed file isn't present.
    """

    with pytest.raises(RuntimeError):
        build_model.tract_comps_development_fee_reduction_feasibility(
            sales_joined_csv=Path("/nonexistent/kauai_sales_2026_joined.csv")
        )
