# AGENTS.md — gw-kernel

Band 3 application slice. Permitted dependencies: declared foundation and OKF packages.
Forbidden edges: every application slice and interface.
See the [root band table](../../AGENTS.md) and the root `pyproject.toml` contracts
"The application band is a tree of five slices beneath the interfaces" and
"Pipeline consumers never import the wiki slice".

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this package is

Band 3: "the one package that knows what a workspace is." A workspace is a
directory on disk (default `<repo>/.works`) holding a manifest
(`workspace.yaml`), an OKF bundle, and a control-plane directory. Every
package below this one (`okf-io`, `okf-ext`, `code-wiki-okf`,
`doc-wiki-okf`, `code-graph-io`, `work-tracker-okf`, `config-io`,
`models-io`, `subagents-io`) receives resolved paths as plain arguments —
none of them has a `graph_dir(workspace)`-shaped function, and none of them
imports `gw_kernel`. An import-linter `layers` contract in the root
`pyproject.toml` enforces the direction; `tests/test_kernel_boundaries.py`
covers the half import-linter's `grimp` cannot see (a module inside this
package importing its own top-level `gw_kernel`, which would let one
vertical pull in every other vertical through the front door).

Shared pipeline services now live in `gw-pipeline`: transactions, finish,
execute_return, landed, gate_config, decision_owner, repo_context, members,
guidance assembly/render and transcript_capture.
Kernel retains bundle_lock/pending commits, anchors, commits, provenance, discovery,
hooks/hatch build, pipeline definitions, dispatch configuration and work schemas.
Kernel source, tests and dependency groups must never import an application slice;
`tests/test_no_orchestrate_imports.py` also guards pipeline/work/wiki names.
The existing internal kernel layers below remain in force.

## Commands specific to this package

```bash
uv run --package gw-kernel mypy --strict --platform linux packages/gw-kernel/src
uv run --package gw-kernel mypy --strict --platform win32 packages/gw-kernel/src
uv run --package gw-kernel pytest packages/gw-kernel/tests
uv run --package gw-kernel pytest packages/gw-kernel/tests \
  --cov=gw_kernel --cov-branch --cov-report=term-missing --cov-fail-under=95
```

These are exactly the root `justfile`'s `types`/`test`/`cov` recipe lines for
this package — not a generic `uv run pytest`. `types` runs twice, once per
`--platform` arm, so a POSIX host still sees a Windows-only `mypy --strict`
failure and vice versa. Run `just sync` first if
`mypy --strict` reports the tier-3 `typer`-decorated commands (from
`code-wiki-okf`/`work-tracker-okf`) as untyped; a bare `uv run` only installs
the *root* workspace's own dependencies, not member packages'.

Subset examples:

```bash
uv run --package gw-kernel pytest packages/gw-kernel/tests/workspace
uv run --package gw-kernel pytest packages/gw-kernel/tests/workspace/test_init.py
uv run --package gw-kernel pytest -k "discovery or manifest"
```

### `hatch_build.py`

A custom wheel build hook, worth knowing about before touching packaging.
The sdist stages `plugins/gw/hooks/examples` inside
`src/gw_kernel/_hook_scripts` (see `pyproject.toml`'s
`sdist.force-include`). Building a wheel *directly from the source tree*
(not from an sdist) would otherwise ship without those scripts, since they
live outside `src/`. The hook checks whether `_hook_scripts` is already
present (sdist path — nothing to do) and, if not, force-includes the
canonical `plugins/gw/hooks/examples` directory into
`gw_kernel/_hook_scripts` for the wheel, raising `FileNotFoundError`
if that canonical directory is missing. If you move or rename
`plugins/gw/hooks/examples`, update the hardcoded relative path
here (`Path(self.root).parents[1] / "plugins" / "gw" / "hooks" / "examples"`)
and `CANONICAL_SCRIPTS_DIR` in `hooks.py`.

