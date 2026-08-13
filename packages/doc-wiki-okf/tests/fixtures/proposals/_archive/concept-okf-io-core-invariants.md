---
kind: concept
mode: create_new
target_slug: okf-io-core-invariants
title: okf-io core invariants
status: created
origins:
- ref: sources/2026-08-design-spec-build-okf-io-core-through-validation-and-writers
  source: ingest
  rationale: The epic spec's §7 states six cross-cutting requirements that every okf-io module and every future package inherits, each now with a shipped, checkable enforcement mechanism. They are currently scattered across five ADRs and two concept pages with no page stating the set.
  evidence:
  - 'Purity: okf-io implements only what SPEC.md defines; the two-dependency limit (ruamel.yaml + markdown-it-py) is the enforcement mechanism, and it held when the footnote join wanted mdit-py-plugins.'
  - 'Tolerance (spec §11): never reject for unknown type, unknown key, broken link, or missing optional family; made structural via Document.parse never raising and the loader recording unreadable members.'
  - 'Round-trip fidelity: every write path preserves comments, quote style, flow-vs-block style and key order; inherited by index.py and log.py, which were written after the rule was stated.'
  - 'Injectable time: shipped stronger than specified — today is keyword-only with no default on derive and on validate(), so no code path can reach the system clock.'
  - 'Derived data stays derived: backlinks, trust tiers and staleness are computed on read and never written into frontmatter.'
  - 'Coverage: >=95% branch enforced by just check --cov-fail-under=95; deferred by children 1 and 2, landed in child 3 at 95.53%.'
tokens: 788
---
<!-- Body regenerated from origins[] while status: proposed. Do not edit here;
     approve via `gw wiki proposal approve concept-okf-io-core-invariants`. -->

## Suggested Action

Create new concept page `concepts/okf-io-core-invariants.md`.

## Evidence From Source

- Purity: okf-io implements only what SPEC.md defines; the two-dependency limit (ruamel.yaml + markdown-it-py) is the enforcement mechanism, and it held when the footnote join wanted mdit-py-plugins.
- Tolerance (spec §11): never reject for unknown type, unknown key, broken link, or missing optional family; made structural via Document.parse never raising and the loader recording unreadable members.
- Round-trip fidelity: every write path preserves comments, quote style, flow-vs-block style and key order; inherited by index.py and log.py, which were written after the rule was stated.
- Injectable time: shipped stronger than specified — today is keyword-only with no default on derive and on validate(), so no code path can reach the system clock.
- Derived data stays derived: backlinks, trust tiers and staleness are computed on read and never written into frontmatter.
- Coverage: >=95% branch enforced by just check --cov-fail-under=95; deferred by children 1 and 2, landed in child 3 at 95.53%.

## Existing Pages Considered

- No existing pages were cited by the proposal reasoner.

## Reasoning Summary

No reasoning summary was captured.

## Potential Conflicts

- No conflicts identified.

## Implementation Notes

- No implementation notes captured.

## Origins

**ingest · [[sources/2026-08-design-spec-build-okf-io-core-through-validation-and-writers]]**
The epic spec's §7 states six cross-cutting requirements that every okf-io module and every future package inherits, each now with a shipped, checkable enforcement mechanism. They are currently scattered across five ADRs and two concept pages with no page stating the set.
