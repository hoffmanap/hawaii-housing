# Existing Conditions — Kauai and Honolulu Counties, as of 2026-10-16

This is the baseline picture every scenario in this tool is measured against: what each county's housing market actually looks like right now, before any zoning reform. It's not a required Challenge deliverable on its own, but it's the context a reviewer needs before any of the scenario numbers in `docs/POLICY_SCENARIO_COMPARISON.md` or the dashboard mean anything. Kauai and Honolulu are both built out below; Hawai'i County and Maui are not yet (see "What this baseline does and doesn't tell you"). The two counties' figures ARE wired into the dashboard as a combined, county-as-a-dimension view (`index.html`'s county toggle switches both Existing Conditions and the Scenario Explorer between Kauai and Honolulu). Full source-by-source citations are in `data/DATA_SOURCES.md` and the inventory table in `docs/DATA_PROVENANCE.md`.

## Market rents

| | Kauai | Honolulu |
|---|---|---|
| Zillow ZORI (countywide, latest) | $3,708/mo (Aug 2026) | $3,020/mo (Aug 2026) |
| ACS median gross rent (B25064, tract) | populated for some tracts | tract median of medians: $2,071/mo |

Zillow's Observed Rent Index has **zero ZIP-code-level coverage for Kauai** — the market there is too thin for a reliable sub-county index. Honolulu has full ZIP coverage in principle, but this project has only pulled the **countywide** ZORI series so far (to match the sales/ACS geography used everywhere else here), not the ZIP-level series — a real option for a future pass, not yet done. The ACS and ZORI figures are different vintages and methodologies for both counties and shouldn't be read as cross-validating each other.

## Sales / home values

| | Kauai | Honolulu |
|---|---|---|
| Real transactions window | Feb 2025–Jun 2026 (17 mo) | Feb 2025–Sep 2026 (20 mo, partial) |
| Raw sales records | 1,116 | 9,157 |
| Usable residential sales (valid, with building sqft) | 722 | 7,127 |
| Countywide median $/building-sqft | $768 | $648 |
| Tracts with 5+ usable sales | 17 of 17 | 256 of 285 matched |
| ACS median home value (B25077, 2022 5-yr) | $742,900 | tract median of medians: $809,550 |

Honolulu's sales export uses a different schema than Kauai's (`Parcel  Class` as a direct "RESIDENTIAL" filter rather than an enumerated taxclass set, separate `Bldg. SF`/`Land SF` columns) and a different parcel-ID join key (12-digit Parcel ID, first 8 digits = the TMK used in `parcels_oahu.json` — confirmed exact match on a full test month before trusting it on all 21 files). See `scripts/process_honolulu_sales_exports.py`.

Kauai's comps are translated into whole-unit for-sale prices via a real OPSD-sourced reference townhome product (see `docs/MODEL_AND_ASSUMPTIONS.md`). **Update, 2026-10-15:** Honolulu now has its own equivalent translation — the real Iwilei-Kapalama O'ahu pro forma from the same State of Hawaii OPSD source table, tract-scaled by ACS median home value via `tract_scaled_oahu_development_fee_reduction_feasibility()` — rather than the contractor blog checked and rejected earlier that day for lack of verifiable figures (see `data/reference/`). Honolulu's $648/sqft countywide median sales figure is still reported as-is above for comparability with Kauai's, but the feasibility gate itself runs on the OPSD pro forma, not this per-sqft figure directly.

## Household income distribution

Pulled as the real ACS B19001 bracket distribution (2022 5-year), not a tract median.

| Income bracket | Kauai (17 tracts, 22,978 HH) | Honolulu (321 tracts, 333,700 HH) |
|---|---|---|
| Under $25,000 | 11.7% | 10.6% |
| $25,000–$50,000 | 13.9% | 13.0% |
| $50,000–$75,000 | 16.9% | 13.5% |
| $75,000–$100,000 | 12.5% | 13.0% |
| $100,000–$150,000 | 20.6% | 20.2% |
| $150,000+ | 24.2% | 29.7% |

Interpolating within the bracket containing the midpoint household: Kauai's countywide median is roughly **$85,000–$90,000**; Honolulu's is roughly **$95,000–$100,000** — both order-of-magnitude estimates from the bracket distribution, not a separately-fetched Census median (B19013).

## The affordability gap, stated plainly

This is the single most important fact in both counties' baselines, and it's why the Affordability-Constrained gate exists at all: Kauai's median household (~$85,000–$90,000) is nowhere close to the ~$270,000/year required to afford the median tract's for-sale comps price under standard mortgage underwriting (see `docs/MODEL_AND_ASSUMPTIONS.md`). **Update, 2026-10-15:** Honolulu's own required-income figure is now computed, using the Iwilei-Kapalama reference-unit translation noted above — real Honolulu tracts require household incomes well above the ~$95,000–$100,000 countywide median to clear the for-sale gate, the same direction as Kauai's gap, though the specific figure varies by tract since Honolulu's sale price is ACS-scaled rather than fixed. See `docs/MODEL_AND_ASSUMPTIONS.md` for the per-reform figures, now reported for both the for-sale and rental tenure (added 2026-10-15, next day's work).

