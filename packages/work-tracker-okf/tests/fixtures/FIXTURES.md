# Fixture vaults

Two vaults, authored here (nothing is vendored). Each file is a named
regression: change the assertion *with* the file, never the file alone.

## `minimal/`

The smallest vault carrying every projection shape the item view has to
survive: eight items across both lanes, one working directory with a
non-markdown member (`03-plan-transcript.txt`), one deliberately broken page
(`broken-eta.md`), and a `notes/` directory outside the lane. It carries no
`schema/` or `sections/`; tests that need them install them into a copy.

## `conformant/`

The vault child 6's acceptance gate is built on: it validates with **zero
errors** under both house rules raised to `severity="error"`. Six active items,
one archived item in the symmetric shape, one working directory holding three
artifacts including a `.jsonl`, and both lane index files.

### Recorded edits

- **`work/index.md` gained a `# Subdirectories` section listing `_archive`**
  (child 4, archiving). The vault was authored before anything reconciled it,
  and `okf_io.update_index` wants that entry whenever `_archive/` carries
  content — which it does, and always did. Adding it makes the fixture's steady
  state its reconciled state, so an archive assertion is about the archive
  rather than about one unrelated addition. Pinned by
  `test_archive.py::test_the_conformant_vault_is_already_index_reconciled`.
- **`work/release-path-native-cutover/children/epic-conformant-vault/children/feature-epic-feature-filing-writer.md` gained an `owner:`**
  (child 5, the lane rules). The vault was authored before anything checked the
  work-lifecycle axis, and `state.in-progress-without-owner` is an **error** —
  so the zero-errors property child 6's gate is built on did not hold the moment
  the lane rules were added. The item genuinely should name an owner; the vault
  is not wrong so much as older than the rule. Pinned by
  `test_conformant_vault.py::test_the_vault_validates_with_zero_errors_with_the_lane_rules`.
- **`work/release-path-native-cutover/children/epic-conformant-vault/children/feature-epic-feature-filing-writer/children/test-gap-cover-the-upsert.md`'s `phase` moved from `plan`
  to `execute`** (child 5, the lane rules). `work_status: accepted` paired
  with `phase: plan` is a pairing `workflow.route()` never produces — every
  transition that lands on `accepted` sets `phase` to one of `execute`,
  `finish` or `done` in the same step — so the pairing could only have arisen
  from how the fixture was hand-authored, and `state.phase-status-incoherent`
  (design spec §7.3's item-by-item check missed this one) correctly flags it as
  a `warn`. Fixing the fixture rather than accepting the warn keeps "exactly
  the three warns §7.3 predicts" a meaningful property instead of one padded
  out with a stale item. Pinned by
  `test_conformant_vault.py::test_the_lane_warns_on_the_conformant_vault_are_exactly_the_three_expected`.

### Known cosmetic divergence

`okf_io.update_index` renders a **new** bullet with a ` - ` separator where
this fixture authors ` — `. It is okf-io's rendering, not this lane's, and
chasing it would mean this package taking an opinion on another package's
output. Assertions on generated entries expect ` - `; the six authored bullets
keep their em dash because `descriptions="preserve"` never rewrites them.

## `nonconformant/` and `nonconformant_repo/`

The whole-catalog vault: one walk triggers **every one of the lane's 31 codes**,
and its reviewed output is `nonconformant.golden.txt`. Mirrors okf-io's
`nonconformant/` fixture exactly, including the part that does the actual work —
a **hand-written code set in the test**. Regenerating the golden alone proves
nothing; the hand-written set in `test_lane_catalog.py` and the conformant
vault's zero-errors property are what hold it honest.

`nonconformant_repo/` is the synthetic repository `repo_root` points at, and it
is a **sibling of the vault, never inside it** (C5-J). `load_bundle` walks
everything under the root, so an in-vault `_repo/` would need its own `ignore=`
pattern — and the recipe that would have to grow is `IGNORE` itself, the exact
contract these tests exist to exercise. A sibling directory is hermetic, keeps
the golden stable against real repo moves, and leaves `IGNORE` untouched.

### Known baseline noise

The conformant vault emits four `provenance.source-uncited` warns from okf-io.
Every `sources[]` entry in this lane is uncited by a body footnote, by
construction: an item page's sources are its artifacts rather than its
citations. Recorded so a later reader does not mistake it for something the lane
rules introduced.