The command `gw config hooks enable` writes binds the interpreter that ran
it as the first argv token (no shell, no `VAR=value` prefix) and renders it
with `subprocess.list2cmdline` plus forced quoting of `& | < > ^ ( )` tokens
on win32 (a token containing `%` raises `HooksError`: cmd expands it even
inside quotes) or `shlex`-equivalent quoting elsewhere
(`gw_kernel.hooks._quote_command`) — never hand-roll a shell-syntax command
string here. The interpreter path is frozen at enable time, so re-running
enable compares each registered command to the freshly computed one and
rewrites a drifted one in place (`HooksResult.updated`); an identical one
stays in `skipped` and nothing is written.

## Architecture

### Module layout

`workspace/` owns resolved layout, manifest and lifecycle primitives.
`graph/` and `read_session/` form the shared substrate; `guidance/` owns
claims and affects closure; `events/` and `wiki_stats/` remain verticals.
Wiki operations and their LLM support live in `gw-wiki`.

`read_session/` — One entry point for display reads: `open_read_session(layout)`
yields a `ReadSession` backed by the persisted read index (reconciled on open)
or the full load; display reads only, never mutation, lint or guidance paths.
It defines the protocol and cache location helpers, the lazy `BundleSession`
and pinned `IndexSession` backends, and the opener's admission and fallback logic.
An `IndexSession` opened with reconciliation also retains the Documents that reconcile parsed, keyed by (raw member id, sha256), until the session closes; read them through `read_session.reconciled_document(session, …)`, which returns `None` for any session lacking the accessor.

