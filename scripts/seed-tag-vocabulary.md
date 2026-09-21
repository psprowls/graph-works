# seed-tag-vocabulary — fill `.gw/tags.yaml` from the vault's own inventory

`plan_scaffold` writes `.gw/tags.yaml` empty. This fills it, once, from an
evidence pack built against the migrated vault — and then applies the
`replaced_by` instructions the file itself declares, so file and corpus agree
from the moment the file exists.

It builds **no mechanism**. Every call is a shipped `okf_ext.tags` function.

## Use

```bash
WS=/Users/pat/Personal/workspaces/graph-works

# 1. Build the pack. Reads only.
uv run python scripts/seed_tag_vocabulary.py "$WS" evidence --as-of 2026-09-01 \
  --out "$WS/wiki/work/epic-cutover-live-migration/children/feature-epic-feature-seed-tag-vocabulary-policy/references/03-execute-evidence.md"

# 2. --- the decision session. No machinery, deliberately. ---
#    Then hand-author "$WS/.gw/tags.yaml".

# 3. Prove the file before believing it.
uv run python scripts/seed_tag_vocabulary.py "$WS" gate

# 4. Apply the file's own deprecations. Dry run first, always.
uv run python scripts/seed_tag_vocabulary.py "$WS" rename
uv run python scripts/seed_tag_vocabulary.py "$WS" rename --write

# 5. Record the acceptance numbers.
uv run python scripts/seed_tag_vocabulary.py "$WS" verify --today 2026-09-01
```

Run through `uv run` from the repo root — the script imports `okf_io`,
`okf_ext` and `work_tracker_okf`, so invoking it directly gets
`ModuleNotFoundError`.

Exit code is `0` on success, `1` on a gate/verify failure or a write failure,
`2` on a usage error.

## Options

| Flag | Where | Effect |
|---|---|---|
| `WORKSPACE` | all | The workspace root — the directory holding `wiki/` and `.gw/`. Not the bundle. |
| `--ignore GLOB` | all | Override the manifest's `ignore:` set. Repeatable. Without it the set is read from `workspace.yaml`'s top-level `ignore:`. |
| `--as-of` | `evidence` | Required. Stamped into the pack. No clock is read, which is also what makes the pack byte-reproducible. |
| `--out` | `evidence` | Write here instead of stdout. |
| `--write` | `rename` | Apply. Without it the plan is only reported. |
| `--today` | `verify` | Required. Passed to `validate`. |

## The decision session — three questions, in this order

Order matters: merges change the counts the floor is applied to, so merging
first and thresholding second is the only sequence that does not throw away
evidence.

**Q1 — the merges.** Walk the similarity list row by row. Each row is *merge*
(name the survivor — it becomes an `allowed` entry — and the loser, which
becomes a deprecated entry with `replaced_by` pointing at it) or *distinct*
(both stand on their own merits; Q2 judges each separately). No row is skipped
and no row is decided by score. The survivor is normally the higher-count
member, but not mechanically.

**Q2 — the floor.** With the merged counts in hand, read the recomputed
coverage curve and name a floor `n`. The floor is a **default for automatic
admission, not a veto**.

**Q3 — the exceptions, in both directions.** Two lists, each entry with a
one-line reason. *Standing*: below the floor, admitted anyway — a tag that is
young rather than unpopular. *Retired*: at or above the floor, deprecated or
dropped anyway — a tag that is common but wrong.

The `live` column is a strong prior on both, **not a rule**. A tag whose entire
corpus is archived work describes finished work; the default disposition is
*dropped*. But an archive-only tag can still name something the vault will meet
again, and that is a Q3 *standing* entry.

## The three dispositions

**`allowed`** — an entry with `deprecated: false` (or absent). A description is
required *in practice* even though the format tolerates its absence: an entry
with no description is a tag a human meets in a `tags.unknown` finding with
nothing to explain it. Descriptions are the vault's prose; nothing in the
toolchain ever rewrites them. The name is the exact spelling —
`load_vocabulary` compares exactly and never normalizes.

**deprecated with `replaced_by`** — the only disposition that changes the
corpus. The loader enforces all of:

- `deprecated` must be a real boolean. `"false"`, `0`, `no` all raise.
- `replaced_by` is meaningful **only** on a deprecated entry; on a live one it raises.
- `replaced_by` must name a tag that is `allowed`. **There are no deprecation
  chains** — `a -> b -> c` is rejected at load, so every merge must collapse
  straight onto its final survivor.
- An empty or whitespace `replaced_by` raises; "deprecated with no successor"
  is spelled by omitting the key.

**dropped** — no entry at all. Two consequences to understand rather than
assume away:

- **The tag stays on the page.** `okf_ext.tags` ships no way to strip a tag;
  every planner routes through a `Mapping[str, str]`. So *dropped* means "not
  endorsed", **never** "deleted".
- **Every carrier warns, forever** — `tags.unknown` at `severity="warn"` on each
  page. That is the intended signal, and it is deliberately harmless: every code
  in this rule set is `warn`, because `Report.ok` is a claim about OKF v0.2
  conformance and a house rule has no business making a conformant bundle look
  otherwise.

Prefer *dropped* over deprecated-without-replacement — same practical outcome,
one fewer file entry. Prefer deprecated-with-`replaced_by` over *dropped*
whenever a survivor exists: a `replaced_by` is a repair, a drop is a complaint.

## The ADR 2026-08-21-a-package-contributes seam — three rules

A package contributes `TagDefinition` entries; the vocabulary *file* is the
vault's. The two routes must never touch the same entry.

1. **Never author an entry named `perf` or `security`.** They arrive by the
   package route (`work_tracker_okf.vocabulary.CONTRIBUTED_TAGS`) on every
   install, including the cutover's.
2. **Never edit those entries' `deprecated` or `replaced_by`.** The merge
   classifier compares both fields and refuses the whole entry when they
   differ — permanently, on every subsequent install. A differing
   **description** is different: that is `TagDrift`, reported and never
   overwritten, so the vault may reword freely.
3. **Exclude them from the admission arithmetic.** They are admitted by
   contribution, not by frequency. A count of 0 is correct, not dead weight —
   it is how a human editing a Bug discovers the tag exists.

`gate` enforces all three.

## What this does not do

- It does not remove a tag from a page. No shipped planner does.
- It does not wire `vocabulary_rule` into the work lane. That is a real gap in
  `work_tracker_okf.compose.rule_set` — the vocabulary is enforced over
  `concepts/`, `adrs/`, `sources/` and `proposals/` only. `verify` reports the
  enforced and unenforced `tags.unknown` counts separately so the gap is visible
  rather than assumed away. It is filed against `work-tracker-okf`, not fixed here.
- It does not add a `gw tags` verb. Nothing enters the frozen CLI surface.
- It does not seed any workspace other than `graph-works`.
