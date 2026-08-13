---
kind: adr
mode: create_new
target_slug: migration-refusal-policy
title: Migration refusals are all-or-nothing per document, reported, and machine-readable
status: proposed
origins:
- ref: sources/2026-08-design-spec-v0-1-v0-2-migration-rewriter-for-okf-io
  source: ingest
  rationale: 'A caller-facing contract that outlives the spec that produced it: it governs how any okf-io write path declines work, and the live-vault migration work item is already a named downstream consumer. ADR-0003 decided that a rewriter exists and what it rewrites; it decided nothing about what happens when the rewriter cannot proceed.'
  evidence:
  - 'All-or-nothing per document (spec 5.4): an impure citations section or a single prose item leaves the section exactly as written, because partial surgery means rewriting a section rather than deleting it and leaves a document half in each format. Rewrite A stays independent of rewrite B.'
  - Every refusal carries a closed machine-readable reason from Literal[unparseable, impure-section, not-a-resource, conflicting-provenance, not-an-instant, multiple-citations-sections] so a caller branches without substring-matching prose (migrate.py:79-86).
  - 'Reported, not silent, wherever anything else warns: a non-instant timestamp still fires legacy.timestamp, so its decline comes back as not-an-instant rather than leaving the caller with a warning no result explains. The one silent exemption is the blank/whitespace timestamp, which _rules/legacy.py also treats as absent, so nothing warns and there is nothing to report.'
  - 'Refusal is preferred over any output that would be half-migrated: a second # Citations heading refuses the whole document, because migrating the first section would strand the second behind an authored sources where the fallback stops firing and nothing can ever see it again.'
  - 'Refusing never mutates: results are dry_run=True by default and Migration carries before/after/changed/diff() exactly as IndexUpdate and LogAppend do, so a declined document is reported through the same vocabulary as a rewritten one.'
---
<!-- Body regenerated from origins[] while status: proposed. Do not edit here;
     approve via `gw wiki proposal approve adr-migration-refusal-policy`. -->

## Suggested Action

Create new adr page `adrs/migration-refusal-policy.md`.

## Evidence From Source

- All-or-nothing per document (spec 5.4): an impure citations section or a single prose item leaves the section exactly as written, because partial surgery means rewriting a section rather than deleting it and leaves a document half in each format. Rewrite A stays independent of rewrite B.
- Every refusal carries a closed machine-readable reason from Literal[unparseable, impure-section, not-a-resource, conflicting-provenance, not-an-instant, multiple-citations-sections] so a caller branches without substring-matching prose (migrate.py:79-86).
- Reported, not silent, wherever anything else warns: a non-instant timestamp still fires legacy.timestamp, so its decline comes back as not-an-instant rather than leaving the caller with a warning no result explains. The one silent exemption is the blank/whitespace timestamp, which _rules/legacy.py also treats as absent, so nothing warns and there is nothing to report.
- Refusal is preferred over any output that would be half-migrated: a second # Citations heading refuses the whole document, because migrating the first section would strand the second behind an authored sources where the fallback stops firing and nothing can ever see it again.
- Refusing never mutates: results are dry_run=True by default and Migration carries before/after/changed/diff() exactly as IndexUpdate and LogAppend do, so a declined document is reported through the same vocabulary as a rewritten one.

## Existing Pages Considered

- No existing pages were cited by the proposal reasoner.

## Reasoning Summary

No reasoning summary was captured.

## Potential Conflicts

- No conflicts identified.

## Implementation Notes

- No implementation notes captured.

## Origins

**ingest · [[sources/2026-08-design-spec-v0-1-v0-2-migration-rewriter-for-okf-io]]**
A caller-facing contract that outlives the spec that produced it: it governs how any okf-io write path declines work, and the live-vault migration work item is already a named downstream consumer. ADR-0003 decided that a rewriter exists and what it rewrites; it decided nothing about what happens when the rewriter cannot proceed.