`read_session.move_selection(session, roots)` is the optional reparent capability, the one exception to this package's display-only reads (epic D-108): it returns the reconcile's verified walk, the candidate member names for moving `roots` (subtree, referrers, frontmatter mentions, parse errors, unreadable files) and the reconcile-parsed Documents, or a fallback reason string when the index is not usable. It reads no body. The index it consults is schema v2, which stores each document's link and reference-definition targets in a `refs` table (including index and log members) so referrers are selected without parsing bodies.
`materialize(session)` copies an open session into a connection-free `SnapshotSession`
that preserves display reads and ignore overlays after the source closes.
Display `run_*` reads take `session: ReadSession | None = None` and open through
`borrow_read_session(layout, session)`: `None` opens and reconciles (the CLI path),
a passed session is used as is and nothing else is opened (the sidecar's memo).

`guidance/` — deterministic guidance inputs, its own layer so any vertical
(including pipeline guidance assembly) may import it while it imports
only `graph/` and `workspace/` below it. `claims.py`: the claims index (every
`decisions:`/`claims:` entry as a flat row, cached under `<cache_dir>/claims/`
with atomic writes); `closure.py`: a work item's `affects` as tiered,
repo-qualified graph URIs; `commands.py`: the layout-level runs behind
`gw wiki claims refresh | show | closure`, resolving the item's repository
with `workspace.repos`' precedence but degrading instead of refusing.

`events/` is the one vertical with no command entry point: it is a pure
`classify` (in `rules.py`) plus its closed vocabulary (`model.py`), consumed
by `gw-serve`'s watcher. It deliberately does not import
`workspace.dispatch_config`; the caller passes the dispatch document paths.

### The four things "what a workspace is" means concretely

**1. Discovery (`workspace/discovery.py`)** — resolving *the workspace root*,
nothing else. Precedence: explicit `workspace=` argument → `GRAPH_WORKS_DIR`
env var → a `.git` walk-up from `cwd`, defaulting to `<repo>/.works`. The
marker file is `<root>/workspace.yaml`; its absence raises
`WorkspaceNotFound`. Three things are deliberately *not* honored:
the legacy `GRAPH_WIKI_WORKSPACE` env var (names the old layout),
the old repo's `.graph-wiki.local.yaml` pointer, and a `repo-directory:`
pin (repos are declared with paths in the manifest instead). `find_repo_root`
(nearest ancestor `.git`) answers "what repo is this workspace inside" for
gitignore/worktree purposes — it is *not* a scan target; scan targets come
from the manifest's `repositories:` block.

**2. The layout object (`workspace/layout.py`)** — the single source of
truth for where a workspace's parts live, once resolved. `WorkspaceLayout`
is a frozen dataclass: `root`, `config_dir` (default `.gw/`),
`cache_dir` (default `config_dir/cache`), `bundle_dir` (default `okf/`),
`worktrees_dir` (default `config_dir/worktrees`), and `repo_root` (the repo
the workspace lives in, or `None`). **There are deliberately no
module-level path functions** — no `graph_dir(workspace)` anywhere in this
package or below. A consumer always receives a `WorkspaceLayout`, or one
member of it, as an argument; `layout_for` is the single constructor, called
only by `discovery.resolve` and `init.plan_init`. `cache_dir`/`worktrees_dir`
resolve against the *already-resolved* `config_dir`, not the root, so
relocating `config_dir` moves the whole control plane as a unit and
`gitignore_entries` (anchored at `config_dir`) always covers its own derived
members.

**3. The manifest (`workspace/manifest.py`)** — `<root>/workspace.yaml`, the
workspace's discovery authority, read/written entirely through
`config-io`'s catalog (`CATALOG: tuple[ConfigEntry, ...]`) rather than
hand-rolled validation. It carries: `version` (fixed at `1`, no migration
path — a foreign version raises `WorkspaceError`), `initialized_at`,
`topic`, the four layout overrides (`layout.bundle_dir`, `layout.config_dir`,
`layout.cache_dir`, `layout.worktrees_dir` — all default to "derive from
`config_dir`" when absent), the bundle declarations `code_wiki_okf.config.load_config`
reads (`repositories.*.path`, `repositories.*.ignore`, `ignore`, `state_gate.*`),
five `roles.*.<field>` wildcard entries (model/backend/region/max_tokens/max_concurrency
overrides consumed by `gw_wiki.agent_substrate.roles`), `workflow_config` (the top-level
pointer naming the workflow file), and the six `workflow.*` keys
(`auto_drive.max_parallel`/`max_attend`/`supervise_merges`,
`gate.unit_jobs`/`gate.max_concurrent`, `workspace_commits`), which the catalog
declares but whose values are homed in the workflow file (`workflow:` block), not
the manifest; a `workflow.*` key in either manifest layer is refused. So is any key outside the catalog (`manifest.check_unknown_manifest_keys`, a structural walk where `*` matches one mapping key): a hand-edited typo in `workspace.yaml` or `workspace.local.yaml` fails the read naming the file, the key and near misses, instead of its setting silently never applying; `workspace.dir` is `env-only` and refused in a file too. A
manifest with no `workflow_config` refuses with "run `gw bootstrap`"; a
workspace with no `workspace.yaml` at all reads those keys as catalog defaults. `graph_dir` and
`declarations_dir` are **not** stored in the manifest — they're resolved from
the layout and supplied by the caller at read time. `Manifest` itself never
holds a `Path`, only strings; turning overrides into resolved paths is
`layout_for`'s job alone. `checked()`/`resolve_checked_key`/`resolve_checked_all`
exist because a hand-edited manifest bypasses config-io's set-time
validation — only `STORED_ORIGINS` values (`"manifest"`, the committed file,
`"local"`, the per-machine overlay, and `"workflow"` / `"workflow-local"`, the
workflow file pair) get re-checked against the catalog's
declared type/`allowed`; env and default origins are trusted.

**One read seam, two write sites.** `manifest_store(path)` and
`workspace_store(layout)` are how every *reader* of a `workspace.yaml` gets
its store — `read`, `resolve_checked_key`, `resolve_checked_all`,
the wiki role reader, `workspace.dispatch_config`,
`workspace.init`'s projection write, and `workspace.config`. Both forms exist
because `discovery.resolve` calls `read(path)` before a layout exists.

`config-io`'s write path is read-mutate-write, so a write never goes through
`PlainYamlStore` construction inside the seam itself: only `manifest_store`
and `set_value` construct it, and `tests/test_workspace_store_boundary.py`
pins the permitted construction sites at exactly those two.
`gw config set`/`unset` re-resolve the key through
`manifest.resolve_checked_key`, then write through
`dispatch_projection.mutate_workspace_config`, which names a layer already on
the `LayeredYamlStore` it got back — `.base` or `.overlay`, of the manifest or
of the workflow file pair — rather than constructing its own `PlainYamlStore`.
That is what let the seam's return type change (the layered read-only store)
without any caller moving.

`dispatch.parse_rules` validates every supplied rule before matching, including
skill names and unsupported permission/backend fields. `dispatch_config` checks
both explicit manifests for retired keys before overlay merging; local nulls
cannot hide legacy shared settings. `dispatch.resolve_dispatch` is the single
profile cascade and records all six field origins. The planner and next-stage
reader derive attributes from work-tracker state via `dispatch_attributes`.

`dispatch_projection` fingerprints both manifests and both workflow-file layers,
including absent local files, adds a top-level `workflow` block to the projection, checks snapshots before publication, and never
writes merged rules back. See [Dispatch rules](docs/dispatch-rules.md) and the
focused `test_dispatch*.py` suites before changing this seam.

**4. Init (`workspace/init.py`)** — `plan_init` / `apply_init`, a
plan-then-apply pair with **no `dry_run` flag** (not calling `apply_init` is
the dry run, matching six other shipped writers across the workspace). In
order, `apply_init` performs: (1) create directories (`root`, `.gw/`,
`.gw/cache/`, `okf/`, `.gw/worktrees/`); (2) write `<config_dir>/.gitignore`
(only the gitignored members — cache and worktrees dirs) and `<root>/.gitignore`
(`workspace.local.yaml` and the pointer's `.local` sibling, `/workflow.local.yaml` for a new workspace, the per-machine overlays); the
repo's own root `.gitignore` is never edited — in the `.works` shape
`layout.root` is `<repo>/.works`, so the workspace's root gitignore is inside
the workspace; (3) create a missing manifest carrying `workflow_config: workflow.yaml`, or insert
only a missing `workflow_config` line into authored YAML (an existing pointer,
even to `dispatch.yaml`, is never rewritten); create a missing workflow file with
the relay-tail seed, preserving authored files; (4) write `<root>/AGENTS.md` via `render_context_file` — the gw region above
`## Local Conventions` regenerated whole from `assets/AGENTS.md.template`,
the tail beneath that heading carried verbatim — and `<root>/CLAUDE.md` as
the one-line `@AGENTS.md` pointer, both at `layout.root` and never a repo
root; then plan a `- ` delete for `<bundle_dir>/AGENTS.md` and
`<bundle_dir>/CLAUDE.md` when present (`PlannedWrite.mode == "delete"`);
(5) `okf_ext.bundle.plan_scaffold` for `index.md`,
`log.md`, `tags.yaml`; (6) each installer in `INSTALLERS` (currently
`code_wiki_okf`, `work_tracker_okf`, `doc_wiki_okf`'s `install_bundle`);
(7) write `<cache_dir>/config.json`, the manifest's resolved projection —
closing the gap where a gitignored, absent projection would leave every
fail-open hook/routing check silently dormant until someone ran `gw` by
hand. `today` is always injected; nothing in this package reads the clock.

### Gotchas that require reading more than one file

- **`repo_root` vs. scan targets.** `layout.repo_root` is *only* used for
  gitignore placement, worktree roots, and `scanner_excludes` — it is never
  the thing a scan walks. The actual scan target(s) come from the manifest's
  `repositories:` block, resolved through `code_wiki_okf.config.load_config`.
  In a split topology (workspace and code in separate repos),
  `layout.repo_root` resolves to the *workspace's own* repo, not the scanned
  one — conflating the two is the exact bug the type distinction exists to
  prevent.

- **`repo_root` given at init doesn't persist.** `plan_init(..., repo_root=...)`
  is not written anywhere in the manifest. A later `resolve()` call
  re-derives it via `find_repo_root`'s walk-up, and for a workspace that
  lives outside the repo it catalogs, that walk-up yields `None` — not the
  originally-intended repo. A caller that knows the intended repo (because it
  read it from `workspace.yaml`, or because it's the same caller that pinned
  it at init) must pass `repo_root=` to `resolve()` again itself.

- **Manifest values `checked()` accepts, and null.** A key that has a real
  (non-`None`) catalog default but is explicitly written as `null` in
  `workspace.yaml` raises `WorkspaceError` rather than silently falling back
  — an explicit null reads as a deliberate setting, so it's treated as
  malformed rather than "unset".

- **The relay tail is workspace-owned.** Packaged branch defaults have no tail.
  Init seeds `pipeline.RELAY_TAIL_SEED` in a missing shared dispatch document;
  it preserves authored dispatch files. A final relay profile without a tail
  blocks. Fix it with a matching rule in the shared/local dispatch document,
  then sync. Init never translates or silently removes retired manifest keys.

- **`init.WorkspacePlan.diff()` legitimately over-reports.** Each of the
  three installers previews the bundle scaffold independently, so a first
  init's diff lists `index.md`/`log.md`/`tags.yaml` up to four times (once
  per installer plus once for `init`'s own scaffold plan). This is
  documented staleness, not a bug — each preview really is an act the plan
  holds.

- **`workspace.provenance` is the only module in this package that runs
  git**; wiki agent-configuration reports reach it through `probe_git`, and every
  provenance function degrades to `None`/a silent no-op —
  capturing provenance must never fail a stage advance.

- **Config raises; content never does** (`workspace/errors.py`). This
  package's exceptions (`WorkspaceError` and its `WorkspaceNotFound` /
  `WorkspaceConfigError` / `InitError` / `ScanError` / `QueryError`
  subclasses, all `ValueError`) are about environment/configuration
  problems — a missing manifest, an unreadable version, a root that exists
  and isn't a directory. This is a different rule from okf-io's "nothing on
  the content path raises" — don't reach for one when you mean the other. A
  conflicting *bundle content* file during init is never an exception; it's
  a `WriteFailure`/`InstallResult` entry in the plan's result.

- **The front door is lazy.** Adding a retained workspace or graph API means
  updating `_EXPORTS`, `TYPE_CHECKING` and `__all__` together. Kernel submodules
  must not import the top-level package. Wiki APIs are imported from their
  defining `gw_wiki` modules.

- **Context files are whole-body regenerated.** Anything a human writes in
  `<root>/AGENTS.md` above `## Local Conventions` is lost on the next
  `gw bootstrap`; prose belongs beneath that heading. `CLAUDE.md` at the root
  is always replaced by the `@AGENTS.md` pointer. The renderer is pure and
  varies on nothing run to run (`initialized_at` and the configured workflow file pair come from the manifest, the pair passed in by `plan_init` as `workflow_file` / `workflow_local_file`), so
  a second plan is empty — if it is not, something in the template is
  reading a run-to-run input.

See `packages/gw-kernel/README.md` for retained workspace and graph details.
Pipeline dispatch, gate and integration guidance lives in `packages/gw-orchestrate/AGENTS.md`
and `packages/gw-pipeline/AGENTS.md`.
Wiki module guidance lives in `packages/gw-wiki/AGENTS.md`.

`read_session.index_revision(session)` compares an optional index epoch plus its pinned generation, so a rebuilt disposable database cannot reuse a stale sidecar memo even when its numeric generation restarts. Materialized sessions preserve the epoch; the required ReadSession protocol stays unchanged.

## Shared test fixtures

`gw_kernel.testing` ships six test-only pytest fixtures: `graph_dir`,
`empty_graph_dir`, `stale_graph_dir`, `git_repo`, `declare_repos`, and
`code_layout`. Consumer conftests explicitly re-export the fixture objects,
for example `from gw_kernel.testing import graph_dir as graph_dir`.
For `code_layout`, also re-export `git_repo` and `declare_repos`; normal
pytest dependency overrides still apply. Pytest is supplied by the development
environment, not a runtime dependency. Keep collection-specific helper paths
in conftests rather than the installed support module.

Work and archive commands and their integration tests live in `gw-work`. Kernel source and tests never import `gw_work`; the reverse AST guard and import-linter enforce that direction.

`gw_kernel.testing.transactions` and `.seed_copy` provide test-only helpers.
The fixture package preserves the six exports above; application code never imports it.
