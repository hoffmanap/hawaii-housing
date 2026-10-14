# Data Provenance

Document every dataset your tool uses. The code license does **not** relicense data—each dataset carries its own terms, and you are responsible for them. Do not commit raw or large data to the repository; describe how to obtain it here and keep only small samples in `data/sample/`.

# Dataset Inventory

Complete one row per dataset.

| Dataset | Source | Version / vintage | Access date | License / terms | Category |
| ------- | ------ | ----------------- | ----------- | --------------- | -------- |
| Statewide TMK parcels (4 counties) | Hawaii Statewide GIS Program, `geodata.hawaii.gov/.../ParcelsZoning/MapServer` layers 5/9/11/25/30 | [confirm layer edit date at fetch time] | [fill at fetch time] | Public domain (per planning.hawaii.gov/?p=2137 download page); standard no-warranty/no-legal-representation disclaimer | State-provided |
| County zoning (4 counties) | Same service, layers 2/3/29/33 | [fill] | [fill] | Same as above | State-provided |
| State Land Use Districts | Same service, layer 20 | [fill] | [fill] | Same as above | State-provided |
| Census Building Permits Survey (HI counties) | U.S. Census Bureau | [fill] | [fill] | Public | Public |
| ACS | U.S. Census Bureau | [fill] | [fill] | Public | Public |
| FHFA HPI (HI) | Federal Housing Finance Agency | [fill] | [fill] | Public | Public |

Categories:

- **Public** — nationally available public datasets (e.g., Census/ACS, BLS).
- **State-provided** — datasets supplied by participating states for the challenge.
- **Third-party** — commercial, nonprofit, academic, or online sources.
- **Team-created** — datasets you built or materially modified.

# Cleaning and Preparation

[For each dataset, describe the cleaning, joining, filtering, or transformation steps you applied, and any assumptions those steps embed.]

# Known Limitations

[Coverage gaps, staleness, geographic mismatches, or quality issues a user should know about.]

# Restricted, Sensitive, or Personal Data

[State whether any dataset involves individuals, households, addresses, facilities, or restricted records. If you used de-identified or aggregated data, describe the method and any re-identification or small-cell risk. Do not use sensitive, confidential, or restricted-use data unless the [Challenge Handbook](https://housingchallenge.turnout.rocks/handbook.html) and the [Official Rules](https://housingchallenge.turnout.rocks/rules.html) expressly permit it and you can document that permission.]
