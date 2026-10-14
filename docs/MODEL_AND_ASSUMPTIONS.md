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
- **Access note:** the HUD USPS portal requires a login/registration that the person obtained access to be able to pull manually (requires requesting access distinct from the public ArcGIS pulls we've automated elsewhere) — `fetch_data.py` will add an `--layer hud_usps_tract` target once the raw extract is in hand; this one isn't scriptable end-to-end the way the ArcGIS layers are.

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
