---
kind: adr
mode: create_new
target_slug: generated-bundle-and-curated-vault-coexistence
title: What happens to the hand-maintained vault now that a graph-derived bundle exists
status: proposed
origins:
- ref: sources/2026-08-code-wiki-okf-epic-design-spec
  source: ingest
  rationale: The epic declares the generated bundle 'a fresh bundle, not a conversion of either existing graph-wiki vault' and puts vault conversion out of scope, but never says what the endgame is -- the two artifacts now both exist, describe overlapping subject matter in incompatible link dialects, and nothing records whether they converge, one replaces the other, or they coexist permanently.
  evidence:
  - 'Epic spec Goal: ''generates and updates a standalone OKF v0.2 bundle from the code graph -- a fresh bundle, not a conversion of either existing graph-wiki vault.'' Out of scope: ''any conversion of the existing graph-wiki vaults.'''
  - 'The two artifacts use incompatible link dialects by decision: the generated bundle is root-absolute markdown (''the bundle is born conformant, no wikilinks anywhere''), the curated vault is Obsidian wikilinks. A merge is a rewrite, not a union.'
  - The first live bundle was deliberately written to a scratch directory outside every git tree (/Users/pat/Personal/code-wiki-okf-live-bundle/), confirmed with the coordinator, so today the generated artifact is tracked by nothing.
  - The epic spec calls the result 'the bundle the graph-wiki port will grow into', which presumes a convergence nobody has designed; work/2026-08-05-feature-live-vault-migration-to-okf covers migrating the existing vault to OKF but is a separate, unstarted item.
  - The generated bundle's entity lanes and the curated vault's entities/ directory document the same packages from the same graph, so a reader with both has two answers and no stated precedence.
---
<!-- Body regenerated from origins[] while status: proposed. Do not edit here;
     approve via `gw wiki proposal approve adr-generated-bundle-and-curated-vault-coexistence`. -->

## Suggested Action

Create new adr page `adrs/generated-bundle-and-curated-vault-coexistence.md`.

## Evidence From Source

- Epic spec Goal: 'generates and updates a standalone OKF v0.2 bundle from the code graph -- a fresh bundle, not a conversion of either existing graph-wiki vault.' Out of scope: 'any conversion of the existing graph-wiki vaults.'
- The two artifacts use incompatible link dialects by decision: the generated bundle is root-absolute markdown ('the bundle is born conformant, no wikilinks anywhere'), the curated vault is Obsidian wikilinks. A merge is a rewrite, not a union.
- The first live bundle was deliberately written to a scratch directory outside every git tree (/Users/pat/Personal/code-wiki-okf-live-bundle/), confirmed with the coordinator, so today the generated artifact is tracked by nothing.
- The epic spec calls the result 'the bundle the graph-wiki port will grow into', which presumes a convergence nobody has designed; work/2026-08-05-feature-live-vault-migration-to-okf covers migrating the existing vault to OKF but is a separate, unstarted item.
- The generated bundle's entity lanes and the curated vault's entities/ directory document the same packages from the same graph, so a reader with both has two answers and no stated precedence.

## Existing Pages Considered

- No existing pages were cited by the proposal reasoner.

## Reasoning Summary

No reasoning summary was captured.

## Potential Conflicts

- No conflicts identified.

## Implementation Notes

- No implementation notes captured.

## Origins

**ingest · [[sources/2026-08-code-wiki-okf-epic-design-spec]]**
The epic declares the generated bundle 'a fresh bundle, not a conversion of either existing graph-wiki vault' and puts vault conversion out of scope, but never says what the endgame is -- the two artifacts now both exist, describe overlapping subject matter in incompatible link dialects, and nothing records whether they converge, one replaces the other, or they coexist permanently.
