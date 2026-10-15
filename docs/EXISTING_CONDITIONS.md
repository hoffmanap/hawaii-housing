# Existing Conditions — Kauai County, as of 2026-10-15

This is the baseline picture every scenario in this tool is measured against: what Kauai's housing market actually looks like right now, before any zoning reform. It's not a required Challenge deliverable on its own, but it's the context a reviewer needs before any of the scenario numbers in `docs/POLICY_SCENARIO_COMPARISON.md` or the dashboard mean anything. Every figure here is also rendered live in `index.html`'s "Existing Conditions" tab, generated from the same pipeline (`scripts/build_dashboard_data.py`), not hand-copied — the two should never drift apart. Full source-by-source citations are in `data/DATA_SOURCES.md` and the inventory table in `docs/DATA_PROVENANCE.md`.

## Market rents

Zillow's Observed Rent Index (ZORI) has **zero ZIP-code-level coverage for Kauai** — the rental market is too thin for Zillow to publish a reliable index below the county level. The only figure available is **countywide: $3,708/month (Aug 2026, latest published)**. ACS also publishes a tract-level "median gross rent" (B25064, 2022 5-year) that is populated for some but not all tracts, shown in the dashboard table alongside the single countywide ZORI figure — the two are not the same vintage or methodology and shouldn't be read as cross-validating each other, just as the two best available rent signals.

## Sales / home values

Two sources, deliberately kept separate rather than blended into one number:

- **Real 2026 transactions**: Kauai County Real Property Tax Office monthly sales exports, Jan–Jun 2026, 362 raw records, 225 usable residential sales after filtering to tax classes with recorded building square footage. **Countywide median: $779/building-sqft.** 16 of 17 Kauai tracts have 5+ usable sales in just six months — better coverage than expected for a market this size.
- **ACS median home value** (B25077, 2022 5-year): **countywide $742,900.** This is a stock measure (what owners report their home is worth) on a different vintage than the 2026 transactions, not a reconciled or averaged figure with the sales data above.

Translated into whole-unit prices via this model's Lihu'e Town Core reference townhome product (1,500 GSF, a real OPSD-sourced pro forma input — see `docs/MODEL_AND_ASSUMPTIONS.md`), the real 2026 comps imply a **median tract-level for-sale price of roughly $1,274,000**, ranging from about $784,000 in the least expensive tract to about $1,871,000 in the most expensive (North Shore) tract.

## Household income distribution

Pulled as the real ACS B19001 bracket distribution (2022 5-year), not a tract median — 17 Kauai tracts, 22,978 total households. Countywide shape:

| Income bracket | Share of households |
|---|---|
| Under $25,000 | 11.7% |
| $25,000–$50,000 | 13.9% |
| $50,000–$75,000 | 16.9% |
| $75,000–$100,000 | 12.5% |
| $100,000–$150,000 | 20.6% |
| $150,000+ | 24.2% |

Interpolating within the bracket that contains the midpoint household puts **countywide median household income at roughly $85,000–$90,000** — this is an estimate from the bracket distribution above, not a separately-fetched Census median (B19013), and is given as an order-of-magnitude figure, not a precise one.

## The affordability gap, stated plainly

This is the single most important fact in this baseline, and it's why the Affordability-Constrained gate exists at all: a household earning the countywide median (~$85,000–$90,000) is nowhere close to the ~$270,000/year required to afford the median tract's for-sale comps price under standard mortgage underwriting (see `docs/MODEL_AND_ASSUMPTIONS.md`'s Affordability-Constrained Demand section). Even Kauai's *least* expensive comps tract requires roughly double the countywide median household income. This gap exists in the data independent of any policy scenario — it describes Kauai today, not a projection.

## Commute mode and density

ACS B08301 (commute mode) and B01003 (population), joined to tract land area: used originally to calibrate the Parking Requirement Reduction scenario's transferability check (Sohoni & Lee 2025's own two axes — non-car commute share and population density). Kauai's population-weighted figures (~5.4% non-car commute share, ~675 people/sq mi) sit well outside the dense-urban range that parking-reform research is normally calibrated against — a caveat carried through that scenario's own documentation, not resolved here.

## What this baseline does and doesn't tell you

- It is tract-level where the underlying source supports it (ACS, real sales comps) and countywide where it doesn't (ZORI rent — the market is too thin for finer geography).
- It mixes vintages on purpose rather than forcing a false reconciliation: 2022 ACS estimates, Aug 2026 Zillow data, and Jan–Jun 2026 real transactions are three different snapshots in time, shown as what they are.
- It is Kauai-only. Honolulu, Hawaii County, and Maui have none of this baseline built out yet (see `docs/DATA_PROVENANCE.md`'s Known Limitations).
