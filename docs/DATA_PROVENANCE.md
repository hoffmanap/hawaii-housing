# Data Provenance

Document every dataset your tool uses. The code license does **not** relicense data—each dataset carries its own terms, and you are responsible for them. Do not commit raw or large data to the repository; describe how to obtain it here and keep only small samples in `data/sample/`.

# Dataset Inventory

Complete one row per dataset. This table is kept in sync with `data/DATA_SOURCES.md`, which carries the full narrative citation, decision rationale, and caveats for each row — this table is the compact inventory the Challenge asks for; that file is where the "why" lives.

| Dataset | Source | Version / vintage | Access date | License / terms | Category |
| ------- | ------ | ----------------- | ----------- | --------------- | -------- |
| Statewide TMK parcels (4 counties) | Hawaii Statewide GIS Program, `geodata.hawaii.gov/.../ParcelsZoning/MapServer` layers 5/9/11/25/30 | Live service, fetched 2026-10-14 | 2026-10-14 | Public domain (planning.hawaii.gov/?p=2137 download page); standard no-warranty/no-legal-representation disclaimer | State-provided |
| County zoning (4 counties) | Same service, layers 2/3/29/33 | Live service, fetched 2026-10-14 | 2026-10-14 | Same as above | State-provided |
| State Land Use Districts | Same service, layer 20 | Live service, fetched 2026-10-14 | 2026-10-14 | Same as above | State-provided |
| Building footprints (all islands) | Google/Microsoft Open Buildings, via a public ArcGIS building-footprints service | Snapshot fetched 2026-10-14 (252,050 footprints) | 2026-10-14 | Microsoft: ODbL; Google V3 Open Buildings license not yet fully confirmed for the combined dataset — flagged, not resolved | Third-party |
| ACS 5-year detailed tables: commute mode (B08301), population (B01003), median home value (B25077), median gross rent (B25064) | U.S. Census Bureau, ACS 5-year via Census API | 2022 5-year estimates, fetched 2026-10-14 | 2026-10-14 | Public | Public |
| ACS 5-year detailed table: household income distribution (B19001) | U.S. Census Bureau, ACS 5-year via Census API | 2022 5-year estimates, fetched 2026-10-15 | 2026-10-15 | Public | Public |
| Kauai Bus GTFS feed (NTD ID 90237) | MobilityDatabase | Snapshot `ntd-90237-202610120135`, fetched 2026-10-14 | 2026-10-14 | Public (NTD-sourced transit feed) | Public |
| HDOT Hawaii Performance Monitoring System (HPMS) Roads — federal functional classification | `geodata.hawaii.gov/.../Transportation/MapServer/12` | Live service, fetched 2026-10-14 (88 Kauai arterial segments) | 2026-10-14 | Public (state GIS service) | State-provided |
| Kauai County Real Property Tax monthly sales exports (Jan–Jun 2026) | Kauai County Real Property Tax Office (qpublic.net/hi/kauai) | 6 monthly exports, 362 records, Jan–Jun 2026 | 2026-10-15 (user-supplied, no bulk self-service export exists) | Public record (county real property tax/sales data); not redistributed in this repo (gitignored, `data/raw/sales_exports/`) | Team-created (manually compiled by the user from county records) |
| Zillow Research public data: ZHVI (home values), ZORI (rent index) | Zillow Research, `files.zillowstatic.com/research/public_csvs/` | Monthly series, Kauai County row fetched 2026-10-15 (latest: Aug 2026) | 2026-10-15 | Zillow Research Data — free for non-commercial/research use per Zillow's published terms | Third-party |
| State of Hawaii OPSD, "Financial Analysis of TOD Infrastructure Funding and Financing Options" (HR&A Advisors et al.) | files.hawaii.gov/dbedt/op/lud, user-supplied PDF | Deliverables dated July/Sept. 2023 | 2026-10-14 | Public state planning document | State-provided |
| State of Hawaii OPSD, companion TOD infrastructure-cost deliverable | files.hawaii.gov/dbedt/op/lud, user-supplied PDF | Nov. 2022 | 2026-10-14 | Public state planning document | State-provided |
| Skyline TOD Feasibility Study, Final Deliverable | files.hawaii.gov/dbedt/op/lud/04Reports, user-supplied PDF | July 2026 | 2026-10-15 | Public state planning document | State-provided |
| HB1409 (2025) / Act 159, HRS §206E-246(j) | legiscan.com/HI/text/HB1409, Hawaii State Legislature | 2025 session, enacted | 2026-10-15 | Public legislative/statutory record | State-provided |
| SB1614 (2025, died in committee) | legiscan.com/HI/text/SB1614, Hawaii State Legislature | 2025 session | 2026-10-15 | Public legislative record | State-provided |
| HiCentral (Honolulu Board of REALTORS) Hawaii mortgage rates | hicentral.com/hawaii-mortgage-rates.php | Page dated 2026-10-14 | 2026-10-15 | Public published rate table | Third-party |
| Honolulu DPP Affordable Housing Requirement Incentives handout | honolulu.gov/tod, PDF | Feb. 2025 | 2026-10-15 | Public government document | State-provided |
| Census Building Permits Survey (HI counties) | U.S. Census Bureau | Not yet pulled into this model — listed as a baseline/economic-context source | — | Public | Public |
| FHFA House Price Index (HI) | Federal Housing Finance Agency | Not yet pulled into this model — listed as a baseline/economic-context source | — | Public | Public |

