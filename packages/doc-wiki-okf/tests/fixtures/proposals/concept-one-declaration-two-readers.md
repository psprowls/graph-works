---
kind: concept
mode: create_new
target_slug: one-declaration-two-readers
title: 'One declaration, two readers: reader, validator and writer key off a single source of truth'
status: proposed
origins:
- ref: sources/2026-08-design-spec-okf-ext-sections-capability
  source: ingest
  rationale: 'The workspace has now instantiated the same pattern three times from the same premise, each time arguing it from scratch: a rule and a writer keyed off separate sources of truth will eventually disagree about what counts, so both must read one declaration. okf_io.migrate did it with doc.fm.fallbacks, okf_ext.sections does it with SectionSpec, and the filed generators capability will do it again with the same declaration. It is a named pattern in three module docstrings and no page.'
  evidence:
  - okf_io.migrate's trigger is membership in doc.fm.fallbacks -- the same set _rules/legacy.py keys off -- so reader, validator and writer can never disagree about what counts as v0.1; it never re-scans a document independently (repo CLAUDE.md, 'The writers').
  - 'okf_ext.sections cites that move explicitly as its own justification: SectionSpec/TypeSections are what section_rule measures a document against AND what render_skeleton and plan_sections write from (sections/__init__.py:8-13, sections/model.py:6-10).'
  - Both sections writers compose the same private _block, so a document created from a skeleton and a document repaired into one are byte-identical in the lines the capability writes -- which is what keeps the rule's placeholder-equality check meaningful across both paths (sections/scaffold.py:3-12).
  - 'Exactly one exception exists and is documented rather than hidden: _block''s closing blank sentinel is dropped when an append lands at the true end of a body with no trailing newline (sections/scaffold.py:196-202).'
  - 'The pattern is the stated substrate for the next capability: work/_archive/feature-okf-ext-generators-capability adds an ownership field to SectionSpec, which is additive precisely because there is one declaration for it to be additive to.'
- ref: sources/2026-08-design-spec-okf-ext-generators-capability
  source: ingest
  rationale: 'The instance this proposal predicted has landed, and it arrived with a twist worth recording before the page is written. okf_ext.generators is the third reader of the sections declaration, but making it one required HOISTING the declaration out of the capability that owned it into a new shared module -- because the independence contract forbids a capability importing a sibling. So the pattern''s guarantee stopped being conventional and became mechanical: there is now exactly one definition of SectionSpec that no contract permits a second copy of. The proposal currently argues the pattern from intent; this origin supplies the enforcement half.'
  evidence:
  - 'The predicted instance landed at ad1ad04: SectionSpec gained an ownership field and TypeSections gained a frontmatter block, both strictly additive, exactly as the sections spec §10 anticipated -- ''which it is not if a declaration written against a newer field refuses to load in an older reader'', which is why the loader''s unknown-key tolerance was written before the field existed.'
  - 'Making it a third reader required a hoist, not an import: the independence contract forbids a capability importing a sibling, so the declaration moved to okf_ext.shape. The rejected alternative names the pattern''s failure mode precisely -- two copies ''is how two capabilities end up promising two different things by the same word'' (design spec §3; okf_ext/shape/model.py:1-11).'
  - 'A Protocol was weighed and rejected on exactly this ground: it ''would satisfy the linter while leaving the coupling real -- an ownership field validated by sections'' loader that nothing in sections ever reads, bound to behaviour in generators by a contract no tool can see.'' The pattern''s value is the guarantee, and a mechanism that hides the coupling does not deliver it.'
  - 'Three readers now: sections'' section_rule measures documents against the declaration, sections'' render_skeleton/plan_sections write from it, and generators'' plan_regenerate derives what may be written from it -- all from one TypeSections, with _granted() (okf_ext/generators/plan.py:31) deriving both the writable key set and the writable heading set from the same object.'
  - 'One rule in the declaration exists only because the two readers meet: ''template implies seeded_is_complete, and the loader sets it'' -- without it every required template section would report sections.unfilled forever for being in exactly the state it is supposed to be in. Stated in the spec as ''a consequence of the two rules meeting rather than a coupling either side chose'' (design spec §4.1).'
