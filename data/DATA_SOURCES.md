# Data Sources — Hawaii

Primary source: the State of Hawaii's statewide GIS program.

| Layer | Service | Coverage | Notes |
| ----- | ------- | -------- | ----- |
| Parcels (TMK) | `geodata.hawaii.gov/arcgis/rest/services/ParcelsZoning/MapServer` — layers 25–28 (Statewide TMKs, Zones, Sections, Plats), plus per-county layers 5 (Hawaii), 11 (Oahu), 9 (Kauai), 30 (Maui) | All 4 counties | Common statewide Tax Map Key (TMK) scheme across counties. **Field richness is uneven by county — confirmed by direct schema inspection, 2026-10-14:** Hawaii County (5) and Maui (30) carry `pittcode` (assessment/tax classification) and `bldgvalue`/`bldgexempt` (assessed building dollar value — usable as an improvement-presence proxy, not a square-footage figure); Kauai (9) carries `taxclass` as a direct text land-use label (e.g. "AGRICULTURAL", "OWNER-OCCUPIED MIXED-USE") plus a `qpub_link` to Schneider Corp's qPublic assessor lookup; **Oahu (11) is cadastral-only** — TMK, acreage, geometry, no classification or value fields at all. None of the four carry building square footage or an existing-unit count directly. See "Known gap" below. |
| County zoning | Same service — layers 2 (Hawaii), 3 (Honolulu), 29 (Kauai), 33 (Maui) | All 4 counties | One layer per county, same endpoint |
| State Land Use Districts | Same service — layer 20 | Statewide | Hawaii's Land Use Law (1961) — Urban / Rural / Agricultural / Conservation, set by the state Land Use Commission. This is the closest thing to genuine *state-level* zoning authority among the states on the Challenge's list, and the natural anchor for a statewide reform model |
| Conservation District Subzones | Same service — layer 1 | Statewide | |
| County land-use / general plans | Same service — layers 22 (Hawaii LUPAG), 7 (Kauai General Plan), 10 / 14 (Maui Community Plan / Island Plan) | Per county | Honolulu has no equivalent plan layer in this service |
| Important Agricultural Lands | Same service — layer 4 | Statewide | |

