---
kind: adr
mode: create_new
target_slug: capability-contract-answering-vs-reporting
title: 'okf-ext capability contract: answering capabilities vs reporting capabilities'
status: proposed
origins:
- ref: sources/2026-08-design-spec-okf-ext-search-capability
  source: ingest
  rationale: okf-ext capabilities come in two shapes and until search landed only one existed. A reporting capability (tags, schemas) emits Findings through extra_rules= and must claim a TOPIC prefix distinct from okf-io's built-in eight; an answering capability (search) returns data to a caller, files nothing, and must claim no prefix at all. The package's boundary tests encoded the reporting shape as universal (test_no_capability_claims_a_built_in_topic_prefix hardcoded {tags.TOPIC, schemas.TOPIC}); search forced it to become conditional. Four sibling capabilities are in flight that will each land on one side of this line, so it is worth settling as a contract before four authors decide independently.
  evidence:
  - okf_ext/search/__init__.py exports no TOPIC and no CODES; test_ext_boundaries.py:324 asserts the absence explicitly with the reason inline ('search answers questions; it files no findings')
  - test_no_capability_claims_a_built_in_topic_prefix was generalized to derive its capability set from the filesystem and to skip rather than fail a capability with no TOPIC (test_ext_boundaries.py:328-353); the spec predicted this edit at §8.2 and named it the only non-mechanical one of four
  - 'Four in-flight sibling work items will each need the distinction: 2026-08-06-feature-okf-ext-{moves,rules,tables,generators}-capability'
  - ADR-0008 defines the dotted-code topic namespace the reporting shape claims from and the answering shape declines
---
<!-- Body regenerated from origins[] while status: proposed. Do not edit here;
     approve via `gw wiki proposal approve adr-capability-contract-answering-vs-reporting`. -->

## Suggested Action

Create new adr page `adrs/capability-contract-answering-vs-reporting.md`.

## Evidence From Source

- okf_ext/search/__init__.py exports no TOPIC and no CODES; test_ext_boundaries.py:324 asserts the absence explicitly with the reason inline ('search answers questions; it files no findings')
- test_no_capability_claims_a_built_in_topic_prefix was generalized to derive its capability set from the filesystem and to skip rather than fail a capability with no TOPIC (test_ext_boundaries.py:328-353); the spec predicted this edit at §8.2 and named it the only non-mechanical one of four
- Four in-flight sibling work items will each need the distinction: 2026-08-06-feature-okf-ext-{moves,rules,tables,generators}-capability
- ADR-0008 defines the dotted-code topic namespace the reporting shape claims from and the answering shape declines

## Existing Pages Considered

- No existing pages were cited by the proposal reasoner.

## Reasoning Summary

No reasoning summary was captured.

## Potential Conflicts

- No conflicts identified.

## Implementation Notes

- No implementation notes captured.

## Origins

**ingest · [[sources/2026-08-design-spec-okf-ext-search-capability]]**
okf-ext capabilities come in two shapes and until search landed only one existed. A reporting capability (tags, schemas) emits Findings through extra_rules= and must claim a TOPIC prefix distinct from okf-io's built-in eight; an answering capability (search) returns data to a caller, files nothing, and must claim no prefix at all. The package's boundary tests encoded the reporting shape as universal (test_no_capability_claims_a_built_in_topic_prefix hardcoded {tags.TOPIC, schemas.TOPIC}); search forced it to become conditional. Four sibling capabilities are in flight that will each land on one side of this line, so it is worth settling as a contract before four authors decide independently.
