# Re-pointing `gw wiki proposals` — the key mapping

**Nothing in this repository re-points `gw wiki proposals`.** Those commands live in
`agent-research`, whose `graph_wiki_core.commands.proposals` imports `wiki_io.proposals` directly,
and `/graph-wiki:proposals` keeps working against the old ledger until the cutover epic
(`2026-08-11-epic-cutover-live-migration`) does the work. This file is what that epic needs: the
honest mapping, with the places there is no honest alias called out rather than papered over.

## Commands

| Old | New |
|---|---|
| `gw wiki proposals` | `doc-wiki-okf proposals ROOT [--page-status …] [--json]` |
| `gw wiki proposal approve <kind>-<slug>` | `doc-wiki-okf proposal approve ROOT <target-path>` |
| `gw wiki proposal reject <kind>-<slug>` | `doc-wiki-okf proposal reject ROOT <target-path>` |
| `python -m wiki_io.file_proposal --kind … --target-slug …` | `doc-wiki-okf proposal file ROOT --lane … --title …` |
| — | `doc-wiki-okf proposal show ROOT <target-path>` |
| — | `doc-wiki-okf proposal promote ROOT <target-path>` |
| — | `doc-wiki-okf init ROOT` |
| — | `doc-wiki-okf migrate ROOT [--apply]` |

**Addressing changed.** A proposal is named by its **target path**, because that is identity in OKF
— a member's id is its path. The `<kind>-<target_slug>` id has no successor.

**`migrate` is top-level, not `proposals migrate`.** `proposals` is already a leaf command that
lists the ledger, and typer cannot make one name both a command and a group — so `migrate` sits
alongside `init` instead of nesting under it. It also inverts this package's own `--dry-run`
convention: every other mutating command here writes by default and previews under `--dry-run`;
`migrate` previews by default and writes only under `--apply`, because a whole-bundle rewrite plus
a batch move should not happen because someone typed a path.

## `--json` keys

| Old key | New key | Note |
|---|---|---|
| `kind` | `lane` | `concept` has no successor; `adr` maps to lane `adr` |
| `target_slug` | `target` | a full bundle-relative path, not a slug |
| `status` | `page_status` | same four values: `proposed`, `approved`, `rejected`, `created` |
| `mode` | — | **no successor.** Derived from whether the target is a member, never stored |
| `tokens` | — | **no successor.** A document-level producer count with no home in `sources[]` |
| `origins` | `sources` | native OKF `sources[]`, not a capability dialect |
| `origins[].ref` | `sources[].resource` | dedup keys on this |
| `origins[].source` | — | **no successor.** A producer tag with no home in `sources[]` |
| `origins[].rationale` | `sources[].rationale` | rides through the merge verbatim |
| `origins[].evidence` | `sources[].evidence` | as do the other four review keys |
| `rank`, `confidence` | `sources[].rank`, `sources[].confidence` | carried, unread — see the epic spec's §7 |
| — | `member` | where the proposal file sits; placement, never identity |
| — | `malformed` | non-`null` for a proposal that could not be read cleanly |
| — | `verified` | the decision, as OKF §5.2's event rather than a `decided:` key |

The skill at `plugins/graph-wiki/commands/proposals.md` reads `kind`, `target_slug`, `status` and
`origins`. All four move; `mode` does not exist. That is the edit the re-pointing requires, and it
is the cutover epic's to make.

## Two consequences worth knowing before the cutover

**A re-fired source no longer updates in place.** `upsert_proposal` keyed `origins[]` by `ref` and
rewrote the matching entry; `_merge_sources` keys by `resource` and skips an entry whose resource is
already stored, so a corrected rationale for an already-cited source does not land. Repair it by
editing the proposal or by rejecting and re-proposing. This is the mechanism behind "idempotence
surfaces as an empty plan", which is the property the capability's tests assert.

**Two targets that slug alike still get distinct proposal files.** `okf_ext.proposals._placement`
suffixes `-2`, `-3` because placement is cosmetic and identity is the target. The old ledger could
not express this — its filename *was* its identity.
