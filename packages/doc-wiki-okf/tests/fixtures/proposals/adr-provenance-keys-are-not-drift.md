---
kind: adr
mode: create_new
target_slug: provenance-keys-are-not-drift
title: Which layer owns 'provenance keys are not drift'
status: proposed
origins:
- ref: sources/2026-08-first-live-bundle-proof-design-spec
  source: ingest
  rationale: A decision was taken implicitly by fda4539 (keep the provenance exclusion at each call site rather than push it into okf_ext.generators.plan_regenerate/key_edits); three call sites in code-wiki-okf now implement the same rule independently, the work item that would have recorded it is closed, and the decision lives nowhere but a commit message.
  evidence:
  - okf_ext.generators.frontmatter.key_edits compares owned keys by literal equality, so a freshly stamped generated.at is never equal to disk and every page reads as content-stale.
  - entities/sync.py defines _PROVENANCE_KEYS for exactly this failure mode but wired it only into plan_entities' read-only preview, not into sync_entities' write path — every one of the live bundle's 52 entity pages was rewritten on every run.
  - mirror.plan._render_matches_disk already carried its own independent copy of the same exclusion; two of three call sites were right, which is the argument for one owner.
  - work/_archive/bug-entities-generated-at-not-idempotent's own plan table asked for 'an ADR or explicit decision recording which layer owns this' and the item resolved without one.
---
<!-- Body regenerated from origins[] while status: proposed. Do not edit here;
     approve via `gw wiki proposal approve adr-provenance-keys-are-not-drift`. -->

## Suggested Action

Create new adr page `adrs/provenance-keys-are-not-drift.md`.

## Evidence From Source

- okf_ext.generators.frontmatter.key_edits compares owned keys by literal equality, so a freshly stamped generated.at is never equal to disk and every page reads as content-stale.
- entities/sync.py defines _PROVENANCE_KEYS for exactly this failure mode but wired it only into plan_entities' read-only preview, not into sync_entities' write path — every one of the live bundle's 52 entity pages was rewritten on every run.
- mirror.plan._render_matches_disk already carried its own independent copy of the same exclusion; two of three call sites were right, which is the argument for one owner.
- work/_archive/bug-entities-generated-at-not-idempotent's own plan table asked for 'an ADR or explicit decision recording which layer owns this' and the item resolved without one.

## Existing Pages Considered

- No existing pages were cited by the proposal reasoner.

## Reasoning Summary

No reasoning summary was captured.

## Potential Conflicts

- No conflicts identified.

## Implementation Notes

- No implementation notes captured.

## Origins

**ingest · [[sources/2026-08-first-live-bundle-proof-design-spec]]**
A decision was taken implicitly by fda4539 (keep the provenance exclusion at each call site rather than push it into okf_ext.generators.plan_regenerate/key_edits); three call sites in code-wiki-okf now implement the same rule independently, the work item that would have recorded it is closed, and the decision lives nowhere but a commit message.
