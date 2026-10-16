# Existing Conditions — Kauai and Honolulu Counties, as of 2026-10-15

This is the baseline picture every scenario in this tool is measured against: what each county's housing market actually looks like right now, before any zoning reform. It's not a required Challenge deliverable on its own, but it's the context a reviewer needs before any of the scenario numbers in `docs/POLICY_SCENARIO_COMPARISON.md` or the dashboard mean anything. Kauai and Honolulu are both built out below; Hawai'i County and Maui are not yet (see "What this baseline does and doesn't tell you"). The two counties' figures are **not yet wired into the dashboard as a combined, county-as-a-dimension view** — that chart redesign is still open; this document reports the real numbers behind it so they exist somewhere citable in the meantime. Full source-by-source citations are in `data/DATA_SOURCES.md` and the inventory table in `docs/DATA_PROVENANCE.md`.

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

Kauai's comps are translated into whole-unit for-sale prices via a real OPSD-sourced reference townhome product (see `docs/MODEL_AND_ASSUMPTIONS.md`) — **no equivalent translation exists yet for Honolulu**; no Honolulu-specific pro forma/reference unit has been sourced (a contractor blog was checked 2026-10-15 and rejected for lack of verifiable figures — see `data/reference/`). Honolulu's $648/sqft median is therefore reported as a per-sqft figure only, not yet converted into a feasibility-gate input.

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

This is the single most important fact in both counties' baselines, and it's why the Affordability-Constrained gate exists at all: Kauai's median household (~$85,000–$90,000) is nowhere close to the ~$270,000/year required to afford the median tract's for-sale comps price under standard mortgage underwriting (see `docs/MODEL_AND_ASSUMPTIONS.md`). Honolulu's own required-income figure has **not yet been computed** — that calculation depends on the same reference-unit translation flagged as missing above, so reporting a number here would mean inventing the pro forma input it requires. The gap plainly exists in Honolulu too (a $648/sqft median against a ~$95-100k median household income points the same direction as Kauai), but this project isn't going to state a specific required-income figure until there's a real Honolulu reference unit to compute it from.

## Commute mode and density

ACS B08301 (commute mode) and B01003 (population), joined to tract land area — originally pulled to calibrate the Parking Requirement Reduction scenario's transferability check (Sohoni & Lee 2025's own two axes: non-car commute share and population density).

| | Kauai | Honolulu |
|---|---|---|
| Population-weighted density | ~675 people/sq mi | ~12,633 people/sq mi |
| Commuter-weighted non-car-mode share | ~5.4% | ~15.2% |

This is a real methodological improvement, not just a second data point: Kauai's figures sit well outside the dense-urban range parking-reform research is normally calibrated against (a caveat carried through that scenario's own documentation). Honolulu's density and non-car-commute share are far closer to the urban contexts that research actually studies — meaning a parking-reduction scenario built on Honolulu's real data would rest on a much more defensible transferability argument than Kauai's does. That scenario has not been built for Honolulu yet; this is a note for whoever builds it next, not a claim that it's already done.

## What this baseline does and doesn't tell you

- It is tract-level where the underlying source supports it (ACS, real sales comps) and countywide where it doesn't (ZORI rent for both counties, as pulled so far).
- It mixes vintages on purpose rather than forcing a false reconciliation: 2022 ACS estimates, Aug 2026 Zillow data, and real transactions through 2026 are different snapshots in time, shown as what they are.
- Kauai and Honolulu are built out; **Hawai'i County and Maui are not** (see `docs/DATA_PROVENANCE.md`'s Known Limitations).
- Honolulu's Existing Conditions numbers above ARE rendered in the dashboard (`index.html`), combined with Kauai's as a county dimension, per the earlier decision to combine rather than toggle once a second county had data.
- **Update, 2026-10-15**: all four reform scenarios (ADU, Missing Middle, Parking Reduction, TOD) now also run for Honolulu and are selectable in the Scenario Explorer tab via a county toggle. Honolulu's numbers are **gross zoning capacity only** — not yet run through a feasibility (development pro forma) or affordability-constrained (household income) gate the way Kauai's four scenarios are, since both of those gates are currently hardcoded to Kauai's Lihu'e pro forma/ACS inputs. Building Honolulu-specific equivalents (off the real Iwilei-Kapālama O'ahu pro forma already sourced this session) is still open work. Missing Middle's Honolulu baseline is keyed off the confirmed ROH Table 21-5.1 single-family-district set (Country/R-20/R-10/R-7.5/R-5/R-3.5), not Kauai's density-per-acre rule — see `scripts/build_model.py`'s `run_missing_middle_baseline()`. TOD's Honolulu eligibility is keyed off real HART rail station geometry (21 stations, official "Rail Transit - Station Footprint" ArcGIS Feature Service maintained by HART consultant CH2M, pulled 2026-10-15 — see `data/reference/hart_station_centroids.csv`), not a bus-stop proxy — see `honolulu_tod_eligible_parcels()`.
