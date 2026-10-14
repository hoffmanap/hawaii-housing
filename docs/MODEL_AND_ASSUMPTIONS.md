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

**What HUD data does *not* solve:** it tells us how many units exist, not what's legally buildable. Reform-capacity eligibility (is this tract currently zoned single-family-only, inside the Urban State Land Use District, etc.) still comes from the zoning/land-use spatial layers above — HUD is an additional, parallel input, not a replacement for the zoning overlay.

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
| Share of eligible parcels that redevelop (absorption rate) | TBD — `housingstrategy`'s El Paso build grounded this in local permit-pace data (FRED BPPRIV series); Hawaii's county-level equivalent needs to be pulled before defaulting a number | Should not reuse El Paso's 8% as-is; different market | Census Building Permits Survey, by HI county |
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
