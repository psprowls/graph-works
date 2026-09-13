# convert-config — `.graph-wiki.yaml` v2 to `workspace.yaml` v1

Converts a workspace's `.graph-wiki.yaml` v2 into `workspace.yaml` v1 plus the
`.gw/` control plane, and leaves behind a `.gw/cache/config.json` projection the
plugin's hooks read correctly. `version: 1` is a **fresh format, not v3** — there
is no migration path inside `graph-works-core`, and `gw bootstrap` never
replaces an authored manifest. This script converts non-dispatch settings;
retired dispatch choices require explicit cleanup and reauthoring.

**One direction only.** There is no reverse converter; the rollback is the frozen
copy taken before the run.

Dry-run by default. Nothing is created and nothing is run without `--write`.

## Use

```bash
# Read what would change. Writes nothing, runs nothing.
uv run python scripts/convert_config.py "$WORKSPACE"

# Apply it.
uv run python scripts/convert_config.py "$WORKSPACE" --write
```

Run it through `uv run` from the repo root, and make sure the environment is
provisioned first (`just sync`, i.e. `uv sync --all-packages`). The script imports
`graph_works_core`, `code_wiki_okf` and `config_io`, so a bare
`python scripts/convert_config.py` gets
`ModuleNotFoundError: No module named 'graph_works_core'` — the same trap
`convert-wikilinks.md` documents. The default `--gw` is itself a
`uv run --package …` invocation, so it inherits the script's cwd.

Always read a dry run first. The disposition table it prints is the artifact to
review: one line per key, what became of it, and why.

## Options

| Flag | Default | Effect |
|---|---|---|
| `WORKSPACE` | *required* | The directory holding `.graph-wiki.yaml`. The workspace, not the vault. |
| `--write` | off | Apply. Without it nothing is created and nothing is run. |
| `--topic` | `graph-works` | Value for `topic:`. Display name only. |
| `--repo-name` | basename of the resolved repo path | The key under `repositories:`. |
| `--repo-path` | from `repo-directory` | Override the scan target. |
| `--bundle-dir` | `wiki` | Value for `layout.bundle_dir`. |
| `--no-sync` | off | Skip the `gw config sync` shell-out, for a workspace whose CLI is not yet swapped. Leaves the conversion **half done**. |
| `--gw` | `uv run --package graph-works-cli gw` | The CLI invocation. The bare `gw` on PATH is the *legacy* binary until `2026-08-21-bug-gw-path-resolves-donor-cli` lands. |
| `-h`, `--help` | | Usage. |

Exit code is `0` on success, `2` if `WORKSPACE` is not a directory, and `1` for
any refusal.

## What it does, in order

1. **Read** `.graph-wiki.yaml`, plus `.graph-wiki.local.yaml` when present (the
   local file wins). Refuses any version but 2, and any top-level key without a
   decided disposition. Each explicit source is checked before overlay merging
   for `workflow.pipeline`, `workflow.auto_drive.models`,
   `workflow.auto_drive.overrides`, and `workflow.auto_drive.permission_mode`.
   Any presence, including null/empty values, refuses conversion before writes.
   Save the desired choices, remove these keys explicitly, and recreate rules
   using the [dispatch guide](../packages/graph-works-core/docs/dispatch-rules.md).
2. **Dispose** — one row per key: carry, re-express, seed or drop, each with its
   reason.
3. **Report** the table. A dry run stops here.
4. **Write `workspace.yaml`** — only when absent, or byte-identical to what it
   would render. A present-and-different manifest is a refusal, never an
   overwrite.
5. **Validate through four independent readers** — `manifest.read`,
   `manifest.resolve_checked_all` (the path `gw config list` takes),
   `code_wiki_okf.config.load_config`, and
   `validate_workspace_dispatch_layers` for the new reference and retired-key refusal.
   A failure unlinks a manifest this run created and exits 1.
6. **Create the control plane** — the four layout directories and
   `.gw/.gitignore`, a missing `dispatch.yaml` with the default branch relay
   rule, and root ignore entries for local manifests/dispatch rules. Authored
   dispatch files remain untouched. The manifest points at the shared file;
   `max_parallel` and `supervise_merges` remain operational settings.
7. **Sync** — `gw config sync --workspace <root>`, then three post-conditions:
   the projection exists, its `_meta.source_sha256` matches the manifest's, and
   it carries `layout.bundle_dir`. This is deliberately the **last** write, so
   the hook's staleness comparison is clean.

## Two traps this exists to avoid

**`layout.bundle_dir` is seeded explicitly**, even though it is an override key
and `render_initial` deliberately omits the layout block. The projection carries
explicit values only, and the routing hook defaults `BUNDLE_DIR` to `okf`, so an
unset key is a *hole*, not an inherited default — every workflow artifact would
route under `<workspace>/okf/` in a vault whose bundle is `wiki/`.

**`ignore` is seeded as `[]`, not from `layout.scanner_excludes`.** The workspace
is its own git repo, so `scanner_excludes` evaluates to `("./**",)` — an ignore
that excludes the entire scan. The workspace does not live inside the repo it
scans, so there is nothing to exclude.

## What it does not do

- **The bundle scaffold and `_schema/`.** `index.md`, `log.md`, `tags.yaml` and
  the three bundle installers are C4 **phase 3**, reached through `gw bootstrap`
  — which is idempotent over what this creates and skips the manifest.
- **The plugin and CLI swap** — C4 phase 2.
- **The ~430 pages** — C2. This touches the control plane only.
- **Deleting `.graph-wiki.yaml` or `.graph-wiki/`.** Both are left in place,
  frozen, for the duration of the cutover.

It also **reports but does not set** `GRAPH_WORKS_DIR`. `workspace.dir` is an
`env-only` catalog entry, so there is nothing to write; the script prints the
`export GRAPH_WORKS_DIR=<root>` line for the operator to act on. Without it the
plugin's resolver finds nothing and every hook goes dormant.

## Rollback

There is no reverse converter. Take a frozen copy of the workspace before running
with `--write`, and restore it if the conversion is wrong.

## Acceptance check

After a `--write` run against the live workspace:

```bash
gw config list --workspace "$WORKSPACE"   # exits 0; every carried key shows origin `manifest`
gw lint --workspace "$WORKSPACE"          # reports no `config projection missing`
```

Those two, not the test suite, are the gate. The suite proves the dispositions;
these prove the workspace.
