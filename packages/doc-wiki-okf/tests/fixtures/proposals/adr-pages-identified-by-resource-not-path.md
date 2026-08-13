---
kind: adr
mode: create_new
target_slug: pages-identified-by-resource-not-path
title: Pages are identified by resource, never by expected path
status: proposed
origins:
- ref: sources/2026-08-code-wiki-okf-epic-design-spec
  source: ingest
  rationale: The epic spec's E-I fixes page identity for every lane at once and is load-bearing for three distinct behaviours, but it lives only in a spec bullet; the mirror lane already carries a documented exception to it, and the not-yet-built source/proposal/work lanes will inherit it by imitation rather than by record.
  evidence:
  - 'E-I: ''Pages are found by resource, never by expected path (survey 4.4). The one exception is placement in the mirror lane: new file pages are always created at the mirrored path, because location-mirrors-repo is that lane''s point.'''
  - resources.py's resource_index()/get() is the only lookup every lane uses; entity-lanes' key claim is that this is what makes a hand-moved page update in place with no separate move-detection logic.
  - Deletion-as-reconciliation and sync.orphan-page both compare the graph/tracked-file set against pages found by resource, so identity-by-resource is a precondition of the drift layer, not just of the writer.
  - mirror/plan.py is the single place a path is computed for placement, which is exactly the documented exception -- an exception with no ADR is an exception a fourth lane can widen unnoticed.
---
<!-- Body regenerated from origins[] while status: proposed. Do not edit here;
     approve via `gw wiki proposal approve adr-pages-identified-by-resource-not-path`. -->

## Suggested Action

Create new adr page `adrs/pages-identified-by-resource-not-path.md`.

## Evidence From Source

- E-I: 'Pages are found by resource, never by expected path (survey 4.4). The one exception is placement in the mirror lane: new file pages are always created at the mirrored path, because location-mirrors-repo is that lane's point.'
- resources.py's resource_index()/get() is the only lookup every lane uses; entity-lanes' key claim is that this is what makes a hand-moved page update in place with no separate move-detection logic.
- Deletion-as-reconciliation and sync.orphan-page both compare the graph/tracked-file set against pages found by resource, so identity-by-resource is a precondition of the drift layer, not just of the writer.
- mirror/plan.py is the single place a path is computed for placement, which is exactly the documented exception -- an exception with no ADR is an exception a fourth lane can widen unnoticed.

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
The epic spec's E-I fixes page identity for every lane at once and is load-bearing for three distinct behaviours, but it lives only in a spec bullet; the mirror lane already carries a documented exception to it, and the not-yet-built source/proposal/work lanes will inherit it by imitation rather than by record.
