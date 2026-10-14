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

## Current housing stock: HUD USPS address data (added 2026-10-14)

**HUD Aggregated USPS Administrative Data on Address Vacancies** — free (registration required), quarterly, census-tract-level counts of active/vacant/no-stat residential and business delivery-point addresses, nationwide including Hawaii. ([huduser.gov/portal/node/1664](https://huduser.gov/portal/node/1664); FAQ: [2018-USPS-FAQ.pdf](https://www.HUDUser.gov/portal/sites/default/files/pdf/2018-USPS-FAQ.pdf))

- **Role:** replaces the county-assessor chase as the statewide current-stock *denominator*, reported natively at tract level (no rollup needed) and uniform across all four counties — unlike every county tax/assessor field investigated above. Oahu's LEGDAT unit counts remain in the repo as a parcel-level validation layer against this, not as the primary source.
- **Caveat:** an "active residential address" is a USPS delivery point, not a legally certified unit count (undercounts multi-unit buildings sharing one mailbox; occasional residential/commercial miscoding).
- **Access:** requires a registered HUD USER account/login — not pullable by the anonymous ArcGIS-style scripts in `fetch_data.py`. The user will pull this extract manually; once in hand it drops into `data/raw/hud_usps_tract.csv` and `fetch_data.py` gets a corresponding `--layer hud_usps_tract` entry documenting the manual-fetch step (script will validate/normalize, not download).

## Building footprints: buildable-area / reform-capacity (added 2026-10-14)

- **Microsoft Global ML Building Footprints** — nationwide, open (ODbL), ML-derived building outlines, state-level GeoJSON extracts including Hawaii. Statewide and uniform across all four counties; default source for buildable-area calculations.
- **County-published footprints (supplement/validation)** — confirmed live: Maui County `BuildingFootprints2023_gdb` Feature Service (`services3.arcgis.com/fsrDo0QMPlK9CkZD/ArcGIS/rest/services/BuildingFootprints2023_gdb/FeatureServer`), produced for planning/wildfire-recovery use. Still need to check whether Honolulu, Hawaii County, and Kauai publish equivalents, particularly for Oahu's dense condo parcels where ML footprints may merge/split buildings inaccurately.
- **Open modeling question:** whether buildable area = parcel area − footprint area directly, or needs a per-zoning-district coverage-ratio/setback adjustment (see docs/MODEL_AND_ASSUMPTIONS.md).

## Supplementary national datasets (for baseline / economic context)

- U.S. Census Bureau / ACS — demographic and housing stock context
- Census Building Permits Survey — baseline permitting trend (Hawaii county-level series)
- FHFA House Price Index — Hawaii, for baseline economic conditions
- Bureau of Labor Statistics — construction employment/cost context for the baseline scenario
- National Transit Database — candidate source for defining "major transit stops" for the Transit-Oriented Density scenario (Honolulu rail/bus)

## Original Challenge-provided list (all states)

See [`DATA_SOURCES_ALL_STATES.md`](DATA_SOURCES_ALL_STATES.md) for the complete, unmodified table distributed with the starter template, retained for reference.
