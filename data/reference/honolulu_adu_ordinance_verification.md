# Honolulu ADU Ordinance — Source Verification Log

**Primary source confirmed 2026-10-15**: Revised Ordinances of Honolulu (ROH) §21-5.50-3(a), "Accessory dwelling unit – standards," enacted via **Ordinance 25-2 (Bill 64 (2023), CD2, FD)** — verified from photographed excerpts of the actual code section and the ordinance's own title/stamp page, supplied by the user. This supersedes an earlier in-session error that misattributed Ordinance 25-2 to Bill 63 (2024) (a fee-waiver-extension bill, confirmed separately via Honolulu City Council committee records — Bill 63 is real but is NOT the ADU-standards ordinance).

## Confirmed directly from primary text (ROH §21-5.50-3(a))

- **Floor area cap**: 500 sq ft for zoning lots under 4,999 sq ft; 1,000 sq ft for lots 5,000 sq ft or more. (subsection (B))
- **One ADU per zoning lot** (including lots with existing multiple dwelling units); an ohana unit otherwise permitted on the lot does NOT count toward this one-ADU limit. (subsection (E))
- **Not permitted** on zoning lots in planned development housing or cluster housing, or on landlocked lots. (subsection (A))
- **No numeric minimum lot size** stated in this subsection.
- ADU may be new construction (attached or detached) or conversion of a legally established structure/attic/basement. (subsection (D))
- Must meet all development standards for the principal use in the underlying zoning district. (subsection (C))
- **Infrastructure gate**: ADUs only permitted where there is sufficient wastewater treatment/disposal, water supply, roadways, and first-responder resources. (subsection (G))
- **Parking**: cross-references §21-6.30(b) for adjustments/exemptions — text of §21-6.30(b) not yet retrieved.
- **Covenant conditions** (from the ordinance's own "Conditions of approval" section, recorded with the Bureau of Conveyances or Land Court): long-term residential occupancy only, no B&B/transient vacation use; restriction lapses upon ADU removal; binds all heirs/successors/assigns; owners may not submit the zoning lot to a Condominium Property Regime to separate ADU ownership from the principal dwelling.

## Still unverified / not yet retrieved

- Eligible zoning district list (secondary sources, e.g. steadily.com, claim Country and R-20/R-10/R-7.5/R-5/R-3.5, excluding AG-1/AG-2/apartment/commercial — not yet confirmed against a primary district-use table).
- Exact parking ratio and transit-proximity waiver distances (§21-6.30(b) and §21-6.30(d) — not yet retrieved in full).
- Owner-occupancy requirement status — this subsection contains none; secondary sources report a DPP sample-covenant clause requiring owner/relative occupancy that may be separate from, or in tension with, the codified ordinance text. Not resolved.

## Implication for the model

`COUNTY_CONFIG["honolulu"]`'s ADU floor-area and one-per-lot cap parameters can now be set from this verified primary text. Zoning-district eligibility and parking-waiver logic should remain flagged as unverified pending §21-6.30(b)/(d) and a confirmed district table, consistent with this project's standing rule against encoding unverified figures as if they were sourced.
