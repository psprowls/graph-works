---
kind: adr
mode: update_existing
target_slug: tier-2-writers-return-plans-not-dry-run-flags
title: Tier-2 writers return a plan value; okf-io's dry_run= flag stops at the core
status: proposed
origins:
- ref: sources/2026-08-design-spec-okf-ext-sections-capability
  source: ingest
  rationale: Three okf-ext capabilities have now made the same choice against okf-io's three writers, each restating the reason in its own docstring, and none of them recorded it as a decision. okf-io's update_index/append_log_entry/migrate all default dry_run=True; okf_ext.tables, okf_ext.moves and okf_ext.sections all ship a plan value with no flag. That is a workspace convention being established by repetition, and the fourth author will either copy it without knowing why or reopen it. It also has a real consequence -- the two halves of the workspace now disagree about what 'preview a write' means.
  evidence:
  - 'okf-io: all three writers default to dry_run=True, and IndexUpdate/LogAppend/Migration share one vocabulary (before, after, a changed property that renders nothing, a diff() that writes nothing) -- repo CLAUDE.md, ''The writers''.'
  - 'okf_ext.sections §6.2 states the divergence and chooses against the core deliberately: ''No dry_run flag. The plan IS the dry run -- the choice tables already made... Consistency inside tier 2 beats consistency with okf-io''s three writers here, and tables is the nearer precedent.'''
  - 'The stated reason is expressive, not stylistic: ''apply 38 of these 40'' is a thing a boolean cannot express (RenamePlan''s original argument, repeated verbatim by SplicePlan and SectionPlan).'
  - 'A second consequence rides along: idempotence becomes an observable value rather than a return code -- SectionPlan.is_empty is the documented ''nothing to do'' signal (sections/model.py:120-137).'
  - 'A third: staleness needs a digest carried on the plan, so every tier-2 plan ships a body digest and a WriteFailure(kind=''stale'') that a dry_run boolean would have no place to put (sections/model.py:98-117, sections/scaffold.py:368-376).'
- ref: sources/2026-08-design-spec-okf-ext-generators-capability
  source: ingest
  rationale: Fourth instance, and the first that stresses the convention rather than merely repeating it. okf_ext.generators ships a RegenerationPlan carrying edits to BOTH halves of a document -- frontmatter keys and body sections -- while deliberately digesting only one of them, which is a shape a dry_run boolean has nowhere to put and which the three earlier instances never had to reason about. The convention is now unanimous across every tier-2 writer and still unrecorded anywhere but four module docstrings.
  evidence:
  - 'Fourth instance: tables, moves, sections and now generators all return a plan value with no dry_run flag, against okf-io''s three writers which all default dry_run=True. ''Idempotence surfaces as an empty plan'' is restated verbatim in RegenerationPlan''s docstring (okf_ext/generators/model.py:120).'
  - 'The first plan to carry edits to both document halves: Regeneration carries key_edits AND section_edits plus the whole new body in ''after'', so apply is a pure digest-check-then-write with no chance of the plan and the apply disagreeing about what an edit means (okf_ext/generators/model.py:92).'
  - 'And the first to digest asymmetrically, deliberately: a whole-body digest with WriteFailure(kind=''stale'') for the body, and NO staleness check at all on frontmatter, because ''a key name does not move'' and refusing ''would be the capability second-guessing its own contract'' (design spec §6). A dry_run boolean has no place to express a per-half staleness policy.'
  - 'The consequence is a documented data-loss window rather than a check: a human edit to an owned key made between plan and apply is lost without a report, and the stated remedy is the declaration -- ''a key humans edit does not belong in owned:''. That is a contract a preview flag cannot carry and a plan value can.'
  - generators is also the second tier-2 capability to need nothing added to either shared union (SkipReason, FailureKind) -- packages/okf-ext/src/okf_ext/writing.py does not appear in ad1ad04's diff at all -- which is evidence the plan/apply vocabulary has stabilised across four capabilities.
