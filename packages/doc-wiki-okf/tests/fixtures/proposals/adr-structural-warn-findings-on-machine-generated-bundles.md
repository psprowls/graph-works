---
kind: adr
mode: create_new
target_slug: structural-warn-findings-on-machine-generated-bundles
title: Should sections.unfilled and frontmatter.description-recommended apply to machine-generated pages
status: proposed
origins:
- ref: sources/2026-08-first-live-bundle-proof-design-spec
  source: ingest
  rationale: A 100%-mechanically-generated OKF bundle can never reach validate --strict zero, because both codes fire once per page by construction whenever prose generation is out of scope. Confirmed with the coordinator as a shared okf-ext decision rather than one a consumer package may take unilaterally — and currently tracked by nothing.
  evidence:
  - 'The first live bundle measured sections.unfilled: 1251 and frontmatter.description-recommended: 1251 — one per page — with every other rule family at zero except the separately-filed dot-directory gap.'
  - Both codes were accepted as permanent and structural for that bundle rather than fixed, so 'validate --strict exits 0' as an acceptance criterion is unmeetable for any generated bundle under the same scope.
  - Exempting machine-written pages would change severity or applicability in okf_ext.sections/okf_io frontmatter rules, affecting every consumer, not just code-wiki-okf.
---
<!-- Body regenerated from origins[] while status: proposed. Do not edit here;
     approve via `gw wiki proposal approve adr-structural-warn-findings-on-machine-generated-bundles`. -->

## Suggested Action

Create new adr page `adrs/structural-warn-findings-on-machine-generated-bundles.md`.

## Evidence From Source

- The first live bundle measured sections.unfilled: 1251 and frontmatter.description-recommended: 1251 — one per page — with every other rule family at zero except the separately-filed dot-directory gap.
- Both codes were accepted as permanent and structural for that bundle rather than fixed, so 'validate --strict exits 0' as an acceptance criterion is unmeetable for any generated bundle under the same scope.
- Exempting machine-written pages would change severity or applicability in okf_ext.sections/okf_io frontmatter rules, affecting every consumer, not just code-wiki-okf.

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
A 100%-mechanically-generated OKF bundle can never reach validate --strict zero, because both codes fire once per page by construction whenever prose generation is out of scope. Confirmed with the coordinator as a shared okf-ext decision rather than one a consumer package may take unilaterally — and currently tracked by nothing.
