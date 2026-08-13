---
kind: adr
mode: create_new
target_slug: bulk-write-staging-protocol
title: Bulk multi-file writes stage to temp siblings and commit with rename
status: proposed
origins:
- ref: sources/2026-08-design-spec-scaffold-okf-ext-and-tag-management
  source: ingest
  rationale: Not anticipated by the spec — it emerged from implementing §9.3's one-paragraph serialize-then-write model and is a reusable posture for every future okf-ext capability that writes, not a tags detail. It also states two non-goals explicitly enough that they should not be re-litigated per capability.
  evidence:
  - 'Three failure regimes with different guarantees, documented in okf_ext.tags.rename.apply: content failures are refused per document (siblings still land); probe and staging failures are all-or-nothing (no live file touched); commit failures are isolated per document, which is the one place partial disk state is possible without a crash.'
  - 'The atomicity guarantee comes from staging, not the probe: every document is written to a `.<name>.<uuid>.tmp` sibling in the same directory before any live file changes, so disk-full/quota/permission failures land before commit. The open(''r+b'') probe is documented as only a probe — a directory-level failure or a permission change after it closes sails through.'
  - 'Commit is Path.replace (os.replace): a single filesystem rename, atomic and metadata-only, so it cannot fail for the content or space reasons a write can.'
  - 'Two declined non-goals, stated rather than left to be inferred: no journal (so an abrupt crash during the replace loop can still leave some documents moved), and no fsync on temp files or their directory (real, measurable cost on network filesystems, and it would not close the gap without the journal anyway).'
  - 'WriteFailure carries a machine-readable kind: FailureKind (nine members) alongside the rendered error, so a caller can distinguish a stale plan worth re-planning from an I/O failure worth retrying without substring-matching prose. Recorded in the README as cheap pre-1.0 and expensive once someone matches on error in production.'
---
<!-- Body regenerated from origins[] while status: proposed. Do not edit here;
     approve via `gw wiki proposal approve adr-bulk-write-staging-protocol`. -->

## Suggested Action

Create new adr page `adrs/bulk-write-staging-protocol.md`.

## Evidence From Source

- Three failure regimes with different guarantees, documented in okf_ext.tags.rename.apply: content failures are refused per document (siblings still land); probe and staging failures are all-or-nothing (no live file touched); commit failures are isolated per document, which is the one place partial disk state is possible without a crash.
- The atomicity guarantee comes from staging, not the probe: every document is written to a `.<name>.<uuid>.tmp` sibling in the same directory before any live file changes, so disk-full/quota/permission failures land before commit. The open('r+b') probe is documented as only a probe — a directory-level failure or a permission change after it closes sails through.
- Commit is Path.replace (os.replace): a single filesystem rename, atomic and metadata-only, so it cannot fail for the content or space reasons a write can.
- Two declined non-goals, stated rather than left to be inferred: no journal (so an abrupt crash during the replace loop can still leave some documents moved), and no fsync on temp files or their directory (real, measurable cost on network filesystems, and it would not close the gap without the journal anyway).
- WriteFailure carries a machine-readable kind: FailureKind (nine members) alongside the rendered error, so a caller can distinguish a stale plan worth re-planning from an I/O failure worth retrying without substring-matching prose. Recorded in the README as cheap pre-1.0 and expensive once someone matches on error in production.

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
Not anticipated by the spec — it emerged from implementing §9.3's one-paragraph serialize-then-write model and is a reusable posture for every future okf-ext capability that writes, not a tags detail. It also states two non-goals explicitly enough that they should not be re-litigated per capability.
