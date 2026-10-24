# Disclosures

AI-assisted development is welcome. This document exists so reviewers can
confirm that your tool's code, models, services, and dependencies are compatible
with the challenge's open-source and downstream-use requirements. Disclose
material use; incidental use of a general coding assistant is lower risk but
should still be noted if its terms affect your submission.

# AI-Assisted Code Generation

**This project was built materially with Claude (Anthropic), via Claude Code, across the full development history** — the data pipeline (`scripts/fetch_data.py`, `scripts/build_model.py`, `scripts/build_dashboard_data.py`), the dashboard (`index.html`), the test suite (`tests/`), and this documentation set were all written with Claude as an active collaborator, not an incidental autocomplete tool. Every commit in this repository's history carries a `Co-Authored-By: Claude ... <noreply@anthropic.com>` trailer and a `Claude-Session:` link to the session that produced it, so the exact scope of AI involvement in any given change is traceable in `git log`, not just asserted here.

Anthropic's consumer and commercial terms permit the person directing Claude to own and freely license the resulting output; nothing in Anthropic's terms restricts submitting Claude-assisted code to a competition or open-licensing it. **[Team: confirm which Anthropic product/plan tier you used (e.g., Claude Code under a Pro/Team/Enterprise plan, or API access) and attach or reference the specific terms-of-service excerpt you're relying on, since this is specific to your account and not something I can certify on your behalf.]** The team remains responsible for understanding, testing, and securing all submitted code regardless of how it was produced — this repo's test suite (`tests/test_build_model.py`, 34 tests) and CI (`.github/workflows/ci.yml`, `codeql.yml`) are the concrete evidence of that review, not a substitute for disclosing AI involvement.

# Pretrained Models, Weights, and Embeddings

No pretrained model, model weights, or embeddings are incorporated into or required by this tool's code — no ML inference happens at runtime. The one adjacent item worth flagging is a dataset (not a model this tool runs) that was itself produced by someone else's ML pipeline:

| Model / asset | Source | License / terms | Restrictions |
| ------------- | ------ | --------------- | ------------ |
| Google/Microsoft Open Buildings (building footprints, ML-derived from satellite imagery) | `data.source.coop/vida/google-microsoft-open-buildings` (VIDA/Source Cooperative) | Microsoft's portion: ODbL. Google's V3 Open Buildings license for the combined/merged dataset is **not yet fully confirmed** — already flagged as unresolved in `docs/DATA_PROVENANCE.md` | Potential attribution/share-alike obligations under ODbL if Google's terms don't supersede it — **unresolved, needs legal review before any redistribution of the raw footprint data itself** (the derived `coverage_ratio`/`remaining_area_sqft` fields this tool actually publishes are computed outputs, not a republication of the footprints) |

# External APIs and Hosted Services

Every API/service this tool depends on is public and free, with no team-specific credential except one (flagged below). Full citations and access dates are in `docs/DATA_PROVENANCE.md`'s Dataset Inventory; this is the subset that's a live dependency (vs. a one-time hand-digitized source):

| Service | Free tier? | Access could lapse? | Commercial/downstream restriction? |
| ------- | ---------- | -------------------- | ----------------------------------- |
| Hawaii Statewide GIS Program (`geodata.hawaii.gov`) — parcels, zoning, State Land Use Districts | Yes, fully public, no key | Low — a standing state GIS service | None found; public-domain data per the state's own download page |
| Honolulu DPP/BFS hosted ArcGIS services (`services.arcgis.com/tNJpAOha4mODLkXz/...`) — CadastralTables, TOD rezoning layer | Yes, fully public, no key | Low | None found |
| U.S. Census Bureau API (ACS 5-year estimates) | Yes, **requires a free API key** (`CENSUS_API_KEY`) | Low — standard federal API | None; public data |
| data.honolulu.gov (Socrata) — building permits | Yes, fully public, no key (rate-limited without a Socrata app token) | Low | None found; standard Socrata public-data terms |
| FRED (Federal Reserve Economic Data) — Building Permits Survey series | Yes, fully public | Low | None; public data |
| MobilityDatabase — GTFS feeds (Kauai Bus, TheBus, Skyline) | Yes, fully public | Low | None found; standard GTFS/NTD-sourced public transit data |
| Zillow Research public CSVs | Yes, no key | Low | **Free for non-commercial/research use per Zillow's published terms** — flag for review if this tool is ever deployed for a commercial purpose, though a Challenge submission is squarely research use |
| `data.source.coop` (Google/Microsoft Open Buildings, via DuckDB httpfs) | Yes, no key | Low | See Pretrained Models section above |

**The one credential this project uses, `CENSUS_API_KEY`, is a personal, free, self-service Census Bureau key — never committed to git (gitignored `.env` only) and never stored in any persistent memory system.** A reviewer running this pipeline fresh needs their own key (instructions in `data/README.md`), not this team's.

# Dependency Licenses

Python runtime dependencies (`requirements.txt`) are all permissively licensed and compatible with closed-source/open-source downstream use — no copyleft, no noncommercial, no unknown-license packages:

| Dependency | License | Runtime or dev-only | Notes |
| ---------- | ------- | -------------------- | ----- |
| pandas | BSD-3-Clause | Runtime | ok |
| geopandas | BSD-3-Clause | Runtime | ok |
| shapely | BSD-3-Clause | Runtime | ok |
| pyproj | MIT | Runtime | ok |
| requests | Apache-2.0 | Runtime | ok |
| duckdb | MIT | Runtime | ok (used for the building-footprints GeoParquet query) |
| dbfread | MIT | Runtime | ok |
| pytest | MIT | Dev-only | ok |

The dashboard (`index.html`) loads two client-side libraries from CDN, both permissive:

| Dependency | License | Runtime or dev-only | Notes |
| ---------- | ------- | -------------------- | ----- |
| Chart.js 4.4.4 (`cdn.jsdelivr.net`) | MIT | Runtime (browser) | ok |
| Leaflet 1.9.4 (`unpkg.com`) | BSD-2-Clause | Runtime (browser) | ok |

**[Team: this list reflects the two manifests actually in the repo (`requirements.txt` and `index.html`'s CDN tags) as of this writing. Export the GitHub dependency-graph SBOM for the authoritative, complete picture — including any transitive dependencies of pandas/geopandas — and reconcile it against this table before submission; the above is a direct read of declared dependencies, not a full transitive-dependency audit.]**

# Generated or Copied Code

No code in this repository was copied or adapted from an external source under a license requiring attribution. All code (pipeline scripts, the dashboard, tests) was written for this project, with Claude as an active collaborator per the AI-Assisted Code Generation section above — which is itself the relevant disclosure for this section, not a separate "copied code" concern. The one near-miss worth naming explicitly: the Chart.js and Leaflet libraries above are *loaded*, not *copied* — they're referenced by CDN URL at runtime, not vendored into this repository's own source, so no NOTICE-file attribution obligation applies to them beyond their own standard MIT/BSD license headers (which ship with the libraries themselves, not this repo).
