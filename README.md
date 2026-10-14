# Hawaii Zoning Reform Impact Model

A state-level simulator that estimates how pro-housing zoning reforms (ADUs, missing middle / gentle density, transit-oriented density, parking reduction, and development fee reduction) would change Hawaii's housing production over a five-year horizon relative to a current-law baseline, aggregated from parcel-level analysis up to the county and state level.

## What this tool does

Models a baseline scenario (current regulatory and economic environment, no policy changes) plus at least three of the five benchmark policy scenarios defined in the Challenge Handbook, for the State of Hawaii. Parcel-level eligibility and yield are computed per-parcel across Hawaii's four counties (Hawaii, Honolulu, Kauai, Maui), then rolled up to county and statewide aggregates — parcel-level output is available only as drill-down detail, never the headline figure, per the Challenge's "what to avoid" guidance on parcel-level precision.

Benchmark scenarios addressed: _[finalize once formulas are validated against Hawaii zoning data — see docs/MODEL_AND_ASSUMPTIONS.md]_

## Command contract

| Step  | Command                     | Notes |
| ----- | --------------------------- | ----- |
| Setup | `pip install -r requirements.txt` | Installs Python dependencies (pandas, geopandas, pytest) |
| Run   | `python scripts/fetch_data.py && python scripts/build_model.py` | Pulls source data from Hawaii's statewide GIS services and runs the scenario model, producing `data/output/*.json` consumed by `index.html` |
| Test  | `pytest tests/` | Runs automated tests covering core eligibility and yield logic |

No API keys or environment variables are required — all data sources are public/public-domain statewide services. `.env.example` is left in place for any addition (e.g., a geocoding key) but is not currently needed.

## Data

All data is fetched at build time from authoritative sources — nothing is committed to this repository. Source: the State of Hawaii's statewide GIS program (`geodata.hawaii.gov` / `geoportal.hawaii.gov`), which serves parcels, county zoning, and the statewide State Land Use Districts layer from a single service covering all four counties. `scripts/fetch_data.py` retrieves this data programmatically on each run. Full provenance, license notes, and cleaning steps are in [docs/DATA_PROVENANCE.md](docs/DATA_PROVENANCE.md).

## Documentation

- [Model & Assumptions Overview](docs/MODEL_AND_ASSUMPTIONS.md) — how the tool works and what it assumes
- [Policy Scenario Comparison](docs/POLICY_SCENARIO_COMPARISON.md) — scenario ranking and drivers
- [Data Provenance](docs/DATA_PROVENANCE.md) — sources, licenses, and cleaning steps
- [Disclosures](docs/DISCLOSURES.md) — AI tools, pretrained models, and external services
- [Presentation Guide](docs/PRESENTATION_GUIDE.md) — slide deck and demo video guidance

## License

This project is licensed under the **Apache License 2.0** (see [LICENSE](LICENSE)), the challenge's recommended default because its express patent grant is the safest choice for tools that may later be adopted or integrated downstream.

The challenge also permits **MIT**, **BSD 3-Clause**, and **BSD 2-Clause**. If you choose one of these instead, replace the LICENSE file with the full text of your chosen license and update this section. Copyleft licenses (GPL, AGPL, LGPL, MPL, EUPL) and public-domain dedications (CC0, the Unlicense) are **not** permitted for submission code. Your dependencies must also be permissively licensed — see docs/DISCLOSURES.md.

## Team

[List team members. All contributors must agree to the license and the challenge's [Official Rules](https://housingchallenge.turnout.rocks/rules.html).]
