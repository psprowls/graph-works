# packages/work-tracker-okf

Python ≥3.12 (the workspace floor). Tests are pytest.

## Layout

- `src/work_tracker_okf/` — library + CLI (`cli.py`, `__main__.py`). The
  decision layer is `hierarchy.py` → `workflow.py` → `advance.py` /
  `children.py` / `projection.py`, importing strictly downward and never the
  reverse.
- `src/work_tracker_okf/compose.py` — what a composing CLI does that no single
  writer owns: the shared rule set, the `sources[]` stamp, the `## Plan` row,
  the one-save advance, and filing's index/log reconcile. Library rather than
  CLI because tier 4 drives this lane through the Python API, so anything in a
  Typer callback is something tier 4 has to reimplement.
- `src/work_tracker_okf/_rules/` — the lane rule catalog: five topic modules
  (`state`, `plan`, `graph`, `targets`, `decisions`) plus `_common.py` and the
  registry. Exported as one `extra_rules=` bundle through `rules.py`'s
  `lane_rules()`.
- `src/work_tracker_okf/decisions.py` — the per-epic decisions ledger, in three
  layers: pure text (`parse`/`render`/`prose_block`), file (`load`/`append`/
  `set_fields`/`supersede`, each one read → mutate → render → write cycle under
  one exclusive `flock`), and pure query (`query`/`counts`). It imports
  `paths` and nothing else from the package.
- `src/work_tracker_okf/assets/` — package-data seed files copied byte-for-byte
  by `plan_install()`: seven `_schema/` documents and seven `_sections/`
  declarations
- `tests/` — pytest tests; `tests/fixtures/minimal/` is the fixture vault

## Conventions

- Never reads the clock: `today=` is an argument everywhere except `cli.py`,
  the one module allowed to call it.
- Never discovers paths: the bundle root is always explicit.
- Nothing on the content path raises. `InitError` is for caller error (a root
  that is not a directory) and nothing else; a malformed work item still
  projects to a `WorkItem` with its uncoercible fields at their empty value.
- Tier 3 per ADR-0005: depends on `okf-io` and `okf-ext[schemas]`. Nothing
  depends on this package.
- The module name is the vocabulary's home: `vocabulary.py` holds every closed
  set, and the base schema's `enum` arrays are reconciled against it by
  `tests/test_vocabulary.py` rather than generated from it.
- Writers plan, then apply. `advance()` returns an `AdvancePlan` and mutates
  nothing; `apply(document, plan)` writes it. `children.py` splits the same
  way, and `apply_children_sync` defaults to `dry_run=True`.
- Refusals are data. `AdvancePlan.refusal` is a closed `RefusalReason`
  vocabulary, not an exception — the one raise on this path
  (`Document.set` on a document that failed to parse) is unreachable through
  the public API, and `test_advance.py` pins that.
- Two `ignore=` recipes, deliberately. `IGNORE` is what every reader and
  validator uses; `ARCHIVE_IGNORE` (it minus `*/references/*`) is what the
  archive path plans moves through, because `okf_ext.moves` never reads
  `bundle.ignored`. `tests/test_ignore.py` asserts both literally *and*
  asserts their delta, because two constants drifting apart silently is the
  failure mode. `ARCHIVE_IGNORE` must never reach `validate()` or
  `update_index`.
- The decisions ledger lives at `work/<slug>/references/00-decisions.md` and the
  epic page does **not** point at it. Inside `references/`, so `IGNORE`'s
  existing `*/references/*` covers it with no new pattern and it rides along on
  archive for free; unstamped, so `SOURCE_ID_PATTERN` and `_base.schema.json`
  are untouched — there is no `decisions` id, and adding one is a schema change.
  `decisions.ledger-missing` is what replaces the `targets.artifact-missing`
  the missing stamp would otherwise have bought, and it asks the sharper
  question: not "is this stamped resource present" but "does an epic that has
  moved past design have a ledger at all."

## Three things about `compose.py`

- **`advance` writes the page once.** `ensure_plan_row` uses
  `okf_ext.tables.splice_text` + `Document.set_body`, never
  `tables.plan_row` / `tables.apply`. The bundle-level pair plans and writes
  against a `Bundle` independently, so pairing it with `advance.apply` would
  write the same page twice — two writes, two chances to half-apply, and
  okf-io's byte-fidelity splice running over a file that already moved
  underneath it.
