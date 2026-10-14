# Copilot Instructions — Housing Data and Technology Innovation Challenge

You are helping a participant build a submission for the Housing Data and
Technology Innovation Challenge. The full specification is in
[docs/CHALLENGE_SPEC.md](../docs/CHALLENGE_SPEC.md); read it for details. This
file summarizes the context and the rules that most affect how you should write
code and documentation.

## What the participant is building

A transparent, replicable, explainable tool that estimates how state-level
pro-housing policy reforms affect housing supply. The tool estimates aggregate,
statewide impacts on housing production or stock relative to a baseline, and
addresses a required baseline plus at least three of five benchmark scenarios:
ADUs, Missing Middle / Gentle Density, Transit-Oriented Density, Parking
Requirement Reduction, and Development Fee Reduction. Use a 5-year time horizon
and, for the fee scenario, assume development fees of $50,000 per unit.

The challenge is methodology-agnostic. Favor clarity and honest, policy-relevant
insight over forecasting complexity.

## Priorities when writing code

1. **Transparency over cleverness.** A non-technical policymaker should be able
   to follow what the tool does and what it assumes. Keep assumptions explicit,
   named, and separated from mechanics. Prefer readable, well-labeled code and
   clear parameter definitions.
1. **Reproducibility.** The tool must run from a clean checkout using only the
   documented setup, run, and test commands in the README. Do not introduce
   undocumented manual steps. If you add a dependency or a command, update the
   README's command contract.
1. **Tested core logic.** Write and maintain automated tests for the core
   analytical logic. Tests must pass on a clean checkout and have a documented
   run command. When you change the model, update its tests.
1. **Security.** Never hardcode or commit secrets, API keys, or tokens; read
   them from environment variables (see `.env.example`) and keep `.env`
   gitignored. Avoid introducing dependencies with known critical/high
   vulnerabilities.
1. **Separation of data from code.** Do not commit raw or large datasets. Point
   to sources and document provenance in docs/DATA_PROVENANCE.md.

## Requirements that gate the submission

These are pass/fail; a submission that misses any does not advance to scoring:

1. Reproducible setup from documented commands.
1. Automated tests covering core logic, passing on a clean checkout.
1. No committed secrets; secret scanning enabled.
1. Security scanning configured (Dependabot alerts and CodeQL code scanning).
1. An approved OSI-compliant license.

## Licensing rules

1. Use a permissive, OSI-compliant license: **Apache License 2.0 (default and
   recommended)**, MIT, BSD 3-Clause, or BSD 2-Clause. Do not change the license
   without the participant's explicit instruction.
1. Do **not** add dependencies under copyleft licenses (GPL, AGPL, LGPL, MPL,
   EUPL) or with noncommercial or unknown licenses. Keep dependencies
   permissively licensed and compatible with closed-source downstream
   integration.
1. If you copy or adapt external code, note it and its license so it can be
   recorded in NOTICE and docs/DISCLOSURES.md.

## Disclosure expectations

AI-assisted development is welcome, but material use must be disclosed. When you
generate substantive logic, incorporate a pretrained model, or add an external
API or hosted service, remind the participant to record it in
docs/DISCLOSURES.md, and confirm the terms permit open-source release and
downstream use.

## Language and tooling

Any language is fine (Python, JavaScript, Go, R, and others). Match the
participant's existing stack; do not switch languages or frameworks without being
asked. If a required tool does not support the chosen language (for example,
CodeQL), rely on the remaining tooling and note the gap rather than forcing a
change.

## Deliverable limits to respect

When helping with documentation or presentation assets, keep to the caps: slide
deck no more than 30 slides; Model & Assumptions Overview no more than 25 pages;
policy scenario comparison up to 4 pages; demo video 5 minutes.