## Commute mode and density

ACS B08301 (commute mode) and B01003 (population), joined to tract land area — originally pulled to calibrate the Parking Requirement Reduction scenario's transferability check (Sohoni & Lee 2025's own two axes: non-car commute share and population density).

| | Kauai | Honolulu |
|---|---|---|
| Population-weighted density | ~675 people/sq mi | ~12,633 people/sq mi |
| Commuter-weighted non-car-mode share | ~5.4% | ~15.2% |

This is a real methodological improvement, not just a second data point: Kauai's figures sit well outside the dense-urban range parking-reform research is normally calibrated against (a caveat carried through that scenario's own documentation). Honolulu's density and non-car-commute share are far closer to the urban contexts that research actually studies — meaning Honolulu's parking-reduction scenario (now built, `run_parking_reduction_scenario("honolulu")`) rests on a much more defensible transferability argument than Kauai's does. As with Kauai, Honolulu's version still reports land freed (sqft), not a unit count, for the same reason: parking does not gate ADU/Missing Middle eligibility in either county's current code.

## What this baseline does and doesn't tell you

- It is tract-level where the underlying source supports it (ACS, real sales comps) and countywide where it doesn't (ZORI rent for both counties, as pulled so far).
- It mixes vintages on purpose rather than forcing a false reconciliation: 2022 ACS estimates, Aug 2026 Zillow data, and real transactions through 2026 are different snapshots in time, shown as what they are.
- Kauai and Honolulu are built out; **Hawai'i County and Maui are not** (see `docs/DATA_PROVENANCE.md`'s Known Limitations).
- Honolulu's Existing Conditions numbers above ARE rendered in the dashboard (`index.html`), combined with Kauai's as a county dimension, per the earlier decision to combine rather than toggle once a second county had data.
- **Update, 2026-10-15**: all four reform scenarios (ADU, Missing Middle, Parking Reduction, TOD) now also run for Honolulu and are selectable in the Scenario Explorer tab via a county toggle. Missing Middle's Honolulu baseline is keyed off the confirmed ROH Table 21-5.1 single-family-district set (Country/R-20/R-10/R-7.5/R-5/R-3.5), not Kauai's density-per-acre rule — see `scripts/build_model.py`'s `run_missing_middle_baseline()`.

- **Update, 2026-10-15 (same day, later pass):** Honolulu's ADU/Missing Middle/TOD figures are no longer gross-capacity-only. `tract_scaled_oahu_development_fee_reduction_feasibility()` and `honolulu_tract_affordability_constrained_capacity()` wire the real Iwilei-Kapalama O'ahu pro forma (already sourced this session, previously unused) into the same feasibility/affordability pipeline Kauai runs through, pinned to the same $50,000 Challenge-standard development fee as Kauai's gate so the dashboard's fee slider stays consistent across counties. Honest finding: all 34 of Honolulu's TOD rail-tier-eligible tracts fail the feasibility test under this gate (0 feasibility-adjusted units), even though the same pro forma clears 44 of Honolulu's 288 tracts overall — flagged as a real, undiagnosed result, not smoothed over. See `docs/MODEL_AND_ASSUMPTIONS.md`'s matching entry for full figures by scenario.

- **Update, 2026-10-15 (same day, later pass):** TOD was rebuilt on the actual statutory eligibility test (HRS §206E-246(j)/226-63(d)), replacing two different ad hoc per-county mechanisms with ONE mode-selectable mechanism: `build_model.tod_eligible_parcels(county, mode)` computes each parcel's real distance to the nearest GTFS-derived transit stop of the selected mode ("bus" or "rail") and assigns the statute's own FAR tier from that distance — no more buffer-boolean test, no more user-set FAR slider. "Bus" (TheBus/Kauai Bus) is capped at the statute's FAR 4.0 base tier in both counties, since a regular bus stop is a real transit station but not an adopted "locally preferred alternative" (LPA) project; "rail" (HART Skyline, Honolulu only) reaches FAR 6.0/7.0 near a station, since Skyline is the one LPA project in Hawaii today. The Scenario Explorer's TOD mode toggle lets you switch between them for Honolulu; Kauai only has bus data. See `data/DATA_SOURCES.md`'s TOD rebuild entry for the full statutory-text sourcing.