- **The plan row's action cell names the root-absolute `resource`.** Its
  leading slash is load-bearing: `_rules/plan.py`'s `_PATH_RE` fullmatches one
  whitespace token, and a token starting with `/` cannot match, so
  `plan.action-target-missing` correctly skips a bundle path. Writing the
  bundle-relative form would make every advanced item report an error under
  `--repo-root`.
- **The log rule.** A command that changes *what the vault contains* appends one
  `log.md` line — `init`, `file`, `archive`. A command that changes an existing
  page's fields does not — `advance`, `sync-children`. One rule, so neither half
  needs remembering.

## The rule catalog

30 codes, 5 topics, 14 rule functions. **The module name is the code prefix**,
asserted mechanically in `test_lane_catalog.py` — the same move
`okf_io/tests/test_catalog.py` makes for its own eight. Adding a code means
adding it to its topic module's `CODES` *and* to the rule that emits it; a new
topic means a new module plus an entry in `_rules/__init__.py`'s two mappings.

The five prefixes had to clear eighteen taken names: okf-io's eight (which make
`validate()` **raise**), okf-ext's six — `health`, `placement`, `render`,
`schemas`, `sections`, `tags` — (which do not raise and would still be wrong,
because the conformant vault runs `schema_rule` and `section_rule` in the same
report), and, for `decisions`, this lane's own four. `lifecycle` is among
okf-io's eight, which is why the module that was literally called
`lifecycle_lint` could not keep its name.

An earlier revision of this file undercounted the taken-name total, crediting
okf-ext with one fewer module than it ships. The correct figure for that earlier
state was fourteen. Nothing collided either way; the count was simply wrong.

**The registry is a factory per topic, not a `RULES` tuple.** `RuleContext`
deliberately carries no filesystem, and two codes (`targets.affects-missing`,
`plan.action-target-missing`) are questions about a repository — so every topic
exports `rules(config: LaneConfig)` and `lane_rules(repo_root=…)` composes them.
`repo_root=None` **skips** those two rather than reporting them: not knowing
where the repo is says nothing about whether the paths are good. Every topic
exports the factory, including the two that inject nothing — a heterogeneous
registry would make the catalog-completeness test special-case half its own
subjects.

**No caching of the projection.** Each rule function calls
`load_items(ctx.bundle)` itself: measured at 93 µs for 7 items, about 30 ms
across all fourteen on a 100-item vault. Fourteen independent passes, no shared
state, and no cache whose invalidation nobody can see.

`Finding.spec` cites the **module that defines the rule** for every lane
invariant — an item whose `workflow_status` is `superseded` with no
`superseded_by` is a perfectly conformant OKF v0.2 document — and `§5.1` only
for the two `targets` codes about `sources[]`. Inventing a section number for a
lane invariant would be a false citation that outlives whoever wrote it.

`_PHASE_COMPAT` lives module-private in `_rules/state.py` and is **bonded to
`workflow.route()` by a test**, not by derivation: `test_phase_compat.py`
enumerates every `RouteState` over the closed sets and asserts the router never
turns a coherent `(workflow_status, phase)` pair into one the map condemns. That
bond is what makes the epic's argument for keeping the routing table at tier 3
worth anything.

### One thing okf-io does not do

Nothing in okf-io or okf-ext validates a `sources[].resource` **target**.
`links.build()` reads body prose only and never frontmatter, and
`provenance.source-resource-missing` asserts only that the `resource` key is
present. `targets.artifact-missing` exists because of that gap — a tier-2 gap,
not a defect here. If okf-io ever grows the check, retire this code rather than
leaving it to double-report.

## Testing

`uv run --package work-tracker-okf pytest packages/work-tracker-okf/tests -v`
from the repo root.

`tests/fixtures/nonconformant/` is the whole-catalog vault: one walk triggers
every one of the 30 lane codes, and `nonconformant.golden.txt` is its reviewed
output. Regenerating the golden alone proves nothing — what holds it honest is
the hand-written `ERROR_CODES` set in `test_lane_catalog.py` and the conformant
vault's zero-errors property. `tests/fixtures/nonconformant_repo/` is the
synthetic repo `repo_root` points at, and it is a **sibling** of the vault: an
in-vault one would need `IGNORE` to grow a pattern, which is the contract these
tests exist to exercise.

`uv run --package work-tracker-okf mypy --strict packages/work-tracker-okf/src`
is this package's type gate. The shared `just types` line may be red for
reasons that predate this package (a root venv missing third-party stubs like
`typer`, `tree_sitter` or `tiktoken`) — that is not yours to fix; only a
failure naming `work_tracker_okf` is.