- ref: sources/2026-08-per-item-layout-filing-conformant-vault-design-spec
  source: ingest
  rationale: Fifth instance, and the first from OUTSIDE tier 2 — which breaks the proposal's own title. work-tracker-okf is a tier-3 package that depends on okf-io directly and could have copied its dry_run=True writers; C2-E instead ships FilingPlan + apply() with no flag, citing the package's own two-children-old convention (advance() returns an AdvancePlan and mutates nothing) and naming okf_ext.moves and okf_ext.tags as the precedent. The convention is therefore not 'tier 2 diverges from the core' but 'everything above the core diverges from the core', which makes okf-io's three writers the exception rather than the rule — a materially different ADR than the one four tier-2 origins were arguing for.
  evidence:
  - 'First tier-3 instance: work_tracker_okf.filing ships FilingPlan (frozen, with before/after-style ''changed'' property and a diff() that writes nothing) plus a separate apply(plan) -> Path, and the spec states ''dry_run is not a parameter: not calling apply IS the dry run, which is the same split okf_ext.moves and okf_ext.tags make and for the same reason'' (design spec §3.2).'
  - 'The tier-3 package reached the convention by an independent route: it cites its OWN prior child (''this package wrote the convention down two children ago -- advance() returns an AdvancePlan and mutates nothing'') rather than inheriting it from okf-ext, which is evidence of a workspace-wide norm rather than one package''s habit.'
  - 'Refusals-as-data rides along again, one tier up: FilingRefusal is a closed Literal vocabulary (''page-exists'', ''directory-exists'', ''unknown-type'') where work-io raised FileExistsError, and there is deliberately no force= flag -- the same ''a fact about the target, not an exception'' move the tier-2 plans make with SkipReason/FailureKind.'
  - 'The same child also documents the convention''s LIMIT, which no prior origin did: results.write_results is a deliberate direct write with no plan, because ''a stub derived entirely from its facts has nothing a plan could show that render(facts) does not'' (C2-H). The plan/apply split earns its keep when the destination or the existing content is what a caller wants to inspect; when neither is in question, a plan is ceremony. An ADR should carry that boundary.'
- ref: sources/2026-08-design-spec-archiving-as-a-symmetric-prefix-move
  source: ingest
  rationale: Sixth instance, and the first anywhere in the workspace where the flag is ruled out for correctness rather than ergonomics or expressiveness. work_tracker_okf.archive's apply_archive computes ArchiveResult.indexes from a bundle reloaded after the move actually happened; a dry_run=True result would have no honest way to report that field without either fabricating it or omitting it. This sharpens every prior origin's argument from 'a boolean is less expressive' to 'a boolean here would have to lie'.
  evidence:
  - 'Second tier-3 instance (after child 2''s FilingPlan): ArchivePlan/apply_archive follows the same plan-then-apply split with no dry_run parameter (archive.py, C4-E).'
  - 'The stated reason is structural, not stylistic: ''A dry_run=True here would also have to lie: ArchiveResult.indexes cannot be computed without the move having happened'' (archive.py module docstring; design spec §4.1).'
  - 'The dishonesty is concrete: ArchiveResult.indexes is populated only after moves.apply runs and the bundle is reloaded through IGNORE — there is no bundle state at which a preview could compute it truthfully.'
  - 'Continues the tier-3 precedent set by C2-E (work_tracker_okf.advance/filing): this package again reaches the convention citing its own prior child rather than inheriting it fresh from okf-ext, reinforcing that the convention now spans two tiers by independent derivation, not just by copying.'
---
<!-- Body regenerated from origins[] while status: proposed. Do not edit here;
     approve via `gw wiki proposal approve adr-tier-2-writers-return-plans-not-dry-run-flags`. -->

## Suggested Action

Update existing adr page `adrs/tier-2-writers-return-plans-not-dry-run-flags.md`.

## Evidence From Source

