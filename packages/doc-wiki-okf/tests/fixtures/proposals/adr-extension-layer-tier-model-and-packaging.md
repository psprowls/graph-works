---
kind: adr
mode: create_new
target_slug: extension-layer-tier-model-and-packaging
title: Extension layer — three-tier model and one-package packaging
status: proposed
origins:
- ref: sources/2026-08-design-spec-scaffold-okf-ext-and-tag-management
  source: ingest
  rationale: 'The spec (§14.1) names this as ADR-shaped and defers it to ingest: it decides where every future beyond-spec capability lands, and is a sibling to ADR-0005 that sharpens rather than supersedes it.'
  evidence:
  - 'Three tiers replace the flat package list of the okf-io design doc §5: core (okf-io, spec only), extension layer (beyond-spec over any bundle), applications (produce/consume bundles). Shipped verbatim as the opening table of packages/okf-ext/README.md.'
  - 'Option C chosen over three alternatives: one distribution, capabilities as self-contained subpackages, explicit promotion triggers (a runtime dependency that cannot be cleanly optional, or a consumer wanting one capability without the rest). Graduation is a directory move plus a re-export shim kept one minor version.'
  - 'Option D — entry-point plugin discovery inside okf-io — is rejected and recorded so the workspace cannot drift into it: it would make the core implicitly behavioural, contradicting ''nothing rejects a bundle, every rule reports, the caller decides''.'
  - 'Enforcement shipped split across two mechanisms because import-linter can only express half of it: a layers contract in the root pyproject.toml, plus an AST test in packages/okf-ext/tests/test_ext_boundaries.py for the ancestor-import half grimp cannot see.'
- ref: sources/2026-08-design-spec-okf-ext-sections-capability
  source: ingest
  rationale: 'The tier model has a second axis the original proposal does not cover: an intra-package shared layer beneath the capabilities, now three modules deep. okf_ext.splice is the second module hoisted into it rather than copied, and the hoist rule plus its registration requirement are stated only in a README paragraph and an AST test. Adding this origin so the ADR, when written, decides both axes at once instead of needing a sibling six months later.'
  evidence:
  - 'The layers contract now has four rows, not two: capabilities (eight, colon-separated as siblings) : okf_ext.splice + okf_ext.writing : okf_ext.body : okf_ext.context (root pyproject.toml at 6899278).'
  - 'The hoist rule is argued as debt prevention with a named threshold: copying tables/splice.py''s five privates ''would make this the third instance of a pattern the README already confesses to'' -- render/rule.py and health/rule.py already duplicate _LOG_NAME verbatim (design spec §7).'
  - 'New-module-rather-than-widen-body is itself a decision with a stated reason: body is memoized, parse-once and read-only, so stapling write primitives onto it muddies a module whose character is worth keeping; splice is its write-side counterpart and writing stays the file-level probe/stage/commit engine one layer below (okf_ext/splice.py:12-19).'
  - 'Registration fails silently, which is the ADR-shaped part: a shared module missing from SHARED in tests/test_ext_boundaries.py is classified as a capability, making the independence contract quietly incomplete (design spec §8; packages/okf-ext/README.md).'
  - 'The refactor''s acceptance condition is a falsifiable one worth recording: the tables suite must stay green with no assertion edits, because ''a test that has to be edited to accommodate the hoist is evidence the hoist changed behaviour''. It held -- no test_tables_*.py file appears in 6899278''s diff.'
- ref: sources/2026-08-design-spec-okf-ext-generators-capability
  source: ingest
  rationale: 'The graduation recipe has a reverse direction the proposal does not cover. okf_ext.shape is the fourth hoist and the first shared module that reads files, and the README now documents the same shim-for-one-minor-version recipe run inwards -- a type moving OUT of a capability into the shared layer -- with an explicit removal version. That is a packaging rule with a deprecation clock, decided twice by repetition and written down only in a README paragraph. The shared-layer axis has also now grown a criterion the earlier origins did not have: what counts as ''shared'' was widened to admit file I/O, and the widening was argued rather than assumed.'
  evidence:
  - 'The layers contract now has five rows, not four: capabilities (nine names, 161 characters) : okf_ext.shape : okf_ext.splice + okf_ext.writing : okf_ext.body : okf_ext.context (root pyproject.toml at ad1ad04), with an inline comment stating that the capabilities row is expected to keep growing one name at a time with no wrap strategy because the test is what keeps it honest, not its width.'
  - 'The README documents the graduation recipe run in reverse, with a deprecation clock: ''SectionSpec, TypeSections, SectionSet, SectionError and load_sections moved from okf_ext.sections to okf_ext.shape in 0.4.0... the shim comes out one minor version after the move, at 0.5.0.'' Nothing schedules or enforces that removal.'
  - 'The definition of the shared layer was widened deliberately: okf_ext.shape is ''the first shared module that reads files -- a widening stated rather than smuggled: the layer already held okf_ext.context, which is configuration, and a declaration loader is configuration that happens to live on disk'' (packages/okf-ext/README.md; okf_ext/shape/__init__.py).'
  - 'The rejected alternatives are the ADR-shaped part and are argued on contract visibility rather than taste: a Protocol ''would satisfy the linter while leaving the coupling real... The contract exists to make coupling visible; passing it by making coupling invisible is the one way to fail it that still shows green'' (design spec §3).'
  - 'The hoist forces a version bump under ADR-0007 even when every caller keeps working: okf-ext went 0.3.1 -> 0.4.0 because ''a public API moving behind a shim is a minor even though the shim keeps callers working''. That is the packaging rule and the versioning rule interacting, and neither page currently states the interaction.'
  - 'The SHARED-registration requirement held on its fourth exercise: ''shape'' was added to SHARED in tests/test_ext_boundaries.py:39, without which a shared module is silently classified as a capability and the independence contract is quietly incomplete.'
