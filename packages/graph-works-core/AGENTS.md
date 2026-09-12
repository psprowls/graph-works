# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this package is

Band 3: "the one package that knows what a workspace is." A workspace is a
directory on disk (default `<repo>/.works`) holding a manifest
(`workspace.yaml`), an OKF bundle, and a control-plane directory. Every
package below this one (`okf-io`, `okf-ext`, `code-wiki-okf`,
`doc-wiki-okf`, `code-graph-io`, `work-tracker-okf`, `config-io`,
`models-io`, `subagents-io`) receives resolved paths as plain arguments —
none of them has a `graph_dir(workspace)`-shaped function, and none of them
imports `graph_works_core`. An import-linter `layers` contract in the root
`pyproject.toml` enforces the direction; `tests/test_graph_works_core_boundaries.py`
covers the half import-linter's `grimp` cannot see (a module inside this
package importing its own top-level `graph_works_core`, which would let one
vertical pull in every other vertical through the front door).

## Commands specific to this package

```bash
uv run --package graph-works-core mypy --strict --platform linux packages/graph-works-core/src
uv run --package graph-works-core mypy --strict --platform win32 packages/graph-works-core/src
uv run --package graph-works-core pytest packages/graph-works-core/tests
uv run --package graph-works-core pytest packages/graph-works-core/tests \
  --cov=graph_works_core --cov-branch --cov-report=term-missing --cov-fail-under=95
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
uv run --package graph-works-core pytest packages/graph-works-core/tests/workspace
uv run --package graph-works-core pytest packages/graph-works-core/tests/workspace/test_init.py
uv run --package graph-works-core pytest -k "discovery or manifest"
```

`pyproject.toml` repeats `asyncio_mode = "auto"` locally (not inherited from
the root) because `tests/agent_substrate/test_agent_loop.py` is entirely
`async def`, and under `uv run --package graph-works-core` pytest's rootdir
resolves to *this* `pyproject.toml`, not the workspace root's.

### `hatch_build.py`

A custom wheel build hook, worth knowing about before touching packaging.
The sdist stages `plugins/gw/hooks/examples` inside
`src/graph_works_core/_hook_scripts` (see `pyproject.toml`'s
`sdist.force-include`). Building a wheel *directly from the source tree*
(not from an sdist) would otherwise ship without those scripts, since they
live outside `src/`. The hook checks whether `_hook_scripts` is already
present (sdist path — nothing to do) and, if not, force-includes the
canonical `plugins/gw/hooks/examples` directory into
`graph_works_core/_hook_scripts` for the wheel, raising `FileNotFoundError`
if that canonical directory is missing. If you move or rename
`plugins/gw/hooks/examples`, update the hardcoded relative path
here (`Path(self.root).parents[1] / "plugins" / "gw" / "hooks" / "examples"`)
and `CANONICAL_SCRIPTS_DIR` in `hooks.py`.

The command `gw config hooks enable` writes binds the interpreter that ran
it as the first argv token (no shell, no `VAR=value` prefix) and renders it
with `subprocess.list2cmdline` on win32 or `shlex`-equivalent quoting
elsewhere (`graph_works_core.hooks._quote_command`) — never hand-roll a
shell-syntax command string here.

## Architecture

### Module layout (three import-linter layers, per the package `__init__.py`)

```
workspace/    layer 0 — errors, layout, manifest, discovery, init, provenance, anchor, pipeline, repos, config, context_seed, transactions
agent_substrate/ : graph/ : prompts/                                   layer 1, shared
ingest/ : scan/ : query/ : lint_drift/ : archive/ : orchestrate/ : wiki_stats/ : work/    layer 2, independent verticals
```

Each layer-2 vertical owns one command entry point (`commands.py` when that
name doesn't collide with the vertical's own name, otherwise a flat module
like `lint_drift/lint.py`), its own prompts, and nothing else reaches across
verticals except through `workspace/` — `orchestrate` is the exception,
split across `commands.py` and `stage_advance.py` at its planner/stage-advance
seam; see "The dispatch seam" in the README.

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
workspace's **one** configuration file, read/written entirely through
`config-io`'s catalog (`CATALOG: tuple[ConfigEntry, ...]`) rather than
hand-rolled validation. It carries: `version` (fixed at `1`, no migration
path — a foreign version raises `WorkspaceError`), `initialized_at`,
`topic`, the four layout overrides (`layout.bundle_dir`, `layout.config_dir`,
`layout.cache_dir`, `layout.worktrees_dir` — all default to "derive from
`config_dir`" when absent), the bundle declarations `code_wiki_okf.config.load_config`
reads (`repositories.*.path`, `repositories.*.ignore`, `ignore`, `state_gate.*`),
five `roles.*.<field>` wildcard entries (model/backend/region/max_tokens/max_concurrency
overrides consumed by `agent_substrate.roles`), the `workflow.pipeline.*.<field>`
dispatch-table overrides, and `workflow.auto_drive.*`. `graph_dir` and
`declarations_dir` are **not** stored in the manifest — they're resolved from
the layout and supplied by the caller at read time. `Manifest` itself never
holds a `Path`, only strings; turning overrides into resolved paths is
`layout_for`'s job alone. `checked()`/`resolve_checked_key`/`resolve_checked_all`
exist because a hand-edited manifest bypasses config-io's set-time
validation — only `STORED_ORIGINS` values (`"manifest"`, the committed file,
and `"local"`, the per-machine overlay) get re-checked against the catalog's
declared type/`allowed`; env and default origins are trusted.

**One read seam, two write sites.** `manifest_store(path)` and
`workspace_store(layout)` are how every *reader* of a `workspace.yaml` gets
its store — `read`, `resolve_checked_key`, `resolve_checked_all`,
`agent_substrate.roles`, `workspace.pipeline`, `orchestrate._routing_rules`,
`workspace.init`'s projection write, and `workspace.config`. Both forms exist
because `discovery.resolve` calls `read(path)` before a layout exists.

`config-io`'s write path is read-mutate-write, so a write never goes through
`PlainYamlStore` construction inside the seam itself: only `manifest_store`
and `set_value` construct it, and `tests/test_workspace_store_boundary.py`
pins the permitted construction sites at exactly those two.
`graph_works_cli.config_cli.main._store` calls `workspace_store` like any
other reader, then writes by naming a layer already on the `LayeredYamlStore`
it got back — `.base` or `.overlay` — rather than constructing its own
`PlainYamlStore`. That is what let the seam's return type change (the layered
read-only store) without any caller moving.