- **Update, 2026-10-15 (same day, final pass):** the unit-size assumption TOD and Missing Middle both use to convert floor area into a unit count was replaced — the prior figure (840 sqft) was silently inherited from Kauai's for-sale townhome pro forma, not a real apartment/rental figure. Now `MULTIFAMILY_RENTAL_UNIT_SIZE_GSF = 815`, a real RentCafe/Yardi Matrix average for Oahu apartment buildings with 50+ units, applied to both counties (no comparable Kauai-specific survey exists). Missing Middle also now has a real buildable-area check it previously lacked entirely — reform capacity capped at `floor(parcel_area_sqft / 815)` as well as the flat 6-unit legal ceiling — so a small lot is no longer credited with 6 units regardless of whether that much floor area fits. See `docs/MODEL_AND_ASSUMPTIONS.md`'s matching entry and `data/DATA_SOURCES.md` for full sourcing.

- **Update, 2026-10-15 (next day's work, same thread):** the "all 34 of Honolulu's TOD rail tracts fail feasibility" finding two entries above is now resolved — it was the right diagnosis (TOD is a rental apartment product being tested against a for-sale pro forma that was calibrated to Honolulu's priciest for-sale neighborhoods), not a bug. TOD in both counties now runs through a dedicated rental feasibility test, `build_model.tract_scaled_rental_feasibility()`, built from the Skyline TOD Feasibility Study's own real Market Rate Rentals inputs ($3,200/mo rent, 4.5% cap rate, $574,080/unit stabilized value) scaled by each tract's real ACS median gross rent, with each county's already-sourced hard/land costs reused on the cost side. Under the new gate: 9 of 31 rail-eligible Honolulu tracts with rental data now clear feasibility (Honolulu TOD rail: raw 72,240.1 → feasibility-adjusted 20,759.2 → affordability-constrained 5,687.9), versus 0 of 34 before. A real, stated limitation carries forward: no rental developer-profit margin is sourced anywhere in the Skyline study, so none is subtracted — a more permissive bar than the for-sale gates'. Rental affordability (both counties, all rental scenarios) was also corrected to test against the real ACS B25118 renter-occupied-household income distribution instead of the blended all-household one, fixing a bug (`_pct_households_above_income()` silently returning 0% for every tract when passed renter-specific brackets) caught because the result was implausible. See `docs/MODEL_AND_ASSUMPTIONS.md`'s matching entry for full sourcing, the cap-rate cross-check, and the ACS rent-paid transparency check.

- **Update, 2026-10-15 (same day, final pass):** every reform with a feasibility gate (ADU, Missing Middle, TOD — both modes, both counties) now runs BOTH the for-sale and rental pro formas per tract and takes whichever pencils better (`build_model.combine_dual_tenure_feasibility()`), rather than the prior round's hardcoding of ADU/Missing Middle to for-sale only and TOD to rental only. Affordability is then routed to the matching income distribution per tract (`apply_dual_tenure_affordability_gate()`), reported as a combined figure plus `_for_sale`/`_rental` breakouts. Real findings: Kauai's affordable outcome is overwhelmingly rental regardless of which tenure wins feasibility more often (for-sale affordability is 0% in every Kauai tract, a finding already established); Honolulu shows a genuine mix — e.g. ADU: 157.0 units afford­able to owner households, 860.4 to renter households (of 3,414.4 feasibility-adjusted), Missing Middle: 796.7 for-sale / 4,118.8 rental, TOD-bus: 13,518.7 for-sale / 81,421.2 rental. TOD-rail remains 0 for-sale / 5,687.9 rental, confirming rather than contradicting the prior entry's diagnosis. See `docs/MODEL_AND_ASSUMPTIONS.md`'s matching entry for the full before/after table and stated limitations (winner-take-all per tract, no local rental-vs-for-sale permit-mix source).

- **Update, 2026-10-16:** the baseline scenario — what today's zoning already allows, with no reform at all — is now directly reported, not just computed internally and subtracted off. Missing Middle's baseline (`run_missing_middle_baseline_tract()`): Kauai's current zoning already permits an estimated **1,922.8 units** beyond what's built (30,521 gross units of legal headroom across 3,160 parcels); Honolulu's is **0 everywhere**, a stated data gap (no sourced density-per-acre rule for Honolulu's R-districts), not a finding of zero real headroom. TOD's own baseline was found to be understated, not just unreported: it previously netted the reform's capacity against only what's physically built, when a parcel's own unused CURRENT zoning headroom should also count as baseline, not reform impact — fixed to net against whichever is larger (built units or current zoned capacity). Kauai TOD (bus) corrects from 137,280.1 to 136,578.6 raw units; Honolulu is unaffected, for the same reason its Missing Middle baseline is 0. ADU's reported number already IS the baseline scenario by construction (current law's 2-ADU-per-lot mandate) — no separate computation needed there. See `docs/MODEL_AND_ASSUMPTIONS.md`'s matching entry for the full reasoning.
