# repositories-okf

Band 2. The repositories lane over OKF v0.2: it declares `ManagedRepository` and `ReferenceRepository`, installs their schemas and sections into a bundle, contributes two tags to its vocabulary, and names the clone glob every graph-works bundle load hides. It depends on `okf-io` and `okf-ext[schemas]` only, and performs no clock read (both are held by `tests/test_repositories_okf_boundaries.py`; a `TYPE_CHECKING`-only import is not a runtime import).

## Commands

From the repo root:

```bash
uv run --package repositories-okf mypy --strict --platform linux packages/repositories-okf/src
uv run --package repositories-okf mypy --strict --platform win32 packages/repositories-okf/src
uv run --package repositories-okf pytest packages/repositories-okf/tests
uv run --package repositories-okf pytest packages/repositories-okf/tests --cov=repositories_okf --cov-branch --cov-report=term-missing --cov-fail-under=95
```

Or `just check-affected` (or `just check-pkg repositories-okf`) for lint, types on both arms and coverage together.

## Module map

- `lane.py`: lane constants (`LANE_DIR`, `TYPES`, `CLONE_GLOB`, `GITIGNORE_PATTERN`, `OBSIDIAN_FILTER`, `IGNORE`) and `placement_directories`.
- `vocabulary.py`: `CONTRIBUTED_TAGS`, the tags this lane adds to `tags.yaml`.
- `resources.py`: `seed_files()` and `SEED_RELATIVE_PATHS`, the nine owned templates read from `assets/`.
- `init.py`: `install_bundle` / `plan_install` / `BundleInstall` / `InitError`, the four-act installer (scaffold, install, vocabulary merge, log) that satisfies core's `Installer` protocol.
- `assets/`: the owned templates, `schema/_base-repository.schema.json`, `schema/ManagedRepository.schema.json`, `schema/ReferenceRepository.schema.json` and one `sections/<Type>.yaml` per type.

## Frontmatter contract

`_base-repository.schema.json` requires `type`, `title`, `description` and `url`. Optional keys: `track`, `status` (`draft`, `stable`, `deprecated`), `updated`, `tags`, `sources`, `generated` (`by`, `at`) and `pin`. `pin` requires `commit` (40 hex) and `fetched_at`; its optional sub-keys are `ref`, `describe`, `tree`, `commit_date` and `previous`, plus `generation` (`gw_version`, `scan_config_hash`). The owned templates are byte-compared on install, so widening one is a template change: re-bootstrap of an existing workspace refuses that file until it is refreshed.

## OKF conformance

- OKF v0.2 conformance (SPEC §11 item 1) is claimed for the committed bundle only.
- A materialized clone under `repositories/<name>/references/git/` usually holds `.md` files without frontmatter, so a working tree with a clone present is not strictly conformant.
- The lane ignore (`CLONE_GLOB`) and directory pruning (`PRUNE_GLOB`) is a graph-works consumer convention, not a spec feature; every graph-works bundle load appends it through `graph_works_core.workspace.bundle`.
- No bundle export or copy tooling exists at the baseline; any future exporter must exclude `repositories/*/references/git/`.

## Obsidian

Bootstrap adds `repositories/*/references/git/` to `<bundle_dir>/.obsidian/app.json`'s `userIgnoreFilters`. Obsidian's exclusion hides files from search, graph and the switcher but still indexes them.

## Lifecycle modules and boundaries

- `git.py`: the git runner, handed its executable and environment; returns `GitFailure` values and disables terminal prompts. Object and range reads set `GIT_NO_LAZY_FETCH=1`; it also owns worktree add/list/remove, `branch_tip`, `current_branch`, `rev_count`, `toplevel`, `is_linked_worktree`, `is_pristine`, `set_config`, `attach`, and `worktree_repair`.
- `repository.py`: the `repository.*` rules over `RepositoryFacts`, gathered by `gather`.
- `pin.py`: source and lineage facts, SHA validation and byte-faithful pin read/write; `Pin.generation` is a managed repository's regeneration stamp.
- `pages.py`: new page rendering through okf-io.
- `snapshots.py`: snapshot paths, range summaries and `RepositorySnapshot` rendering.
- `changelog.py`: `RepositoryChangelog` reconciliation; preserves human entry text and line endings.
- `flagging.py`: changed clone links, directory links and one proposal per affected bundle page.
- `lifecycle.py`: naming, repository page paths/rendering and pure restore decisions.
- `subprocess` is permitted only in `git.py`, named by `test_repositories_okf_boundaries.py::PROCESS_MODULES`. Callers supply timestamps; no module reads the clock.
- Exact rename diffs use `--find-renames=100%`; `-M` can lazily fetch missing blobs in a partial clone.