---
<!-- Body regenerated from origins[] while status: proposed. Do not edit here;
     approve via `gw wiki proposal approve concept-one-declaration-two-readers`. -->

## Suggested Action

Create new concept page `concepts/one-declaration-two-readers.md`.

## Evidence From Source

- okf_io.migrate's trigger is membership in doc.fm.fallbacks -- the same set _rules/legacy.py keys off -- so reader, validator and writer can never disagree about what counts as v0.1; it never re-scans a document independently (repo CLAUDE.md, 'The writers').
- okf_ext.sections cites that move explicitly as its own justification: SectionSpec/TypeSections are what section_rule measures a document against AND what render_skeleton and plan_sections write from (sections/__init__.py:8-13, sections/model.py:6-10).
- Both sections writers compose the same private _block, so a document created from a skeleton and a document repaired into one are byte-identical in the lines the capability writes -- which is what keeps the rule's placeholder-equality check meaningful across both paths (sections/scaffold.py:3-12).
- Exactly one exception exists and is documented rather than hidden: _block's closing blank sentinel is dropped when an append lands at the true end of a body with no trailing newline (sections/scaffold.py:196-202).
- The pattern is the stated substrate for the next capability: work/_archive/feature-okf-ext-generators-capability adds an ownership field to SectionSpec, which is additive precisely because there is one declaration for it to be additive to.
- The predicted instance landed at ad1ad04: SectionSpec gained an ownership field and TypeSections gained a frontmatter block, both strictly additive, exactly as the sections spec §10 anticipated -- 'which it is not if a declaration written against a newer field refuses to load in an older reader', which is why the loader's unknown-key tolerance was written before the field existed.
- Making it a third reader required a hoist, not an import: the independence contract forbids a capability importing a sibling, so the declaration moved to okf_ext.shape. The rejected alternative names the pattern's failure mode precisely -- two copies 'is how two capabilities end up promising two different things by the same word' (design spec §3; okf_ext/shape/model.py:1-11).
- A Protocol was weighed and rejected on exactly this ground: it 'would satisfy the linter while leaving the coupling real -- an ownership field validated by sections' loader that nothing in sections ever reads, bound to behaviour in generators by a contract no tool can see.' The pattern's value is the guarantee, and a mechanism that hides the coupling does not deliver it.
- Three readers now: sections' section_rule measures documents against the declaration, sections' render_skeleton/plan_sections write from it, and generators' plan_regenerate derives what may be written from it -- all from one TypeSections, with _granted() (okf_ext/generators/plan.py:31) deriving both the writable key set and the writable heading set from the same object.
- One rule in the declaration exists only because the two readers meet: 'template implies seeded_is_complete, and the loader sets it' -- without it every required template section would report sections.unfilled forever for being in exactly the state it is supposed to be in. Stated in the spec as 'a consequence of the two rules meeting rather than a coupling either side chose' (design spec §4.1).

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
The workspace has now instantiated the same pattern three times from the same premise, each time arguing it from scratch: a rule and a writer keyed off separate sources of truth will eventually disagree about what counts, so both must read one declaration. okf_io.migrate did it with doc.fm.fallbacks, okf_ext.sections does it with SectionSpec, and the filed generators capability will do it again with the same declaration. It is a named pattern in three module docstrings and no page.

**ingest · [[sources/2026-08-design-spec-okf-ext-generators-capability]]**
The instance this proposal predicted has landed, and it arrived with a twist worth recording before the page is written. okf_ext.generators is the third reader of the sections declaration, but making it one required HOISTING the declaration out of the capability that owned it into a new shared module -- because the independence contract forbids a capability importing a sibling. So the pattern's guarantee stopped being conventional and became mechanical: there is now exactly one definition of SectionSpec that no contract permits a second copy of. The proposal currently argues the pattern from intent; this origin supplies the enforcement half.