- okf-io: all three writers default to dry_run=True, and IndexUpdate/LogAppend/Migration share one vocabulary (before, after, a changed property that renders nothing, a diff() that writes nothing) -- repo CLAUDE.md, 'The writers'.
- okf_ext.sections §6.2 states the divergence and chooses against the core deliberately: 'No dry_run flag. The plan IS the dry run -- the choice tables already made... Consistency inside tier 2 beats consistency with okf-io's three writers here, and tables is the nearer precedent.'
- The stated reason is expressive, not stylistic: 'apply 38 of these 40' is a thing a boolean cannot express (RenamePlan's original argument, repeated verbatim by SplicePlan and SectionPlan).
- A second consequence rides along: idempotence becomes an observable value rather than a return code -- SectionPlan.is_empty is the documented 'nothing to do' signal (sections/model.py:120-137).
- A third: staleness needs a digest carried on the plan, so every tier-2 plan ships a body digest and a WriteFailure(kind='stale') that a dry_run boolean would have no place to put (sections/model.py:98-117, sections/scaffold.py:368-376).
- Fourth instance: tables, moves, sections and now generators all return a plan value with no dry_run flag, against okf-io's three writers which all default dry_run=True. 'Idempotence surfaces as an empty plan' is restated verbatim in RegenerationPlan's docstring (okf_ext/generators/model.py:120).
- The first plan to carry edits to both document halves: Regeneration carries key_edits AND section_edits plus the whole new body in 'after', so apply is a pure digest-check-then-write with no chance of the plan and the apply disagreeing about what an edit means (okf_ext/generators/model.py:92).
- And the first to digest asymmetrically, deliberately: a whole-body digest with WriteFailure(kind='stale') for the body, and NO staleness check at all on frontmatter, because 'a key name does not move' and refusing 'would be the capability second-guessing its own contract' (design spec §6). A dry_run boolean has no place to express a per-half staleness policy.
- The consequence is a documented data-loss window rather than a check: a human edit to an owned key made between plan and apply is lost without a report, and the stated remedy is the declaration -- 'a key humans edit does not belong in owned:'. That is a contract a preview flag cannot carry and a plan value can.
- generators is also the second tier-2 capability to need nothing added to either shared union (SkipReason, FailureKind) -- packages/okf-ext/src/okf_ext/writing.py does not appear in ad1ad04's diff at all -- which is evidence the plan/apply vocabulary has stabilised across four capabilities.
- First tier-3 instance: work_tracker_okf.filing ships FilingPlan (frozen, with before/after-style 'changed' property and a diff() that writes nothing) plus a separate apply(plan) -> Path, and the spec states 'dry_run is not a parameter: not calling apply IS the dry run, which is the same split okf_ext.moves and okf_ext.tags make and for the same reason' (design spec §3.2).
- The tier-3 package reached the convention by an independent route: it cites its OWN prior child ('this package wrote the convention down two children ago -- advance() returns an AdvancePlan and mutates nothing') rather than inheriting it from okf-ext, which is evidence of a workspace-wide norm rather than one package's habit.
- Refusals-as-data rides along again, one tier up: FilingRefusal is a closed Literal vocabulary ('page-exists', 'directory-exists', 'unknown-type') where work-io raised FileExistsError, and there is deliberately no force= flag -- the same 'a fact about the target, not an exception' move the tier-2 plans make with SkipReason/FailureKind.
- The same child also documents the convention's LIMIT, which no prior origin did: results.write_results is a deliberate direct write with no plan, because 'a stub derived entirely from its facts has nothing a plan could show that render(facts) does not' (C2-H). The plan/apply split earns its keep when the destination or the existing content is what a caller wants to inspect; when neither is in question, a plan is ceremony. An ADR should carry that boundary.
- Second tier-3 instance (after child 2's FilingPlan): ArchivePlan/apply_archive follows the same plan-then-apply split with no dry_run parameter (archive.py, C4-E).
- The stated reason is structural, not stylistic: 'A dry_run=True here would also have to lie: ArchiveResult.indexes cannot be computed without the move having happened' (archive.py module docstring; design spec §4.1).
- The dishonesty is concrete: ArchiveResult.indexes is populated only after moves.apply runs and the bundle is reloaded through IGNORE — there is no bundle state at which a preview could compute it truthfully.
- Continues the tier-3 precedent set by C2-E (work_tracker_okf.advance/filing): this package again reaches the convention citing its own prior child rather than inheriting it fresh from okf-ext, reinforcing that the convention now spans two tiers by independent derivation, not just by copying.

## Existing Pages Considered

- No existing pages were cited by the proposal reasoner.

## Reasoning Summary

No reasoning summary was captured.

## Potential Conflicts

- No conflicts identified.

## Implementation Notes

- No implementation notes captured.

## Origins

**ingest · [[sources/2026-08-design-spec-okf-ext-sections-capability]]**
Three okf-ext capabilities have now made the same choice against okf-io's three writers, each restating the reason in its own docstring, and none of them recorded it as a decision. okf-io's update_index/append_log_entry/migrate all default dry_run=True; okf_ext.tables, okf_ext.moves and okf_ext.sections all ship a plan value with no flag. That is a workspace convention being established by repetition, and the fourth author will either copy it without knowing why or reopen it. It also has a real consequence -- the two halves of the workspace now disagree about what 'preview a write' means.

**ingest · [[sources/2026-08-design-spec-okf-ext-generators-capability]]**
Fourth instance, and the first that stresses the convention rather than merely repeating it. okf_ext.generators ships a RegenerationPlan carrying edits to BOTH halves of a document -- frontmatter keys and body sections -- while deliberately digesting only one of them, which is a shape a dry_run boolean has nowhere to put and which the three earlier instances never had to reason about. The convention is now unanimous across every tier-2 writer and still unrecorded anywhere but four module docstrings.

**ingest · [[sources/2026-08-per-item-layout-filing-conformant-vault-design-spec]]**
Fifth instance, and the first from OUTSIDE tier 2 — which breaks the proposal's own title. work-tracker-okf is a tier-3 package that depends on okf-io directly and could have copied its dry_run=True writers; C2-E instead ships FilingPlan + apply() with no flag, citing the package's own two-children-old convention (advance() returns an AdvancePlan and mutates nothing) and naming okf_ext.moves and okf_ext.tags as the precedent. The convention is therefore not 'tier 2 diverges from the core' but 'everything above the core diverges from the core', which makes okf-io's three writers the exception rather than the rule — a materially different ADR than the one four tier-2 origins were arguing for.

**ingest · [[sources/2026-08-design-spec-archiving-as-a-symmetric-prefix-move]]**
Sixth instance, and the first anywhere in the workspace where the flag is ruled out for correctness rather than ergonomics or expressiveness. work_tracker_okf.archive's apply_archive computes ArchiveResult.indexes from a bundle reloaded after the move actually happened; a dry_run=True result would have no honest way to report that field without either fabricating it or omitting it. This sharpens every prior origin's argument from 'a boolean is less expressive' to 'a boolean here would have to lie'.
