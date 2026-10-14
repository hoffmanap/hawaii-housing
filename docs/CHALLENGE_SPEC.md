# Challenge Specification

This document is the self-contained technical specification for the Housing Data and Technology Innovation Challenge. It lets this repository stand on its own: you should not need any other document to understand what to build and how your submission will be evaluated. The official [Challenge Handbook](https://housingchallenge.turnout.rocks/handbook.html) and the [Official Rules](https://housingchallenge.turnout.rocks/rules.html) remain authoritative for logistics, eligibility, and legal terms; where this spec and those documents conflict, they govern.

# The Problem

The United States faces a persistent housing shortage. Policymakers are pursuing reforms—legalizing accessory dwelling units, reducing parking requirements, allowing more housing near jobs and transit, and lowering development fees—but often lack accessible tools to estimate the likely effects of those reforms before implementation. This challenge asks you to build a transparent, replicable, and explainable tool that helps decision-makers understand how state-level pro-housing policy reforms are likely to affect housing supply.

# What to Build

Build a functional, interactive tool (simulator, dashboard, model, or
equivalent) that:

1. **Estimates state-level housing supply impacts.** Estimate aggregate, statewide impacts on housing production or housing stock relative to a baseline. You may model smaller geographies but must aggregate to the state level.
1. **Addresses the benchmark policy scenarios.** Model a required baseline plus at least three of the five benchmark scenarios below.
1. **Is transparent, replicable, and explainable.** A policymaker without a technical background should understand what the tool does, what it assumes, and what it cannot tell them. Clearly described and justified assumptions are welcome.
1. **Is open source.** Publish under an approved OSI-compliant license (see Licensing).

The challenge is methodology-agnostic. Your tool might be an econometric model, a simplified pro forma simulator, a rule-based policy engine, a geospatial analysis, a data-synthesis dashboard, or something else. Reviewers care more about clear, honest, policy-relevant insight than about forecasting complexity.

# Benchmark Policy Scenarios

Model a **baseline**—housing production under the current regulatory and economic environment with no policy changes—and at least **three of the five** scenarios:

1. **Accessory Dwelling Units (ADUs).** Allow one ADU on parcels with existing single-family buildings; detached and semi-detached ADUs of at least 600 sq ft; no additional parking required.
1. **Missing Middle / Gentle Density.** Allow 2–6 dwelling units on parcels currently restricted to single-family use, heights up to 3 stories. You may assume setback and lot-coverage requirements do not constrain 2–6 units.
1. **Transit-Oriented Density.** Increase allowable residential density near major transit stops via greater heights and/or floor area ratios. Define "major transit stop" and justify your proximity threshold. You may assume parking requirements are reduced or eliminated.
1. **Parking Requirement Reduction.** Reduce or eliminate minimum parking requirements. Specify assumptions about developer-provided parking absent mandates.
1. **Development Fee Reduction.** Reduce total development fees by 50% across all major fee categories.

# Shared Modeling Assumptions

For comparability across submissions:

1. **Time horizon:** assume a 5-year horizon.
1. **Housing outcome:** you may model permits, starts, completions, or housing-stock growth, but must clearly define and justify your choice.
1. **Development fees:** for the fee scenario, assume development fees total $50,000 per unit across all major fee categories.

For each scenario you address, show: how outputs change relative to baseline; what drives the differences; how sensitive results are to key inputs and assumptions; and what the model cannot tell policymakers.

# Deliverables

1. **Interactive tool** — functional, not a mockup.
1. **Slide deck** — no more than 30 slides, exported to PDF.
1. **Demo video** — a 5-minute recording of the tool in use, viewable without an account.
1. **Model & Assumptions Overview** — no more than 25 pages (single-spaced). See docs/MODEL_AND_ASSUMPTIONS.md.
1. **Source code** — this public repository, runnable per the README.
1. **Policy scenario comparison** — up to 4 pages (single-spaced). See docs/POLICY_SCENARIO_COMPARISON.md.
1. **Data provenance** — docs/DATA_PROVENANCE.md.
1. **Disclosures** — docs/DISCLOSURES.md.

# Eligibility Gates (Pass/Fail)

A submission must pass all of these before it is scored. They are pass/fail; a failed gate removes a submission from scoring.

1. **Reproducible setup.** The tool runs from the documented commands in the README, from a clean checkout, with no undocumented manual steps.
1. **Automated tests.** Tests cover the core analytical logic, pass on a clean checkout, and have a documented run command.
1. **No committed secrets.** No credentials, API keys, or tokens are committed. Secret scanning is enabled with zero unresolved alerts.
1. **Security scanning configured.** Dependabot alerts and CodeQL code scanning are enabled, with no unaddressed critical/high findings (or a documented justification for any that remain).
1. **OSI-compliant license.** The repository includes an approved license (see Licensing), and dependencies are permissively licensed.

See docs/REPO_SETUP_GUIDE.md to enable the repository security settings. These features are free for public GitHub repositories; enabling them is your responsibility.

# Licensing

Publish your submission source code under one of these permissive, OSI-compliant licenses:

1. **Apache License 2.0** — default and recommended (its express patent grant is the safest choice for tools that may be adopted or integrated downstream).
1. **MIT License**
1. **BSD 3-Clause "New" or "Revised" License**
1. **BSD 2-Clause "Simplified" License**

**Not permitted for submission code:** copyleft licenses (GPL, AGPL, LGPL, MPL, EUPL) and public-domain dedications (CC0, the Unlicense). Dependencies must also be permissively licensed and compatible with closed-source downstream integration. Publishing under an open-source license grants rights to use the code; it does not, by itself, transfer ownership.

# How Submissions Are Scored

Submissions that pass all eligibility gates are scored on:

| Criterion | Weight |
| --------- | ------ |
| Transparency & Explainability | 30% |
| Replicability & Scalability | 25% |
| Usability | 20% |
| Methodological Soundness | 15% |
| Policy Insight & Interpretation | 10% |

AI-assisted development is permitted and expected. Reviewers do not penalize AI assistance; they penalize code that cannot be verified, explained, or reproduced. You remain responsible for understanding, testing, and securing whatever you submit, and for disclosing material use of AI tools, pretrained models, external services, and third-party code (see docs/DISCLOSURES.md).

# Language and Tooling

Any language is acceptable (Python, JavaScript, Go, R, and others). The files in this template are configuration and documentation only; nothing forces a particular language or framework. Where a required tool does not support your language (for example, CodeQL), rely on the remaining tooling and document the gap.
