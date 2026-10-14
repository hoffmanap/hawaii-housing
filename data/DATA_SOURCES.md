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

**Known gap — building characteristics (confirmed missing, 2026-10-14):** none of the four county parcel layers carry building square footage or an existing-unit count, which our eligibility logic (underutilization screen, existing-units-by-parcel) depends on. `bldgvalue` (Hawaii County, Maui) is an assessed-dollar proxy for whether a parcel is improved at all, not a size or unit count. Candidates not yet verified: Honolulu's open-data `LEGDAT` table (unit number per TMK) and `ASMTPITT` table (tax-class codes — likely the same `pittcode` scheme seen on Hawaii County/Maui, given the name match) at `opendata.hawaii.gov`; no equivalent source identified yet for Hawaii, Kauai, or Maui counties specifically for unit counts. This must be resolved before `build_model.py`'s eligibility logic is written — see `scripts/fetch_data.py` module docstring and docs/MODEL_AND_ASSUMPTIONS.md.

**License:** stated as public domain by the Hawaii Office of Planning and Sustainable Development download page, no fee, standard no-warranty/no-legal-representation disclaimer. No redistribution restriction stated. See docs/DATA_PROVENANCE.md for the full citation and access-date record required by the Challenge.

## Supplementary national datasets (for baseline / economic context)

- U.S. Census Bureau / ACS — demographic and housing stock context
- Census Building Permits Survey — baseline permitting trend (Hawaii county-level series)
- FHFA House Price Index — Hawaii, for baseline economic conditions
- Bureau of Labor Statistics — construction employment/cost context for the baseline scenario
- National Transit Database — candidate source for defining "major transit stops" for the Transit-Oriented Density scenario (Honolulu rail/bus)

## Original Challenge-provided list (all states)

See [`DATA_SOURCES_ALL_STATES.md`](DATA_SOURCES_ALL_STATES.md) for the complete, unmodified table distributed with the starter template, retained for reference.