Human-browsable front door: [geoportal.hawaii.gov](https://geoportal.hawaii.gov/) (ArcGIS Hub) and the download index at [planning.hawaii.gov/?p=2137](https://planning.hawaii.gov/?p=2137). Both point at the same underlying `geodata.hawaii.gov` REST services used programmatically by `scripts/fetch_data.py`.

**Oahu building-characteristics gap — CLOSED, 2026-10-14.** The state ParcelsZoning service's Oahu layer is cadastral-only, but Honolulu's Real Property Assessment Division (BFS) publishes a separate `CadastralTables` Feature Service (`services.arcgis.com/tNJpAOha4mODLkXz/.../CadastralTables/FeatureServer`), updated weekly, joined to parcels on `tmk`/`parid`:

| Table | Layer | What it actually gives us | Confirmed against live data |
| --- | --- | --- | --- |
| `LEGDAT` | 11 | **One row per legal unit on a parcel** — `unitno` is a condo unit label ("508"), `'0'` for non-condo. **`existing_units` for a TMK = count of LEGDAT rows sharing that TMK** (not a field to read). `totsqft` is lot/land area, not building floor area. | 326,944 rows / 172,195 distinct TMKs; 4,720 genuinely multi-unit parcels, one condo tower at 302 units on a single TMK |
| `ASMTPITT` | 10 | `buildingvalue` — assessed-dollar improvement proxy (same role as Hawaii County/Maui's `bldgvalue`). `ovrclass` — numeric tax-class code, **not yet decoded** (no coded-value domain on the field; hypothesis: maps to Hawaii's standard 16-category statewide land-use classification, unconfirmed) | 327,161 rows |
| `Zoning - Ohana` (Honolulu's own `Zoning_Reg` service) | 3 | A specific ohana/ADU regulatory **overlay** — only 130 features. **Not** a citywide "ADUs allowed here" layer; `ohana_type` codes also undecoded | 130 rows |

Statewide (non-Oahu) equivalent for Hawaii, Kauai, Maui counties' unit counts specifically: still not identified. Those counties' own `pittcode`/`taxclass` fields (above) remain the closest proxy, same decode gap as `ovrclass`.

**Tax-class decoder — base scheme confirmed, but not a primary-classification fix (2026-10-14).** Base 1–9 codes, from an official Honolulu Real Property Assessment tax-rate ordinance (`realproperty.honolulu.gov/media/x0ph24q2/03_txrate.pdf`); the same scheme applies with minor label differences across all four counties:

| Code | Honolulu | Maui | Hawaii County | Kauai |
| --- | --- | --- | --- | --- |
| 1 | Improved Residential | Improved Residential | Improved Residential | Single Family Residential |
| 2 | Apartment | Apartment | Apartment | Apartment |
| 3 | Commercial | Commercial | Commercial | Commercial |
| 4 | Industrial | Industrial | Industrial | Industrial |
| 5 | Agricultural | Agricultural | Agricultural/Native Forests | Agricultural |
| 6 | Preservation | Conservation | Conservation | Conservation |
| 7 | Hotel and Resort | Hotel and Resort | Hotel and Resort | Hotel and Resort |
| 8 | Unimproved Residential | Unimproved Residential | Unimproved Residential | Homestead |
| 9 | Public Service | Homeowner | Homeowner | — |

Live-data check against the full `ASMTPITT` table (327,161 rows) found this table doesn't actually solve classification for Oahu: `ovrclass` is blank for 90.0% of rows (it's an override field for multi-class-split parcels only), and the two dominant non-blank values (`'11'`: 30,502 rows; `'14'`: 2,048 rows) are newer categories added after this base table (Honolulu's current FY27 tax-rate schedule — `realproperty.honolulu.gov/media/b3pcwo2o/state-report-fy27-final_tax-rates.pdf` — lists "Residential A," "Vacant Agricultural," "Bed and Breakfast Home," "Transient Vacation" by name but without numeric codes). Working guess, unconfirmed: `'11'` = Residential A. Given this, primary land-use eligibility logic is keyed off zoning-district + State Land Use District overlays instead of tax class; decoding the post-9 codes is now a low-priority nice-to-have.

**License:** stated as public domain by the Hawaii Office of Planning and Sustainable Development download page, no fee, standard no-warranty/no-legal-representation disclaimer. No redistribution restriction stated. See docs/DATA_PROVENANCE.md for the full citation and access-date record required by the Challenge.

## Census 2020 tract boundaries (added 2026-10-14)

`fetch_data.py --layer census_tracts`: TIGERweb's `Tracts_Blocks` MapServer, layer 4 ("Census Tracts"), filtered to Hawaii (`STATE='15'`). No pagination needed (461 features, under the layer's 100,000 max record count). This is the tract geometry build_model.py joins parcels to for tract-level rollup. **Note:** 461 tracts returned here vs. 432 in the HUD USPS extract below — likely water-only/zero-population 2020 tracts with no delivery points, not yet confirmed.

## Current housing stock: HUD USPS address data (added 2026-10-14)

**HUD Aggregated USPS Administrative Data on Address Vacancies** — free (registration required), quarterly, census-tract-level counts of active/vacant/no-stat residential and business delivery-point addresses, nationwide including Hawaii. ([huduser.gov/portal/node/1664](https://huduser.gov/portal/node/1664); FAQ: [2018-USPS-FAQ.pdf](https://www.HUDUser.gov/portal/sites/default/files/pdf/2018-USPS-FAQ.pdf))

- **Role:** replaces the county-assessor chase as the statewide current-stock *denominator*, reported natively at tract level (no rollup needed) and uniform across all four counties — unlike every county tax/assessor field investigated above. Oahu's LEGDAT unit counts remain in the repo as a parcel-level validation layer against this, not as the primary source.
- **Caveat:** an "active residential address" is a USPS delivery point, not a legally certified unit count (undercounts multi-unit buildings sharing one mailbox; occasional residential/commercial miscoding).
- **Access:** requires a registered HUD USER account/login — not pullable by the anonymous ArcGIS-style scripts in `fetch_data.py`. The user will pull this extract manually; once in hand it drops into `data/raw/hud_usps_tract.csv` and `fetch_data.py` gets a corresponding `--layer hud_usps_tract` entry documenting the manual-fetch step (script will validate/normalize, not download).

## Building footprints: buildable-area / reform-capacity — AUTOMATED, 2026-10-14

**Primary source, now scripted:** the combined Google/Microsoft Open Buildings dataset (via VIDA / Source Cooperative), public GeoParquet, no login. There's no Hawaii-specific or state-level file — it's partitioned by country, so `fetch_data.py`'s `fetch_building_footprints()` queries the USA partition with a statewide bounding-box filter via DuckDB's spatial + httpfs extensions, which prunes GeoParquet row groups by their bbox statistics instead of downloading the whole country file. Run with `python scripts/fetch_data.py --layer building_footprints`.

- **Verified live, 2026-10-14:** 252,050 building footprints returned for the Hawaii bounding box (`xmin -160.6, ymin 18.7, xmax -154.6, ymax 22.5` — Niihau through the Big Island), written to `data/raw/building_footprints.json` as a GeoJSON FeatureCollection. Each feature carries `bf_source` (google/microsoft), `confidence`, `area_in_meters`, plus `s2_id`/`geohash` for spatial joins.
- **Caveats:** ML-derived outlines — occasional merged/split buildings, especially plausible on Oahu's dense condo parcels; `confidence` is frequently null for Microsoft-sourced footprints (only Google-sourced ones reliably carry a score); no attribute data beyond geometry, area, and source.
- **Supplement/validation candidate:** a Maui County-specific `BuildingFootprints2023_gdb` Feature Service is live (`services3.arcgis.com/fsrDo0QMPlK9CkZD/ArcGIS/rest/services/BuildingFootprints2023_gdb/FeatureServer`), produced for planning/wildfire-recovery use. Not yet checked whether Honolulu, Hawaii County, or Kauai publish equivalents.
- **Open modeling question:** whether buildable area = parcel area − footprint area directly, or needs a per-zoning-district coverage-ratio/setback adjustment (see docs/MODEL_AND_ASSUMPTIONS.md).
- **License:** Microsoft's footprints are ODbL; confirm Google's V3 Open Buildings license terms (and the combined dataset's effective license) before this goes in DATA_PROVENANCE.md's formal citation table.

## ACS commute mode + density: Parking Requirement Reduction transferability check (added 2026-10-14)

**Census Bureau ACS 5-year detailed tables**, tract-level, via the Census API: `B08301` (Means of Transportation to Work — used for non-car commute share) and `B01003` (total population, combined with TIGERweb tract land area for density). `fetch_data.py --layer acs_commute_density --county <county>` writes `data/raw/acs_commute_density_{county}.csv`.

- **Role:** lets the model place a Hawaii county on the same two axes (non-car commute share, population density) that Sohoni & Lee (2025) — see `docs/MODEL_AND_ASSUMPTIONS.md`'s Parking Requirement Reduction section — use to explain how much parking supply cities actually keep after removing minimum parking requirements, instead of applying a borrowed elasticity number blind.
- **Access:** requires a **free Census API key** (https://api.census.gov/data/key_signup.html) in the `CENSUS_API_KEY` environment variable (or `.env`, gitignored). Confirmed 2026-10-14: every ACS vintage returns an HTML "Missing Key" page (HTTP 200, not JSON) without one — this is not optional the way the other ArcGIS pulls in this repo are.
- **Verified live, Kauai:** 17 tracts, population-weighted non-car commute share ~5.4%, population-weighted density ~675 people/sq mi.

## Development pro forma inputs: Kauai-specific (added 2026-10-14)

**State of Hawaii OPSD, *Financial Analysis of TOD Infrastructure Funding and Financing Options*** (HR&A Advisors, Group Pacific, PBR Hawai'i, CBRE, CoStar/Zillow — user-supplied PDF, deliverables dated July/Sept. 2023; https://files.hawaii.gov/dbedt/op/lud/02PROJ/OPSDTODInfra_2022/3.Deliverable3_20230908_HawaiiTODInfraFin-FullFinal.pdf). Appendix "Real Estate Assumptions" table gives, per Hawaii submarket including Lihu'e Town Core (Kaua'i): construction hard cost per GSF by product type, land price per SF/acre, financing terms (loan-to-cost, interest rates, fees), developer profit margins, and market/affordable sale prices and rents — used in `build_model.kauai_development_fee_reduction_feasibility()`. Also gives a real Hawaii "excess value" feasibility test (market value − total development cost − required developer return) used for the same function's methodology. See `docs/MODEL_AND_ASSUMPTIONS.md`'s Development Fee Reduction section for the full citation and result.

A companion OPSD deliverable (https://files.hawaii.gov/dbedt/op/lud/02PROJ/OPSDTODInfra_2022/1.Deliverable1_20221102_HawaiiTODInfraFin.pdf, Nov. 2022) covers TOD infrastructure project costs (electrical, water/wastewater) and financing-capacity context for four pilot areas (Oahu, Maui, Kauai, Hawaii County) — reviewed, no station-specific density/FAR or parking figures found in the visible text; flagged as background context, not a direct model input.

## Transit-Oriented Density: Kauai Bus GTFS + HDOT road classification (added 2026-10-14)

**Kauai Bus GTFS feed** (MobilityDatabase, NTD ID 90237; `https://files.mobilitydatabase.org/ntd-90237/ntd-90237-202610120135/ntd-90237-202610120135.zip`; daily-refreshed, zero validation errors as of fetch). `fetch_data.py --layer kauai_bus_major_stops` downloads it, computes weekday trip counts and distinct-route counts per stop, and writes `data/raw/kauai_bus_major_stops.csv` with an `is_major_stop` flag (2+ distinct weekday routes — 58 of 126 weekday-served stops qualify).

- **Role:** per the user's 2026-10-14 correction — the TOD scenario should not be scoped to Honolulu's rail stations ("there's nothing that says it has to be built around transit stations... base it around bus lines") — this is the real, bus-based transit data the scenario is built on instead, for the one county (Kauai) already fully built out in this model.
- **Why "2+ routes":** Kauai Bus has no rail or BRT; its network is a hub-and-spoke of single-route shuttle loops feeding a handful of mainline/transfer stops. Route count is a clean, data-driven line between those two kinds of stop.

**HDOT Hawaii Performance Monitoring System (HPMS) Roads** (`geodata.hawaii.gov/arcgis/rest/services/Transportation/MapServer/12`) — FHWA federal functional classification (`f_system_t` field: Interstate, Freeway/Expressway, Principal Arterial, Minor Arterial, Major/Minor Collector, Local). `fetch_data.py --layer kauai_arterial_roads` pulls Kauai's Principal- and Minor-Arterial segments (88 segments: Kuhio Hwy, Kaumualii Hwy, and their approaches) to `data/raw/kauai_arterial_roads.json`.

- **Role:** added after a first pass using major-stop proximity alone flagged ~40% of Kauai's parcels — every town with any bus service — which isn't a usable *uniform zoning standard* (per the user's follow-up: "This needs to be a zoning standard applied uniformly... transit stops plus some type of street designation, like high frequency transit along major corridors"). TOD eligibility (`build_model.kauai_tod_eligible_parcels()`) now requires a parcel to be within a half-mile of BOTH a major stop AND an arterial corridor — a real, statewide-applied federal classification, not a threshold invented for this project.
- **Known limitation, not yet resolved:** because Kauai's towns sit directly along its two trunk highways, the arterial-corridor requirement only modestly narrowed eligibility (10,097 → 9,428 parcels at the half-mile threshold used for both factors; even an eighth-mile corridor buffer alone still covers ~5,600 parcels). The geography isn't the main driver of this scenario's still-implausible total — see the Transit-Oriented Density section of `docs/MODEL_AND_ASSUMPTIONS.md` for the real finding and the open question it leaves (the ~50 units/acre density target, borrowed from an Oahu rail-pilot Master Plan, is almost certainly the wrong scale to apply here).

## Real 2026 sales comps: Kauai County Real Property Tax Office (added 2026-10-15)

**Kauai County Real Property Tax sales exports** (qpublic.net/hi/kauai -- a Schneider Corp/qPublic CAMA system; confirmed 2026-10-15 that it has no self-service bulk export, so the user pulled six monthly exports manually: Jan-Jun 2026, 362 records total). Each record: Parcel ID (TMK, 12-digit, Section+Plat+Parcel+CPR -- no zone digit), address, owner/grantor/grantee, sale date, sale price, acres, tax class, year built, building square feet, neighborhood code, zoning. `scripts/process_sales_exports.py` consolidates the monthly files, joins to `kauai_parcel_level.csv` on a reverse-engineered TMK key (parcel_level's `tmk_txt[1:]` == this export's `Parcel ID[:8]`, confirmed exact match on all 362 records), and computes real $/building-sqft by zoning district and by tract.

- **Role:** replaces the ACS-median-home-value *proxy* ratio in the development-fee-reduction feasibility pro forma with REAL observed 2026 transaction prices, where sample size allows (`tract_comps_development_fee_reduction_feasibility()`, `min_sales_per_tract=5`). 16 of Kauai's 17 tracts clear that bar in just six months -- better coverage than expected.
- **Cross-validation:** countywide median $779/building-sqft across 225 usable residential sales is within 1% of the Lihu'e reference townhome's own implied $786/sqft ($1,179,403 / 1,500 sqft) -- real transactions corroborating the OPSD-sourced pro forma input, not a coincidence engineered by this project.
- **Divergence from the ACS proxy, worth flagging:** the real-comps feasibility gate flags MORE tracts infeasible than the ACS proxy did (9/16 feasible vs. 13/17) -- several tracts flip (15007040402, 040500, 040701, 040800). The real data is the one to trust going forward; the ACS version is kept as a fallback for counties/tracts without enough sales comps yet.
- **Honolulu equivalent available too:** a one-month (Jan 2026) Honolulu County export was also supplied (418 records) -- not usable yet since Honolulu isn't built in this model, saved for when it is. If monthly exports continue to arrive for Kauai and extend to the other counties, this becomes the strongest revenue-side input in the whole model, worth requesting on an ongoing basis.
- **Not committed:** `data/raw/sales_exports/*.xlsx` (gitignored, like all raw data) and the processed join (`data/processed/kauai_sales_2026_joined.csv`) both live only in the local environment -- this is manually-sourced data without automated fetch, so anyone reproducing this model needs the same monthly exports.

## Zillow Observed Rent Index (ZORI), Kauai County (added 2026-10-15)

**Zillow Research public data** (files.zillowstatic.com/research/public_csvs/, no key or login). Checked ZIP-level ZORI coverage first: zero Kauai zip codes appear -- the market is too thin for Zillow to publish a reliable rent index below the county level. County-level ZORI does have a "Kauai County" row: $3,708/month, Aug 2026 (latest available). ZHVI (home values), by contrast, has 13 Kauai zip codes -- used nowhere directly yet, kept as a candidate cross-check against the real sales comps above.

- **Decision:** use this single county-wide ZORI figure (`build_model.KAUAI_COUNTYWIDE_ZORI_USD_PER_MONTH`) rather than a paid provider (Rentcast, etc.) for finer granularity -- a different vendor re-slicing the same small set of actual Kauai rental listings doesn't manufacture real tract-level signal in a market this thin.
- **Not yet wired into a scenario.** This is the market-rent input a real multifamily *rental* pro forma would need to properly feasibility-test the TOD scenario's higher-density parcels, which the current feasibility gate still can't do (it prices a townhome-resale product). Building that rental pro forma is the next real step.

## Supplementary national datasets (for baseline / economic context)

- U.S. Census Bureau / ACS — demographic and housing stock context
- Census Building Permits Survey — baseline permitting trend (Hawaii county-level series)
- FHFA House Price Index — Hawaii, for baseline economic conditions
- Bureau of Labor Statistics — construction employment/cost context for the baseline scenario
- National Transit Database — candidate source for defining "major transit stops" for the Transit-Oriented Density scenario (Honolulu rail/bus)

## Original Challenge-provided list (all states)

See [`DATA_SOURCES_ALL_STATES.md`](DATA_SOURCES_ALL_STATES.md) for the complete, unmodified table distributed with the starter template, retained for reference.