---
<!-- Body regenerated from origins[] while status: proposed. Do not edit here;
     approve via `gw wiki proposal approve adr-extension-layer-tier-model-and-packaging`. -->

## Suggested Action

Create new adr page `adrs/extension-layer-tier-model-and-packaging.md`.

## Evidence From Source

- Three tiers replace the flat package list of the okf-io design doc §5: core (okf-io, spec only), extension layer (beyond-spec over any bundle), applications (produce/consume bundles). Shipped verbatim as the opening table of packages/okf-ext/README.md.
- Option C chosen over three alternatives: one distribution, capabilities as self-contained subpackages, explicit promotion triggers (a runtime dependency that cannot be cleanly optional, or a consumer wanting one capability without the rest). Graduation is a directory move plus a re-export shim kept one minor version.
- Option D — entry-point plugin discovery inside okf-io — is rejected and recorded so the workspace cannot drift into it: it would make the core implicitly behavioural, contradicting 'nothing rejects a bundle, every rule reports, the caller decides'.
- Enforcement shipped split across two mechanisms because import-linter can only express half of it: a layers contract in the root pyproject.toml, plus an AST test in packages/okf-ext/tests/test_ext_boundaries.py for the ancestor-import half grimp cannot see.
- The layers contract now has four rows, not two: capabilities (eight, colon-separated as siblings) : okf_ext.splice + okf_ext.writing : okf_ext.body : okf_ext.context (root pyproject.toml at 6899278).
- The hoist rule is argued as debt prevention with a named threshold: copying tables/splice.py's five privates 'would make this the third instance of a pattern the README already confesses to' -- render/rule.py and health/rule.py already duplicate _LOG_NAME verbatim (design spec §7).
- New-module-rather-than-widen-body is itself a decision with a stated reason: body is memoized, parse-once and read-only, so stapling write primitives onto it muddies a module whose character is worth keeping; splice is its write-side counterpart and writing stays the file-level probe/stage/commit engine one layer below (okf_ext/splice.py:12-19).
- Registration fails silently, which is the ADR-shaped part: a shared module missing from SHARED in tests/test_ext_boundaries.py is classified as a capability, making the independence contract quietly incomplete (design spec §8; packages/okf-ext/README.md).
- The refactor's acceptance condition is a falsifiable one worth recording: the tables suite must stay green with no assertion edits, because 'a test that has to be edited to accommodate the hoist is evidence the hoist changed behaviour'. It held -- no test_tables_*.py file appears in 6899278's diff.
- The layers contract now has five rows, not four: capabilities (nine names, 161 characters) : okf_ext.shape : okf_ext.splice + okf_ext.writing : okf_ext.body : okf_ext.context (root pyproject.toml at ad1ad04), with an inline comment stating that the capabilities row is expected to keep growing one name at a time with no wrap strategy because the test is what keeps it honest, not its width.
- The README documents the graduation recipe run in reverse, with a deprecation clock: 'SectionSpec, TypeSections, SectionSet, SectionError and load_sections moved from okf_ext.sections to okf_ext.shape in 0.4.0... the shim comes out one minor version after the move, at 0.5.0.' Nothing schedules or enforces that removal.
- The definition of the shared layer was widened deliberately: okf_ext.shape is 'the first shared module that reads files -- a widening stated rather than smuggled: the layer already held okf_ext.context, which is configuration, and a declaration loader is configuration that happens to live on disk' (packages/okf-ext/README.md; okf_ext/shape/__init__.py).
- The rejected alternatives are the ADR-shaped part and are argued on contract visibility rather than taste: a Protocol 'would satisfy the linter while leaving the coupling real... The contract exists to make coupling visible; passing it by making coupling invisible is the one way to fail it that still shows green' (design spec §3).
- The hoist forces a version bump under ADR-0007 even when every caller keeps working: okf-ext went 0.3.1 -> 0.4.0 because 'a public API moving behind a shim is a minor even though the shim keeps callers working'. That is the packaging rule and the versioning rule interacting, and neither page currently states the interaction.
- The SHARED-registration requirement held on its fourth exercise: 'shape' was added to SHARED in tests/test_ext_boundaries.py:39, without which a shared module is silently classified as a capability and the independence contract is quietly incomplete.

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
The spec (§14.1) names this as ADR-shaped and defers it to ingest: it decides where every future beyond-spec capability lands, and is a sibling to ADR-0005 that sharpens rather than supersedes it.

**ingest · [[sources/2026-08-design-spec-okf-ext-sections-capability]]**
The tier model has a second axis the original proposal does not cover: an intra-package shared layer beneath the capabilities, now three modules deep. okf_ext.splice is the second module hoisted into it rather than copied, and the hoist rule plus its registration requirement are stated only in a README paragraph and an AST test. Adding this origin so the ADR, when written, decides both axes at once instead of needing a sibling six months later.

**ingest · [[sources/2026-08-design-spec-okf-ext-generators-capability]]**
The graduation recipe has a reverse direction the proposal does not cover. okf_ext.shape is the fourth hoist and the first shared module that reads files, and the README now documents the same shim-for-one-minor-version recipe run inwards -- a type moving OUT of a capability into the shared layer -- with an explicit removal version. That is a packaging rule with a deprecation clock, decided twice by repetition and written down only in a README paragraph. The shared-layer axis has also now grown a criterion the earlier origins did not have: what counts as 'shared' was widened to admit file I/O, and the widening was argued rather than assumed.