A manifest-sourced `workflow.pipeline.<variant>.skill` is shape-checked at read
time by `pipeline.check_skill_name`, beside `manifest.checked()` and for the
same reason. A **bare** name is valid and used verbatim (user-level and
repo-local skills carry no plugin prefix); empty, whitespace-only and
malformed-qualification values (`a:`, `:b`, `a:b:c`) raise `WorkspaceError`.
There is no charset rule. `PACKAGED_PIPELINE` is not checked at runtime — its
shape is pinned by `test_pipeline.py` instead.

**4. Init (`workspace/init.py`)** — `plan_init` / `apply_init`, a
plan-then-apply pair with **no `dry_run` flag** (not calling `apply_init` is
the dry run, matching six other shipped writers across the workspace). In
order, `apply_init` performs: (1) create directories (`root`, `.gw/`,
`.gw/cache/`, `okf/`, `.gw/worktrees/`); (2) write `<config_dir>/.gitignore`
(only the gitignored members — cache and worktrees dirs) and `<root>/.gitignore`
(one line: `workspace.local.yaml`, the gitignored per-machine overlay); the
repo's own root `.gitignore` is never edited — in the `.works` shape
`layout.root` is `<repo>/.works`, so the workspace's root gitignore is inside
the workspace; (3) write `<root>/workspace.yaml` **only if absent,
never overwritten** — this is where `repositories:`/`ignore:` get seeded
from the detected repo root; (4) write `<root>/AGENTS.md` via `render_context_file` — the gw region above
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

- **The relay-tail hole is deliberate, and only `init` closes it for new
  workspaces.** The packaged dispatch table has no default
  `prompt_tail` for the `branch` variant, so a `relay`-mode worker dispatched
  without one falls into an interactive menu with nobody watching.
  `plan_init` seeds `pipeline.RELAY_TAIL_SEED` into a fresh workspace's
  manifest specifically because the manifest write only happens when
  `workspace.yaml` is absent — re-running init over an existing workspace
  never arms an old one retroactively. The fix for an existing workspace
  without a tail is manual: `gw config set workflow.pipeline.branch.prompt_tail "…"`.

- **`init.WorkspacePlan.diff()` legitimately over-reports.** Each of the
  three installers previews the bundle scaffold independently, so a first
  init's diff lists `index.md`/`log.md`/`tags.yaml` up to four times (once
  per installer plus once for `init`'s own scaffold plan). This is
  documented staleness, not a bug — each preview really is an act the plan
  holds.

- **`workspace.provenance` is the only module in this package that runs
  git**, and every one of its functions degrades to `None`/a silent no-op —
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

- **`orchestrate.commands` walks the whole vault on every `plan()` call**
  (decision-hold scan) and reads the owning epic's decisions ledger *twice*
  per plan (once for the routing gate, once for the plan's own decision
  fields) — not one atomic snapshot. Both are known, accepted limits
  documented in the README rather than bugs to fix reflexively.

- **`__init__.py`'s hoisting rules are intentional, not an oversight if a
  name seems "missing" from the top level.** `manifest`, `roles`, and
  `prompts` stay qualified submodules (`graph_works_core.workspace.manifest.read`,
  not a top-level `read`) because their names read better qualified and
  hoisting them would clutter the front door with generic names. Each
  vertical hoists only its call-shaped API (e.g. `run_ingest_source`,
  `run_scan`, `run_archive`) while its file-surface/command internals
  (`scan.commands.*`, `query.commands.*`) stay qualified. `graph_target` and
  `resolve` are hoisted because they *are* the call, the same reason
  `apply_init`/`plan_init` are.

- **Context files are whole-body regenerated.** Anything a human writes in
  `<root>/AGENTS.md` above `## Local Conventions` is lost on the next
  `gw bootstrap`; prose belongs beneath that heading. `CLAUDE.md` at the root
  is always replaced by the `@AGENTS.md` pointer. The renderer is pure and
  varies on nothing run to run (`initialized_at` comes from the manifest), so
  a second plan is empty — if it is not, something in the template is
  reading a run-to-run input.

See `packages/graph-works-core/README.md` for the fuller narrative,
including the agent substrate, the graph surface's exit-code table, the
ingest pipeline, the dispatch/pipeline seam, and the custom-type provenance
contract — all of which build on the layout/manifest/discovery/init
primitives documented above.
