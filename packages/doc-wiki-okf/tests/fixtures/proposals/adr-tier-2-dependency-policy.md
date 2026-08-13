---
kind: adr
mode: create_new
target_slug: tier-2-dependency-policy
title: Dependency policy for tier 2 — okf-io's budget does not transfer wholesale
status: proposed
origins:
- ref: sources/2026-08-design-spec-scaffold-okf-ext-and-tag-management
  source: ingest
  rationale: The spec (§14.2) names this as ADR-shaped. okf-io's two-dependency budget is quoted often enough in this vault that where it stops applying needs recording as a decision, not as spec prose — and v0.1.0 already shipped two dependencies where §3 predicted one.
  evidence:
  - 'Of okf-io''s three reasons for a dependency budget, only one transfers: the purity/spec-claim reason does not (okf-ext is beyond-spec by definition), the versioning-anchor reason barely does (okf-ext is a leaf), and ''everyone pays'' transfers only to unconditional dependencies.'
  - 'Structurally guaranteed and worth stating: nothing in okf-ext''s dependency list can affect okf-io''s purity, because ADR-0005 makes the direction one-way. A budget on tier 2 was never load-bearing for the purity goal.'
  - 'The policy as shipped in packages/okf-ext/README.md: unconditional dependencies stay few and each is justified in the README; capability-specific dependencies ship as extras (okf-ext[schemas]) with the capability __init__ raising an ImportError naming the extra; promotion is triggered by the §2.1 conditions, never by a dependency count.'
  - 'Already exercised: v0.1.0 declares two runtime dependencies (okf-io>=0.1,<0.2 and ruamel.yaml>=0.18), not §3''s predicted one — ruamel is declared rather than inherited because relying on it arriving through okf-io breaks the day the core swaps YAML libraries, and importing okf_io._yaml would couple tier 2 to a private module of tier 1.'
---
<!-- Body regenerated from origins[] while status: proposed. Do not edit here;
     approve via `gw wiki proposal approve adr-tier-2-dependency-policy`. -->

## Suggested Action

Create new adr page `adrs/tier-2-dependency-policy.md`.

## Evidence From Source

- Of okf-io's three reasons for a dependency budget, only one transfers: the purity/spec-claim reason does not (okf-ext is beyond-spec by definition), the versioning-anchor reason barely does (okf-ext is a leaf), and 'everyone pays' transfers only to unconditional dependencies.
- Structurally guaranteed and worth stating: nothing in okf-ext's dependency list can affect okf-io's purity, because ADR-0005 makes the direction one-way. A budget on tier 2 was never load-bearing for the purity goal.
- The policy as shipped in packages/okf-ext/README.md: unconditional dependencies stay few and each is justified in the README; capability-specific dependencies ship as extras (okf-ext[schemas]) with the capability __init__ raising an ImportError naming the extra; promotion is triggered by the §2.1 conditions, never by a dependency count.
- Already exercised: v0.1.0 declares two runtime dependencies (okf-io>=0.1,<0.2 and ruamel.yaml>=0.18), not §3's predicted one — ruamel is declared rather than inherited because relying on it arriving through okf-io breaks the day the core swaps YAML libraries, and importing okf_io._yaml would couple tier 2 to a private module of tier 1.

## Existing Pages Considered

- No existing pages were cited by the proposal reasoner.

## Reasoning Summary

No reasoning summary was captured.

## Potential Conflicts

- No conflicts identified.

## Implementation Notes

- No implementation notes captured.

## Origins

**ingest · [[sources/2026-08-design-spec-scaffold-okf-ext-and-tag-management]]**
The spec (§14.2) names this as ADR-shaped. okf-io's two-dependency budget is quoted often enough in this vault that where it stops applying needs recording as a decision, not as spec prose — and v0.1.0 already shipped two dependencies where §3 predicted one.
