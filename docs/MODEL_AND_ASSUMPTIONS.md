# Model & Assumptions Overview

This is a required deliverable. It is the single most important document for the Transparency & Explainability criterion (30% of scoring). Write it so a policymaker with no technical background can understand what your tool does, what it assumes, and what it cannot tell them.

Replace every [bracketed prompt] with your content. Keep it concise; clarity beats length.

# Purpose and Scope

This tool estimates how five-year statewide housing production in Hawaii would change under pro-housing zoning reforms, for policymakers evaluating state-level reform options. Computation happens at the parcel level (using TMK parcels, county zoning, and the statewide State Land Use Districts layer) but every reported figure is aggregated to county and statewide totals — parcel detail is drill-down only, never the headline output.

**In scope:** a baseline (no-reform) scenario plus a working subset of the five benchmark scenarios, mapped onto Hawaii's actual zoning mechanisms (see table below). **Out of scope:** construction financing/cost modeling, displacement/affordability outcomes, and any scenario not grounded in a real Hawaii zoning or land-use category.

## DRAFT — Scenario-to-Hawaii-data mapping

Working sketch to validate before any formula is coded. Each row names the Hawaii-specific mechanism the scenario has to key off, and the open question that still needs an answer from county zoning code or ordinance text (not just the GIS attribute schema).