Categories:

- **Public** — nationally available public datasets (e.g., Census/ACS, BLS).
- **State-provided** — datasets supplied by participating states for the challenge, or published by Hawaii state/county government.
- **Third-party** — commercial, nonprofit, academic, or online sources.
- **Team-created** — datasets this project built or materially modified (including a user-compiled extract of otherwise-public county records, where no bulk export existed).

# Cleaning and Preparation

- **TMK parcels / zoning / Land Use Districts, building footprints**: pulled live via `fetch_data.py` into `data/raw/`; no cleaning beyond the service's own geometry. Parcel eligibility for each scenario (R-zone filtering, min-lot-size, TOD-corridor proximity) is computed in `build_model.py`, documented per-scenario in `docs/MODEL_AND_ASSUMPTIONS.md`.
- **ACS tables**: Census's "estimate cannot be computed" sentinel (`-666666666`) is converted to null rather than treated as zero (`fetch_data.py`'s `ACS_NULL_SENTINEL` handling). Population density is computed by joining ACS population to TIGERweb tract land area, not pulled from a single combined source.
- **Kauai Bus GTFS**: a stop is flagged a "major stop" if it's served by 2+ distinct weekday routes (`is_major_stop`) — a data-driven line for a hub-and-spoke, rail-free network, not an arbitrary threshold; see `docs/MODEL_AND_ASSUMPTIONS.md`'s TOD section.
- **HDOT arterial roads**: filtered to the `f_system_t` values "Principal Arterial" and "Minor Arterial" — the service's own renderer/legend categories, not a threshold invented here.
- **Kauai sales exports**: six monthly `.xlsx` files consolidated by `scripts/process_sales_exports.py`, joined to the parcel pipeline via a reverse-engineered TMK key (the export's 12-digit Parcel ID's first 8 digits match `parcel_level.csv`'s 9-digit `tmk_txt` minus its leading zone digit — confirmed exact match on all 362 records). Filtered to residential tax classes and sales with recorded building square footage before computing $/sqft.
- **Zillow ZORI/ZHVI**: checked ZIP-level coverage first; found zero Kauai ZIP codes in ZORI (market too thin), so the county-level row is used instead of a finer granularity that doesn't exist for this market.
- **ACS income distribution (B19001)**: written wide (16 raw bracket counts per tract, not pre-computed percentages), since the required-income threshold it's compared against varies by scenario/typology — the comparison happens downstream in `build_model.tract_affordability_constrained_capacity()`, which interpolates linearly within whichever bracket a threshold falls inside.
- **Development pro forma inputs (OPSD report, HiCentral rates, Honolulu DPP fee handout)**: hand-digitized from the source PDFs/pages into named constants in `build_model.py`, each with an inline citation comment to the specific table/page the figure came from — never silently re-derived or rounded without saying so.

# Known Limitations

- **Two-county build (updated 2026-10-16)**: Kauai and Honolulu are both built out in the model — parcel/zoning, the TOD transit layer (real GTFS-based bus/rail stops), real sales-comps/ACS-scaled feasibility, and ACS income/commute layers all run for both. Hawai'i County and Maui are not yet built out (no parcel/zoning/TOD/ACS pipeline has been run for either). The Honolulu monthly sales exports (20 months, 9,157 raw records) ARE now wired in and usable, via `scripts/process_honolulu_sales_exports.py` — see `docs/EXISTING_CONDITIONS.md`.
- **Feasibility/affordability pro forma is submarket-specific, scaled, not re-derived per tract**: cost inputs (hard cost, land cost, financing) stay fixed at the source table's figures for every tract (Kauai: Lihu'e Town Core; Honolulu: Iwilei-Kapalama) — only the revenue side (sale price/rent) is scaled by each tract's own ACS median home value, median gross rent, or real sales comps. This is a stated, one-sided simplification — see `docs/MODEL_AND_ASSUMPTIONS.md`.
- **No Hawaii-specific property tax/insurance/HOA data**: the affordability gate's required-income calculation prices principal + interest only, not full PITI — it understates the true affordability bar.
- **No Kauai-specific development-fee schedule found**: the fee-reduction slider's $0–$75,000 range is bounded by a Honolulu-specific published fee breakdown, used as the best available statewide bound.
- **Real sales comps are manually sourced, not automated**: no bulk self-service export exists on Kauai County's site; monthly `.xlsx` files were supplied by the user and are gitignored, not committed, so reproducing this model exactly requires the same files.
- **TOD eligibility footprint produces an implausible result at every tested density mechanism** — flagged explicitly, with the finding, in `docs/MODEL_AND_ASSUMPTIONS.md`'s Transit-Oriented Density section, not hidden behind a single chosen number.

# Restricted, Sensitive, or Personal Data

No individual-level, address-level, or otherwise personally identifying data is used. The Kauai sales exports include the names of owners, grantors, and grantees as recorded in public county real-property-transfer records (these are public documents by nature — real estate transfers are a matter of public record everywhere in the U.S., including Hawaii) — the model only uses the aggregate/derived fields (sale price, building square footage, zoning, tract) and never surfaces or republishes the names themselves anywhere in this repository's code, data outputs, or dashboard. All ACS data used is already aggregated to the Census tract level by the Bureau, with small-sample tracts suppressed by the Bureau's own disclosure-avoidance methodology (reflected in this project as the `-666666666` "cannot be computed" sentinel, handled as null rather than overridden). No restricted-use or confidential government data is used.
