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

## Confirmed data gap (2026-10-14): existing units and building size

Direct inspection of all four county parcel layers found **no building square footage or existing-unit-count field on any of them** — the exact data `prep_data.py`'s `get_existing_units()` and the underutilization screen depended on in the El Paso build. What each county's parcel layer *does* carry, confirmed by schema:

| County | Land-use/tax classification | Building-presence proxy | Notes |
| --- | --- | --- | --- |
| Hawaii | `pittcode` (integer, meaning not yet decoded) | `bldgvalue`/`bldgexempt` (assessed $, not sqft) | |
| Maui | `pittcode`, same scheme | `bldgvalue`/`bldgexempt` | Identical schema to Hawaii County |
| Kauai | `taxclass` (text label, e.g. "AGRICULTURAL", "OWNER-OCCUPIED MIXED-USE") | `qpub_link` to Schneider Corp qPublic assessor records | Most directly usable of the four as-is |
| Honolulu (Oahu) | **None** | **None** | Cadastral-only layer — TMK, acreage, geometry. The most-populous county has the least usable parcel data in this service. |

Before any eligibility logic is written, this needs to be closed — candidates identified but **not yet verified**: Honolulu's separate open-data tables `LEGDAT` (unit number per TMK) and `ASMTPITT` (likely the same `pittcode` scheme as Hawaii/Maui, based on the name), both at `opendata.hawaii.gov`. No equivalent source yet identified for Hawaii, Kauai, or Maui counties' unit counts specifically. Until this is resolved, `bldgvalue` (Hawaii/Maui) or `taxclass` (Kauai) are the only honest proxies available — a materially weaker eligibility signal than El Paso's `state_cd` + footprint join, and worth stating as a real limitation in the final write-up if it isn't closed.

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