| Benchmark scenario | Hawaii mechanism it keys off | Data needed | Open question |
| --- | --- | --- | --- |
| **Baseline** | Trend permitting under current State Land Use District + county zoning | Census Building Permits Survey (HI counties), State Land Use District boundaries, county zoning | Which housing outcome to model (permits vs. completions) and over what trailing window to set the trend |
| **ADUs** | County ADU ("ohana dwelling") ordinances — Honolulu, Hawaii County, Kauai, and Maui each regulate these separately at the county level, not via State Land Use Districts | Parcel: existing-unit count = 1, zoning district, lot size | Each county's ADU ordinance has different lot-size/setback minimums — need to pull actual ordinance text per county, not assume a single statewide rule, since this is one of the few scenarios where county-level variation matters even though State Land Use is unified |
| **Missing Middle / Gentle Density** | Parcels in county residential zoning districts currently capped at single-family, inside the **Urban** State Land Use District (Rural/Ag/Conservation districts generally don't permit multifamily regardless of county zoning) | Parcel: county zoning code (e.g., Honolulu R-5, R-7.5 codes), State Land Use District overlay | Need the actual permitted-density table for each county's single-family zoning designations to confirm "currently restricted to single-family" eligibility — the GIS zoning layer gives the district code, not the permitted-use table behind it |
| **Transit-Oriented Density** | Honolulu's rail (Skyline) station areas have their own TOD zoning overlays; other counties have no comparable fixed-guideway transit | Parcel distance to Skyline stations (National Transit Database or City & County of Honolulu TOD overlay data), Honolulu zoning | This scenario may only be meaningfully modelable for Oahu — worth stating explicitly as a geographic limitation rather than forcing a statewide transit-proximity proxy onto islands with no rapid transit |
| **Parking Requirement Reduction** | County-level off-street parking minimums (each county sets its own) | Parcel: zoning district, county parking code minimums by use/district | Need each county's current parking ratio by zoning district — not in the GIS layer, has to come from ordinance text |
| **Development Fee Reduction** | County impact/development fees (differ by county; Honolulu's are documented, outer counties' less so) | County fee schedules | Follow the Rules' standardized $50,000/unit assumption for comparability rather than trying to source four different real fee schedules |

## Existing units and building size — gap found, then closed for Oahu (2026-10-14)

Direct inspection of all four county parcel layers found **no building square footage or existing-unit-count field on any of them** — the exact data `prep_data.py`'s `get_existing_units()` and the underutilization screen depended on in the El Paso build. The state layers alone give:

| County | Land-use/tax classification | Building-presence proxy | Notes |
| --- | --- | --- | --- |
| Hawaii | `pittcode` (integer, meaning not yet decoded) | `bldgvalue`/`bldgexempt` (assessed $, not sqft) | |
| Maui | `pittcode`, same scheme | `bldgvalue`/`bldgexempt` | Identical schema to Hawaii County |
| Kauai | `taxclass` (text label, e.g. "AGRICULTURAL", "OWNER-OCCUPIED MIXED-USE") | `qpub_link` to Schneider Corp qPublic assessor records | Most directly usable of the four as-is |
| Honolulu (Oahu) | **None** | **None** | Cadastral-only layer in the state service |

**For Oahu specifically, this is now closed.** Honolulu's own Real Property Assessment Division publishes a separate `CadastralTables` Feature Service (confirmed live, see data/DATA_SOURCES.md for the real schema and record counts), joined on TMK:
- **Existing units** = count of `LEGDAT` rows sharing a TMK (each legal unit — e.g. each condo apartment — is its own row; a plain single-family parcel has exactly one row). Verified: 4,720 genuinely multi-unit TMKs out of 172,195, including condo towers over 300 units.
- **Improvement/vacancy proxy** = `ASMTPITT.buildingvalue` (assessed dollar value — same role as Hawaii County/Maui's `bldgvalue`, still not a square-footage figure).
- **Land-use class**: `ASMTPITT.ovrclass` exists but its numeric codes are **not yet decoded** — no coded-value domain ships with the field. Working hypothesis (unconfirmed): maps to Hawaii's standard 16-category statewide land-use classification. Needed before land-use-based eligibility (e.g. "currently single-family only") can be read directly off this field for Oahu; until decoded, eligibility there has to lean on zoning-district code instead.
- **"Zoning - Ohana"** (a Honolulu-specific layer, confirmed live) is a narrow regulatory overlay (130 features), **not** a citywide ADU-eligibility layer — don't use it to answer "which parcels can add an ADU," only to flag the specific areas it covers.

**Tax-class decoder — confirmed base scheme, but it does not solve the primary problem (2026-10-14):** sourced a citable base 1–9 tax-class code table from an official Honolulu Real Property Assessment tax-rate ordinance (same scheme, minor label variants, across all four counties — see data/DATA_SOURCES.md for the full table and citation). However, live-data analysis of the full `ASMTPITT` table (327,161 rows) showed:
- `ovrclass` is **blank for 90.0%** of rows (294,322/327,161). It is an *override* field, populated only when a parcel has two or more distinct tax-class sub-areas — it was never going to classify most parcels, override or not.
- The two dominant non-blank values (`'11'`, 30,502 rows; `'14'`, 2,048 rows) fall **outside** the base 1–9 scheme — they're newer categories (Honolulu has since added Residential A, Vacant Agricultural, Bed & Breakfast Home, Transient Vacation) that a current tax-rate document lists by name but without numeric codes. Working guess: `'11'` = Residential A, based on plausible scale — unconfirmed, not load-bearing on anything yet.

Net effect: `ovrclass` does not give usable primary land-use classification for Oahu, and the equivalent fields on the other three counties (`pittcode`, `taxclass`) were never run through this same gap-check. **This is why the project is moving the current-stock and land-use-eligibility problems onto different, more uniform data sources rather than continuing the county-by-county assessor-code chase** — see the next two sections.

## Current housing stock — now via HUD tract-level address data (added 2026-10-14)

Rather than continuing to chase inconsistent, partially-undecoded per-county assessor fields for *existing unit counts*, the model uses **HUD's Aggregated USPS Administrative Data on Address Vacancies** as the statewide current-stock baseline:

- **What it is:** a free (registration required, no fee) HUD/USPS dataset giving quarterly, census-tract-level counts of active, vacant, and no-stat residential and business delivery-point addresses, nationwide — Hawaii included. ([huduser.gov](https://huduser.gov/portal/node/1664), [2018 USPS FAQ](https://www.HUDUser.gov/portal/sites/default/files/pdf/2018-USPS-FAQ.pdf))
- **Why it's a good fit here:** it already reports at census-tract level — the Challenge's target aggregation — so there's no parcel-to-tract rollup to get wrong, and it applies *uniformly* across all four counties, unlike every assessor field we've chased so far.
- **The real caveat:** an "active residential address" is a USPS delivery point, not a certified housing unit count — a duplex with one mailbox undercounts, a commercial address occasionally miscodes as residential. It's a strong proxy for current stock, not a legal record of it.
- **Role in the model:** HUD tract counts become the **current-housing-stock denominator** feeding the Baseline scenario, and the cross-check for Oahu's LEGDAT-derived counts (LEGDAT stays in as a parcel-level validation/drill-down layer for Oahu specifically, where it already exists, but is no longer a dependency for the statewide number).
- **Access note:** the HUD USPS portal requires a login/registration, so this is a manual pull, not scriptable end-to-end like the ArcGIS layers. **In hand as of 2026-10-14** (`usps_vac_062026_tractsum_2kx.zip`, June 2026 vintage, 2020 census tract geography) — filtered to Hawaii (`geoid` starting `15`) and saved to `data/raw/hud_usps_tract_hi.csv`, 432 tracts. Sanity check: statewide active residential addresses (`ams_res`) sum to 631,002, in the right range for Hawaii's housing stock.

## Buildable-area methodology: parcel-level computation, bucketed rollup

Reform-capacity scenarios (Missing Middle, Parking Requirement Reduction, and any density-increase scenario) need *remaining* buildable area per parcel, not just current footprint. The chosen approach computes this at the parcel level and rolls up into **buckets**, not a single averaged ratio, because:

- **An averaged coverage ratio would misrepresent capacity.** Reform capacity is concentrated in a minority of underbuilt/vacant parcels; a tract average blends them with already-built-out parcels and erases exactly the signal a scenario needs to act on.
- **Bucketing matches the data's real precision.** Building footprints are ML-derived (see below) and noisy at parcel edges — a coverage ratio of 23% vs. 31% is noise-sensitive, but which band it falls in generally isn't.

**Pipeline:**
1. **Parcel level (per county):** spatial join of TMK parcels to building footprints; compute `coverage_ratio = Σ footprint_area / parcel_area` and `remaining_area = parcel_area − Σ footprint_area` per TMK.
2. **Bucket each parcel** on lot-size band (e.g. <5k / 5–10k / 10–20k / 20k+ sq ft), coverage band (e.g. 0–10% / 10–25% / 25–50% / 50–75% / 75%+), **and** zoning district + State Land Use District — the latter two aren't optional extra dimensions, they're what eligibility is keyed on anyway, so the bucket table is grouped by them from the start.
3. **Roll up** to `(tract or county) × zoning district × lot-size band × coverage band → parcel_count, Σ remaining_area`. This bucketed table — not raw parcel geometry — is what every scenario formula reads from: auditable, reproducible, and already at the Challenge's required aggregation level.
4. Scenario eligibility logic reads as, e.g., "Urban district, single-family zoning, lot-size ≥10k, coverage <25% → ADU-eligible" against the bucket table.

**Still open:** exact bucket boundaries (lot-size and coverage bands above are placeholders) and whether `remaining_area` needs a per-zoning-district coverage-ratio/setback adjustment rather than the flat `parcel − footprint` figure (same ordinance-text research flagged elsewhere in this document).

## `build_model.py` — implemented and verified for Kauai (2026-10-14)

The pipeline above is now real code (`scripts/build_model.py`), not just a plan. Status: **Kauai only, verified end-to-end against live data** — Honolulu, Hawaii County, and Maui share the same functions but each needs its own `COUNTY_CONFIG` entry (the field names differ per county — see data/DATA_SOURCES.md), and Honolulu specifically needs a LEGDAT-row-count existing-residential flag instead of a taxclass match, which isn't wired in yet.

**What it does:** loads parcels + county zoning + statewide State Land Use Districts + Census 2020 tracts (added today — TIGERweb, Hawaii-filtered, 461 tracts) + building footprints, all reprojected to UTM Zone 4N (EPSG:32604) for consistent area math across the state. Joins each parcel to its zoning district, State Land Use District, and census tract by centroid-in-polygon (the standard approximation at this feature count — not full polygon overlay). Joins building footprints to parcels the same way, sums footprint area per parcel, and computes `coverage_ratio` and `remaining_area_sqft`. Buckets every parcel on lot-size band × coverage band × zoning × State Land Use District, and rolls up to tract level. Runs a first scenario (ADUs) against the bucketed table.

**Verified run, Kauai, 2026-10-14:** 25,119 parcels in, 25,119 out (no join-induced duplicates — fixed a boundary-coincidence double-count in both the zoning/SLUD/tract join and the footprint join, where a centroid landing exactly on a shared edge matched two polygons). Coverage ratio distribution: median 5.5%, IQR 0–27% — consistent with Kauai's largely rural, low-density character, not an artifact. ADU scenario: 10,000–11,000 eligible parcels statewide-for-Kauai, ~837 estimated new units at the placeholder 8% absorption rate — a sane order of magnitude for the county's housing stock, not implausible, but **both the eligibility rule and the absorption rate are placeholders, not sourced conclusions:**
- ADU eligibility used `ludcode == 'U'` (Urban district) + an existing-residential taxclass + lot size ≥ 7,500 sqft. **The 7,500 sqft minimum is an unconfirmed placeholder** — it has not been checked against Kauai's actual ADU ordinance (the same open question already logged above under the ADU scenario mapping).
- The 8% absorption rate is the same unsourced placeholder flagged in the Core Assumptions table below — not yet grounded in Hawaii permit-pace data.

**Known approximation, not yet addressed:** census tract count mismatch — TIGERweb returns 461 Hawaii tracts, HUD's USPS extract covers 432. Likely water-only or population-zero 2020 tracts with no USPS delivery points; needs confirming before the HUD stock numbers and this pipeline's tract output are merged.

Outputs land in `data/processed/` (gitignored, same as `data/raw/` — regenerate with `python scripts/build_model.py --county kauai`): `{county}_parcel_level.csv`, `{county}_bucket_rollup_tract.csv`, `{county}_adu_scenario_tract.csv`.

## Absorption-rate benchmarks, sourced (added 2026-10-14)

Rather than a single statewide default, each county/island gets its own empirical benchmark, computed as average annual building permits (2016–2025) ÷ 2023 housing stock, scaled to a 10-year horizon — the same kind of permit-pace calibration the El Paso build's documentation recommends, applied per island because Hawaii's islands diverge meaningfully in build pace (nearly 2x between Honolulu and Hawaii County):

| Area | 2023 housing units | Avg. annual permits (2016–2025) | Decade-equivalent rate |
| --- | --- | --- | --- |
| Statewide (default) | 572,063 | 3,849 | **6.7%** |
| Honolulu / Oahu | 376,209 | 1,886 | **5.0%** |
| Hawaii County (Big Island) | 91,546 | 1,006 | **11.0%** |
| Maui County | 73,563 | 764 | **10.4%** |
| Kauai County | 30,631 | 193 | **6.3%** |

Source: FRED Building Permits Survey series `BPPRIV015001` (Hawaii County), `BPPRIV015003` (Honolulu), `BPPRIV015007` (Kauai), `BPPRIV015009` (Maui), `HIBPPRIV` (statewide); housing-unit denominators from Hawaii DBEDT's `CO-EST2023-HU-15`. Full table at `data/reference/absorption_rate_benchmarks.csv`.

**What this is and isn't:** this measures total permits against total existing housing stock, not permits against an *eligible-parcel* universe under a specific reform — that historical baseline doesn't exist for Hawaii any more than it existed for El Paso before calibration. Treat these as defensible starting points for the slider, not as validated absorption rates for any one scenario. Honolulu's comparatively low rate (5.0%) likely reflects its much larger, more built-out existing stock rather than a slower construction *pace* in absolute terms (1,886 units/year is still the largest of the four) — a reminder that the ratio is stock-relative, not a measure of activity level on its own.

**Implementation:** `build_model.ScenarioParams(absorption_rate=None)` (the default) pulls the calling county's own benchmark via `absorption_rate_benchmark_for()`; passing an explicit value overrides it. This is the mechanism a future slider UI would call on every drag — see below.

## Scenario parameters as sliders, not fixed rules (added 2026-10-14)

Per your direction: rather than asserting one ADU eligibility rule, the model exposes the rule's components as adjustable parameters (`build_model.ScenarioParams`), so the tool can show how eligible-parcel counts and estimated capacity move as requirements change — which is the actual policy question ("what if the minimum lot size were lower/higher") rather than a single verdict.

Implemented now, Kauai-verified:
- **`min_lot_sqft`** (default 7,500, unconfirmed placeholder) — swept 5,000→15,000 sqft: eligible parcels range from 15,395 down to 3,020, a >5x spread. This is the parameter the real ADU-ordinance research (still an open item) should ultimately replace with Kauai's actual minimum, but the mechanism for varying it is now in place regardless of what that number turns out to be.
- **`require_owner_occupancy`** — Kauai's own tax-class field already distinguishes `OWNER-OCCUPIED` from `NON-OWNER-OCCUPIED RESIDENTIAL`, so this toggle works today with no new data: turning it on roughly halves eligible parcels (11,351 → 5,960 at the default lot size). Only Kauai has this distinction in its taxclass scheme right now (see data/DATA_SOURCES.md) — the other three counties' fields don't support it yet, and the code says so explicitly (`owner_occupancy_warning` in the returned metadata) rather than silently no-op'ing.
- **`absorption_rate`** — defaults to the county's own sourced benchmark above; overridable.
- **`require_parking`** — present as a toggle, but **not functionally implemented**: no county parking-ratio ordinance data has been sourced yet (same open item as the Parking Requirement Reduction scenario mapping above). Turning it on is recorded in the metadata with an explicit warning that it currently changes nothing, rather than pretending to apply an unsourced number.

The expensive spatial-join/bucketing step (`build_county()`) runs once per county; `run_adu_scenario()` re-applies these parameters cheaply against the already-bucketed table — the split a real slider UI would need, so flipping a toggle doesn't mean re-running the GIS joins.

## Kauai ADU ordinance — real numbers found, one correction (2026-10-14)

Pulled Kauai County's actual zoning code (Chapter 8, Article 15 reference via §8-1.4(i)/8-1.5, via eCode360) rather than continuing to guess. Confirmed, citable:

- **Max floor area: 800 sq ft** for a guest house/additional dwelling unit — this is a real, sourced yield assumption (replaces a planning-literature generic figure for this scenario specifically).
- **Parking: 1 off-street space required per unit**, separate from the primary dwelling's own parking. This is also the first real anchor number for the Parking Requirement Reduction scenario (below) — previously only a generic "need ordinance data" placeholder.
- **No owner-occupancy requirement.** The code requires common *ownership* between the ADU and primary dwelling (can't be sold separately, including via condominium property regime) but does not require the owner to live on site. **This corrects what I implied earlier** — I'd offered `require_owner_occupancy` as a slider without checking whether Kauai's real ordinance actually gates on it. It doesn't. The toggle stays in the model (it's a legitimate "what if a future ordinance required this" question, and other jurisdictions do condition ADUs on owner-occupancy), but it should not be presented as reflecting Kauai's current rule.
- **Eligible zones:** R-1 to R-6, R-10 to R-20, plus CN, CG, AG, and O — broader than the Urban-SLUD-only assumption `run_adu_scenario()` currently uses. Worth revisiting: the current `ludcode == 'U'` gate may be too narrow for Kauai specifically, since AG-zoned parcels outside the Urban district are explicitly eligible zones per this code section.
- **Still not found:** the per-district minimum lot area table (what "R-6" vs. "R-10" actually require in sqft) — the ecode360 pages that should hold this didn't render it in the sections fetched. The `min_lot_sqft` slider's 7,500 sqft default remains an unconfirmed placeholder; this is still the single open item before the ADU scenario can claim a sourced eligibility threshold.

**Acted on immediately:** replaced the `ludcode == 'U'` (Urban State Land Use District) eligibility proxy with the confirmed zoning-prefix list above (`COUNTY_CONFIG["kauai"]["adu_eligible_zoning_prefixes"]`) — a direct correctness fix, not a slider. Effect: eligible parcels rose from 11,351 to 13,727 at default settings, because AG-zoned parcels outside the Urban district (which the old proxy excluded) are explicitly listed as eligible in the real ordinance.

## Parking Requirement Reduction — first real implementation, Kauai (2026-10-14)

Built on the confirmed finding above: on Kauai, **parking does not gate ADU eligibility** — 1 off-street space is required either way under current code, so removing that requirement doesn't change *which* parcels qualify, it changes how much of each parcel's remaining area the requirement consumes. `run_parking_reduction_scenario()` reports that land-consumption figure (2,745,400 sqft statewide-for-Kauai at current settings) using a 200 sqft/space planning-literature assumption (not Kauai-sourced — same status as the 850 sqft/unit and 1,000 sqft/job figures the El Paso build flagged as generic benchmarks, not local data).

This is a narrower, more honest scope than the original mapping in the Baseline table implied ("need each county's current parking ratio by zoning district") — for Kauai's ADU case specifically, the ratio turned out to be a simple, confirmed constant (1 space/unit), not a per-district table requiring more ordinance research. Whether the same holds for *primary* dwellings (not just ADUs) under Missing Middle is a separate, still-open question.

## Density rule found — real numbers, two scenarios, one bug caught before it shipped (2026-10-14)

Kauai County Code §8-4.2(b) and §8-4.6 (confirmed via eCode360, pointed to directly): *"the number portion of each Residential Density District establishes the maximum number of dwelling units that may be permitted per acre of land."* I.e. R-6 allows up to 6 units/acre, R-20 up to 20, computed against the parcel's actual area, with §8-4.6(f) rounding a fractional result up at 65%+ of a whole unit. This is a real, generic rule — `max_density_units_per_acre()` parses the district number straight out of the zoning string via regex, so it works for every R-N value without a hardcoded per-district table (confirmed against R-15, which isn't individually named anywhere in the code).

**This replaces the flat 7,500 sqft placeholder for R-numbered districts.** The flat lot-size slider (`min_lot_sqft`) still applies, but now only as the fallback for the non-R zones that are ADU-eligible but have no units/acre figure (AG, O, CN, CG). Effect on the ADU scenario at default settings: eligible parcels dropped from 13,727 to **4,970** (density is a much stricter constraint than a flat 7,500 sqft minimum for low-density R-1/R-2 districts, which need a full acre or more per additional unit).

**Bug caught in the same pass, before it shipped:** the ordinance's "R-1 to R-6, R-10 to R-20" is a *numeric range*, not an enumerated list — it includes R-15 (between 10 and 20) and excludes R-7/R-8/R-9 (the gap between the two ranges). My first implementation matched literal zoning-code strings (`"R-1"`, `"R-2"`, … `"R-10"`, `"R-20"`) and would have silently excluded Kauai's 12 R-15 parcels from ADU eligibility. Caught by testing the eligibility function against R-7/R-8/R-15/R-40 directly rather than only checking that totals looked plausible — fixed to a numeric range check (`is_adu_eligible_zone()`) before this was reported as working.

**Missing Middle / Gentle Density — now implemented**, built directly on this density rule (R-numbered districts only; AG/O/CN/CG have no units/acre figure and are excluded from this scenario, not just unscored). Capacity per parcel = `min(max_units_by_density, 6) − existing_units_estimate`, capped at the Challenge's benchmark definition of 2–6 units regardless of what a district's density math alone would allow on a larger lot. At default settings: 3,160 eligible parcels, ~465 estimated new units (statewide-for-Kauai, at the 6.3% county benchmark absorption rate).

**Known limitation, not yet solved:** `existing_units_estimate` assumes exactly 1 existing unit on every already-residential parcel — there's no direct existing-unit-count field for Kauai (same gap that made Oahu's LEGDAT row-count discovery necessary). This overstates remaining capacity on any Kauai parcel that's already multi-unit. Also not yet reconciled: ADU and Missing Middle both draw on the same underlying district density allowance for a given parcel, so a combined headline number should take the **max** of the two scenarios per parcel, not their sum — consistent with the "never count the same physical capacity twice" rule the El Paso reference project uses. That reconciliation logic isn't written yet.

## Baseline vs. policy intervention — architectural correction (2026-10-14)

The user flagged a real gap in the framing above: **the Challenge requires establishing both a baseline (current law) and the policy interventions (changes beyond current law) — they are not the same number.** Up to this point, `run_adu_scenario()`'s "current-code-derived" eligibility rule (confirmed single-family zoning + the real density math) and its slider parameters were blurred together into one function, with no explicit separation of "what's already legal today" from "what a reform would add." That's a modeling error, not just a documentation gap — it would have reported a reform's *total* capacity as if it were the reform's *impact*, double-counting whatever capacity already existed under current law.

**Fixed via `ScenarioParams.is_baseline`:** `ScenarioParams()` with every field at its default now reproduces current Kauai County Code exactly — single-family-zoned (R-numbered) land only, the real density-per-acre rule, no owner-occupancy requirement, parking required as coded. This *is* the Challenge's required baseline, not an arbitrary starting point for sliders. Any non-default field (`allow_non_single_family_zones=True`, `require_owner_occupancy=True`, etc.) represents an actual policy intervention — a change beyond what current law allows.

**`run_adu_policy_impact()`** is the function that answers the Challenge's actual question: it runs the baseline and a given intervention, and returns their *difference* at tract level (`additional_eligible_parcels`, `additional_estimated_units`) — not either number standing alone. It refuses to run if the "intervention" params are identical to the baseline (nothing to measure the impact of).

Verified for the one intervention implemented so far — extending ADU eligibility to non-single-family zones (`allow_non_single_family_zones=True`, using AG/O/CN/CG with the flat `min_lot_sqft` slider, since those zones have no units/acre figure):

| | Eligible parcels | Estimated units |
| --- | --- | --- |
| **Baseline** (current code, single-family only) | 2,297 | 144.7 |
| **+ Intervention impact** (extend to AG/O/CN/CG) | +2,673 | +168.4 |

**This also corrected a related zoning-eligibility error, caught by the same user review:** an earlier pass had conflated ADU eligibility with the broader "guest house" accessory-use provision (§8-1.4(i)), which extends to AG/O/CN/CG — but ADUs specifically are single-family-zoned-land-only. `is_adu_eligible_zone()` no longer has a non-R fallback for the baseline; AG/O/CN/CG only enter the picture as an explicit, labeled intervention.

**Missing Middle got the same correction, and needed it more.** The scenario as first written used `min(max_units_by_density, 6)` as the *reform* capacity for every R-zoned parcel — but `max_units_by_density` is current law, not a reform. A parcel on a large R-20 lot that already legally allows 8 units today isn't a "Missing Middle" success story; that's baseline zoning doing its job. Split into:
- **`run_missing_middle_baseline()`**: already-legal capacity today (`max_units_by_density − existing_units_estimate`, uncapped at 6) — 3,160 parcels, 30,521 total units of capacity that current code already permits, no reform needed.
- **`run_missing_middle_scenario()`**: the actual reform, scoped to parcels that are *currently* single-family-restricted in practice (`baseline_capacity == 0` — today's density math on that parcel's actual area allows exactly the existing unit and no more). Only those parcels get upzoned to the Challenge's 6-unit benchmark ceiling. Result: 13,206 eligible parcels, +4,163.8 estimated units.

**That last number is large relative to Kauai's total housing stock (~30,631 units, 2023) — worth flagging rather than quietly presenting as settled.** 4,164 estimated units over the model's horizon is roughly 2.3x Kauai's actual decade-average permit pace (182 units/year × 10 ≈ 1,820). This is gross capacity × the county's absorption-rate benchmark, which is exactly the kind of result the absorption rate exists to temper — but the sheer size of the Missing Middle reform's theoretical unlock (every currently-1-unit R-zoned parcel gaining up to 5 more) means even an 6.3% absorption rate on that gross number still produces a figure worth independently sanity-checking in ArcGIS before trusting it, more than the smaller ADU numbers.

## Development Fee Reduction — deliberately NOT a production estimate (2026-10-14)

Unlike ADUs and Missing Middle, Development Fee Reduction doesn't change zoning eligibility — a lower fee doesn't make a parcel legally buildable that wasn't before, it changes project *economics*. Translating "fee reduced by $X" into "Y more units get built" requires a fee-elasticity-of-housing-supply estimate. Real research on this exists (e.g. RAND's 2024 impact-fee study, which found a 25% fee reduction "enables hundreds of new projects in high-cost cities"), but every estimate I found is city-specific and methodology-dependent — applying one to Hawaii would mean importing another market's elasticity and presenting it as if it were Hawaii's own number. I'm not doing that.

**What's implemented instead:** `run_development_fee_reduction_scenario()` takes any other scenario's tract-level output (ADU baseline, Missing Middle policy impact, etc.) and prices the Challenge's own standardized $50,000/unit assumption onto its *existing* unit-count column — a feasibility/dollar-savings metric layered on top of a unit count some other, eligibility-based scenario already produced, not a new production estimate of its own. Verified: $7.24M in fee savings on the ADU baseline's 144.7 estimated units; $208.19M on Missing Middle's 4,163.8 estimated units (same per-unit rate, scaled by each scenario's own unit count — the ratio between the two dollar figures is exactly the ratio between the two unit counts, confirming no hidden multiplier crept in).

**If a future pass wants production impact instead of just dollars:** the right next step is sourcing a Hawaii-specific (or at minimum California/high-cost-coastal-market) fee elasticity from the literature, citing it explicitly, and treating it with the same "this is someone else's number, not ours" caveat the absorption-rate benchmarks already carry — not inventing a plausible-sounding multiplier.

**What HUD data does *not* solve:** it tells us how many units exist, not what's legally buildable. Reform-capacity eligibility (is this tract currently zoned single-family-only, inside the Urban State Land Use District, etc.) still comes from the zoning/land-use spatial layers above — HUD is an additional, parallel input, not a replacement for the zoning overlay.

## Development feasibility (pro forma) research — inconclusive, 2026-10-14

Followed up on the fee-reduction gap above by looking for a real development pro forma to model feasibility, not just dollar savings. Findings:

- **Methodology found:** Terner Center for Housing Innovation (UC Berkeley), *"Making Missing Middle Pencil: The Math Behind Small-Scale Housing Development"* (June 2024). Full IRR-based pro forma: land cost/sqft + hard construction cost/sqft (by product type: duplex, fourplex-rental, fourplex-condo, multiplex) + a $40,000/unit impact-and-connection-fee assumption + financing (60% loan-to-cost, 9.5% construction / 6.5% permanent interest, 1.30 DSCR) vs. revenue (rent or sale price/sqft), tested against a 15% IRR-to-equity feasibility threshold ("pencils" or not). Includes an explicit fee-reduction sensitivity test: cutting fees $40k→$10k/unit raised IRR ~1–2 points — on its own, not enough to clear 15% for most California prototypes tested.
- **Problem: every input is California-specific** (East Bay/South Bay/LA/Sacramento submarkets). Applying Terner's cost and revenue numbers to Hawaii would mean importing another market's pro forma and presenting it as Hawaii's own — the exact thing this project has been declining to do for the fee-elasticity number.
- **Looked for Hawaii-specific substitutes, found them too thin to adapt responsibly:**
  - RLB's *Construction Cost Report – Hawaii Q3 2025* (rlb.com) confirms Hawaii construction costs rose 5.4% y/y (vs. 4.5% national) but the publicly fetchable page has no actual $/sqft figures by product type — those are in RLB's full PDF/NCCI index, not retrieved.
  - Title Guaranty Hawaii's Kauai EOY 2025 residential sales report gives a median Kauai single-family sale price ($1.2M, 423 sales) and vacant-land sale totals by district (Hanalei, Kawaihau, Lihue, Koloa, Waimea), but no land price *per square foot or per acre* — the figures needed to plug into a per-sqft pro forma aren't broken out at this level of the report.
- **Decision: do not build a feasibility/pro forma model on partial, mixed-provenance inputs.** A pro forma with a real California cost structure and a few isolated Hawaii price points (median sale price, a y/y cost-growth rate) would look rigorous while actually being less honest than the current dollar-savings metric, which is explicit about what it does and doesn't claim. `run_development_fee_reduction_scenario()` stays as the dollar-savings-only implementation.
- **What would unblock this:** RLB's full Hawaii Q3 2025 PDF (hard cost/sqft by product type and, ideally, an outer-island/Kauai premium over Oahu) and a Kauai land-value source reporting $/sqft or $/acre for vacant residential land, not just total sale $ by district. Both are flagged as open data gaps rather than filled with substitutes.

## Baseline/intervention principle confirmed as the general rule, not just ADU/Missing Middle (2026-10-14)

Per direct instruction: every scenario must model the effect of a reform *beyond* what current code already allows, never current-code capacity relabeled as reform impact. This is already the architecture behind `ScenarioParams.is_baseline`, `run_adu_policy_impact()`, and the Missing Middle baseline/scenario split above — it is the governing principle for every scenario in this model, including Transit-Oriented Density once Honolulu's county config exists (see below), not a one-off fix specific to ADUs.

**Missing Middle's setback/coverage exemption is the Challenge's own text, not an assumption made here.** The Challenge defines the scenario as: *"Teams may assume that setback and lot coverage requirements do not constrain the development of 2–6 units on a parcel currently restricted to single-family use."* `run_missing_middle_scenario()` already doesn't apply any footprint/coverage constraint to eligible parcels — this was previously an implicit simplification; it is now a cited compliance fact, not a shortcut.

**Transit-Oriented Density — exact Challenge language logged for the eventual Honolulu build:** *"Model the effect of increasing allowable residential density near major transit stops through higher building heights and/or increased floor area ratios. Teams should define major transit stops and justify their transit proximity threshold. Teams may assume that parking requirements are reduced or eliminated."*

**Update, 2026-10-14: checked the State's own TOD program materials** (planning.hawaii.gov/lud/state-tod/ — the Office of Planning and Sustainable Development's State TOD Program hub) rather than assuming the National Transit Database route was the only option. This closes the "justify a proximity threshold" requirement with the state's own standard instead of one this project would otherwise have had to pick and defend itself:
- **Defining TOD** (planning.hawaii.gov/lud/state-tod/defining-tod/) states the state's own working definition and proximity focus: TOD is "an area with a mix of residential, commercial, and institutional uses that is designed to maximize transit options," typically focused "within a half mile of a transit facility (about a ten minute walk)."
- **Station Area and Corridor Maps** (planning.hawaii.gov/lud/state-tod/station-area-and-corridor-maps/) confirms the state draws its own official TOD station-area boundaries at that same **half-mile radius**, around all 21 Honolulu rail (Skyline) stations: East Kapolei, UH West Oahu, Hoopili, West Loch, Waipahu Transit Center, Leeward Community College, Pearl Highlands, Pearlridge Center, Halawa Makai, Pearl Harbor Naval Base, Honolulu International Airport, Lagoon Drive, Middle Street, Kalihi, Kapalama, Iwilei, Chinatown, Downtown, Civic Center, Kakaako, Ala Moana.
- **Decision for the eventual Honolulu build:** use the state's own half-mile TOD station-area definition and this list of 21 stations as "major transit stops," rather than deriving a proximity threshold independently from NTD service-frequency data — it's the state's own stated planning standard for exactly this question, which is a stronger justification than an independently chosen frequency cutoff. The OPSD hub also links several deliverables not yet reviewed in detail (the State TOD Strategic Plan, a 2026 Skyline TOD Feasibility Study, and a 2023 TOD Infrastructure Finance report) that may carry station-specific density/FAR figures worth checking before the Honolulu baseline is built — flagged as the next TOD research step, not yet done.
- TOD still gets the same baseline/intervention split as every other scenario when built: baseline = current density/height/FAR within that half-mile radius, intervention = the increase the reform allows, with parking assumed reduced/eliminated per the Challenge's own text rather than modeled as a constraint.

## Parking Requirement Reduction — what developers actually build post-reform, 2026-10-14

User-supplied source: Sohoni & Lee, *"Parking the Minimum Parking Requirement: Evidence from Nine U.S. Cities,"* *Journal of Planning Education and Research* 46(1), 2025 (DOI [10.1177/0739456X251343047](https://doi.org/10.1177/0739456X251343047)) — a quasi-experimental study of nine U.S. cities that removed minimum parking requirements citywide, in central districts, or along transit corridors.

**What's available vs. paywalled.** The abstract and a secondary citing source (a June 2026 UCLA Center for Parking Policy issue brief) give directional findings but not a clean "developers provide X spaces/unit absent a minimum" ratio — the paper's own results tables (Figure 1: % change in new parking provision relative to the former minimum, by city) are behind SAGE's paywall and weren't reachable here:
- Effects depend heavily on market demand and the existing policy environment — car-oriented, low-density cities saw little to no significant change; cities that removed minimums specifically in low-demand districts saw the largest reductions.
- Across 12 cities/areas examined (the nine-city study plus related cases cited in the same brief), 9 saw an overall decline in new parking supply post-reform and 3 saw an increase.
- In San Francisco and Buffalo (both citywide repeals), reductions in core/downtown areas were 1.5–1.9x larger than in the rest of the city — i.e., removing the minimum doesn't produce zero parking, and the effect is spatially uneven within one city, let alone transferable to Kauai as a single ratio.
- Mixed-use projects provided less parking than purely residential projects in the four CBDs where that breakdown was reported.

**Update, 2026-10-14: got the full PDF, plus a concrete transferability test using real ACS data.** The full text (Sohoni & Lee 2025) gives:
- **Table 3** — exact pre/post parking supply (as % of the former minimum) for all nine case cities. Honolulu is one of them (TOD-district reform: 89%→75% of MPR, treatment; control group rose 78%→106% over the same period) — real, same-state evidence, though it's an Oahu TOD-district result, not directly transferable to Kauai's ADU/parking context.
- **Figure 4** — two regression lines explaining the size of each city's reduction: % non-car commute share in the deregulated zone (R²=0.168) and mean block-group population density there (R²=0.317). Both positive: denser, less car-dependent places cut parking supply more.
- **p.98, in the authors' own words:** "Grand Rapids and Hartford, which experienced minimal policy effects, rank very low in density and very high in car dependency."

This is exactly the transferability test the user proposed: pull Kauai's own ACS commute-mode and density figures and see where it falls relative to the study's cities, rather than importing a number blind. Implemented in `fetch_data.py --layer acs_commute_density --county <county>` (tract-level ACS B08301 commute-mode-to-work + B01003 population, joined to TIGERweb tract land area already on hand; requires a free Census API key in `CENSUS_API_KEY`, not optional the way the other ArcGIS pulls are — the Census API returns an HTML "Missing Key" page for every vintage without one) and `build_model.kauai_parking_reform_context()` in `scripts/build_model.py`.

**Result, verified against live 2022 ACS 5-year data:** Kauai's population-weighted non-car commute share is **~5.4%** and its population-weighted density is **~675 people/sq mi** countywide. Both sit below the study's own "minimal effect" anchor cities (Grand Rapids, Hartford) — and the comparison is already generous to Kauai, since the study's axis is density/commute share *within the deregulated zone itself* (i.e., the built-up area being upzoned), not a countywide average diluted by Kauai's large rural/agricultural tracts.

**Why this is a scale question, not just a geography question (user clarification, 2026-10-14):** the mechanism behind *any* parking-reform effect — in Sohoni & Lee's nine cities and on Kauai alike — is that parking only changes outcomes where it's actually competing with buildable area for land. On a typical single-parcel ADU addition, 1 required space (≈200 sqft, this model's placeholder) is a small fraction of most eligible lots' remaining buildable area; removing that requirement rarely unlocks anything, because parking usually isn't the binding constraint there in the first place. A quick check against live Kauai parcel data confirms this isn't close for most ADU-eligible parcels — the median ADU-eligible parcel has plenty of room regardless of lot-size band — but the *distribution* is skewed by a real subset of small/already-built-out lots where parking is the difference between a unit fitting and not. The pattern that actually matters, and the one worth stating plainly rather than papering over with a single average:
- **Small-scale (ADU on an existing single-family lot):** parking reform is a rounding error for most eligible parcels, with a real but narrow tail of small or already-dense lots where it's the deciding factor.
- **Larger-scale (Missing Middle's 2–6-unit buildings on a parcel being upzoned):** parking's bite scales with unit count — 2–6 required spaces instead of 1 is a much bigger chunk of a parcel's buildable envelope than one ADU space ever is — so this is where removing the requirement is actually expected to matter, consistent with the ACS/study comparison above showing Kauai's *county-wide* car-dependency profile limits how large that effect can be even there.
- **Scope note:** `run_parking_reduction_scenario()` currently only scores the ADU-scale case (it's built on `run_adu_scenario()`'s eligible-parcel set). Extending it to score the Missing Middle-scale case — where, per the logic above, the effect is actually expected to be more than a rounding error — is a documented next step, not yet implemented.

**Decision: still don't manufacture a specific spaces/unit assumption.** Reading a precise reduction ratio off a regression with R²=0.168–0.317, for a geography sitting outside the sample's own range on both axes, would be extrapolation dressed up as a data-driven number — the same discipline this project has applied to the fee-elasticity and pro forma questions above. What the real data *does* support, and is now the documented position: expect little to no reduction in parking actually built on Kauai even if the minimum is removed, consistent with (not contradicted by) `run_parking_reduction_scenario()`'s existing approach of reporting land consumed under the current 1-space/unit requirement rather than assuming a reduced-but-nonzero post-reform figure. `kauai_parking_reform_context()` surfaces this verdict, the real ACS figures, and the study's city benchmarks directly in the scenario's metadata — not a silent assumption.

## Building footprints — buildable-area / reform-capacity modeling, AUTOMATED 2026-10-14

Several scenarios (Missing Middle, Parking Requirement Reduction, and really any density-increase scenario) need to know how much of a parcel is *already* built on, to estimate remaining buildable area rather than assuming the whole lot is available.

**Primary source, now scripted end-to-end:** the combined Google/Microsoft Open Buildings dataset, pulled by `fetch_data.py --layer building_footprints` via a DuckDB spatial/httpfs bounding-box query against the public USA GeoParquet partition (no login, no manual step — unlike HUD above). Verified live: 252,050 footprints returned for Hawaii's statewide bounding box. Statewide and uniform across all four counties — the default for the aggregate model, with the usual ML-footprint caveats (occasional merged/split buildings, `confidence` null for most Microsoft-sourced polygons, no attributes beyond geometry/area/source).

**Supplement/validation candidate:** a confirmed-live Maui County `BuildingFootprints2023_gdb` Feature Service, produced for planning/wildfire-recovery use — worth checking whether Honolulu, Hawaii County, and Kauai publish equivalents before assuming the statewide ML layer alone is sufficient for Oahu's unusually dense condo parcels.

**Open question, not yet resolved:** whether "buildable area" should be computed as `parcel area − footprint area` directly, or needs a setback/coverage-ratio adjustment per zoning district (a lot that's 40% built may still be at its legal buildable maximum under that district's coverage limit) — this needs the same ordinance-text research already flagged above for parking ratios and ADU lot-size minimums, not just the GIS geometry.

**Still open:** decoding the newer (post-base-9) tax-class codes remains low-priority now that primary land-use eligibility is being driven by zoning-district + State Land Use District overlays rather than assessor tax class.

**Why the State Land Use District matters as the statewide anchor:** every scenario except Development Fee Reduction interacts with whether a parcel sits in the Urban district, since Rural/Agricultural/Conservation districts constrain density regardless of county zoning. This is what makes a genuinely *statewide* model defensible here, rather than four independent county models stitched together — Urban-district boundaries are the one zoning fact that's state-level by law, not just by convenience of data source.

# Core Assumptions and Their Rationale

| Assumption | Value / choice | Rationale | Source |
| ---------- | -------------- | --------- | ------ |
| Share of eligible parcels that redevelop (absorption rate) | **Statewide default 6.7%, island-specific benchmarks range 5.0%–11.0%** — see table below. Exposed as a slider in `build_model.py` (`ScenarioParams.absorption_rate`), defaulting per-county to its own benchmark rather than one number everywhere. | Not the same quantity El Paso's 8% approximated (no historical eligible-*parcel* baseline exists for Hawaii either) — this is the best available empirical proxy: each area's average annual permit count as a share of its housing stock, scaled to a 10-year horizon | FRED Building Permits Survey (`BPPRIV015001/3/7/9`, `HIBPPRIV`) × Hawaii DBEDT 2023 housing-unit estimates (`CO-EST2023-HU-15`) — full figures in `data/reference/absorption_rate_benchmarks.csv` |
| Unit size / job size planning benchmarks (sq ft per unit, per job) | TBD | Carried over as a *pattern* from the El Paso build, not as literal values | Planning literature, adjustable in UI |
| Parking land consumption per space | TBD | Needed for Missing Middle / Parking Reduction formulas | Local parking-standard literature |

# Data Sources and Cleaning Steps

[Summarize the datasets you used and how you prepared them. Full provenance and licensing go in DATA_PROVENANCE.md; summarize here and link to it.]

# Methodological Approach

[Explain how the tool works: the equations, logic flows, or rules; how a scenario changes the inputs; and how outputs are produced. Diagrams or pseudocode are welcome. Someone should be able to follow the reasoning from input to output.]

# Limitations and Known Edge Cases

[What does the tool get wrong or handle poorly? Where should users be cautious? Honesty here is rewarded, not penalized.]

# Sensitivity Notes

[Which inputs move the results the most? Show what happens when key assumptions change. Ranges and sensitivity analysis are preferred over single-point claims.]

# Intended Users and Appropriate Use Cases

[Who should use this tool, and for what decisions? Just as important: for what decisions should it NOT be used?]
