# graph-works-cli

`gw` is a thin Typer CLI over `graph-works-core`: it routes, parses, formats, and traces while
domain decisions stay in the core and wiki packages.

`graph-works-core` is a real, pinned dependency (see `CLAUDE.md` for the package layout and the
D-003 audit note).

## Wiki command surface

Every command accepts `--workspace PATH`. Initialized commands resolve it from the explicit path,
then `GRAPH_WORKS_DIR`, then workspace discovery.

Root commands:

- `gw bootstrap --topic TEXT [--workspace PATH] [--repo-root PATH] [--dry-run] [--json]` initializes a
  workspace. `--repo-root` pins the repository the workspace catalogs; pass it when the workspace lives
  outside that repository, where the `.git` walk-up cannot find it.
- `gw scan [--no-narrate | --emit-worklist | --apply]` runs, emits, or applies a scan handoff.
- `gw ingest --source PATH [--json]` ingests one source.
- `gw query --query TEXT [--limit N]` answers a query with citations.

For stage completion, `gw work advance PATH --from PHASE` checks the item's current phase under
the advance lock before applying a transition. Use `--from none` for an item with no phase yet;
omitting `--from` leaves the current phase unchecked for compatibility with existing callers.
The accepted values are `none`, `design`, `plan`, `execute`, `finish`, and `done`. A stale value
returns `phase-mismatch` without changing the item, artifacts, or active-work pointer. The
`--from` guard only checks the phase and does not make repeated calls without `--from` idempotent.
Separately, completing design or plan requires its canonical artifact to exist as a regular file;
otherwise the command returns `artifact-missing` without advancing the item.

Wiki commands:

- `gw wiki lint [--json]`
- `gw wiki drift [--backend TEXT] [--only TEXT] [--dry-run] [--json]`
- `gw wiki stats [--top N] [--json]`
- `gw wiki index`
- `gw wiki archive [TARGET] [--dry-run]`
- `gw wiki tags inventory [--json]`
- `gw wiki tags draft OUT --as-of YYYY-MM-DD [--floor N] [--ceiling F]`
- `gw wiki tags apply DISPOSITION [--only merge|strip] [--dry-run]`
- `gw wiki tags gate [--json]`
- `gw wiki proposals [--json]`
- `gw wiki proposal file --lane TEXT --title TEXT --id TEXT --resource PATH [--description TEXT] [--rationale TEXT] [--evidence TEXT]`
- `gw wiki proposal approve TARGET`
- `gw wiki proposal reject TARGET`

Most mutating commands apply by default; `--dry-run` is the preview option. `gw bootstrap --dry-run`
and `gw wiki archive --dry-run` show the plan without applying. `gw wiki drift` inverts this: it
previews by default (use `--no-dry-run` to apply).

`gw wiki tags gate` is the narrow, CI-shaped question — it exits non-zero on any tag the
vocabulary does not declare, without running a full lint.

### Scan worklist handoff

Use the emitted JSON to hand per-entity prose tasks to another process. Artifacts always live below
the workspace's `.gw/cache/scan/` directory.

```console
$ gw scan --emit-worklist --workspace .works
{
  "worklist_path": ".works/.gw/cache/scan/worklist.json",
  "briefs_dir": ".works/.gw/cache/scan/briefs",
  "results_dir": ".works/.gw/cache/scan/results",
  "short_head": "abc1234",
  "entities_written": [],
  "entities_created": [],
  "entities_updated": [],
  "entities_deleted": [],
  "entity_errors": []
}

$ # Write one result JSON per task into .works/.gw/cache/scan/results/.
$ gw scan --apply --results-dir .works/.gw/cache/scan/results --short-head abc1234 --workspace .works
{
  "narrated": 1,
  "sections_filled": 2,
  "stamped": 1,
  "entity_errors": []
}
```

### Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | Completed, including an idempotent no-op or successful fallback. |
| 1 | Refusal, incomplete write, entity error, lint failure, or operational failure. |
| 2 | Stale scan handoff: `--short-head` differs from the emitted worklist. |
| 3 | An initialized command could not find a workspace manifest. |
| 4 | The emitted scan worklist has an unsupported schema version. |
| 5 | A required repository could not be resolved. |

## Utility commands

- `gw util platform [--probe] [--workspace PATH] [--json]` reports the
  platform, durability tier, dispatch backend, file lock and process control
  — each derived from the machinery that owns it, with `unavailable` as a
  derived property rather than a maintained list. `--probe` also runs
  liveness checks and requires a workspace (via `--workspace` or discovery);
  without it the command is a pure declaration and needs no workspace.

  ```console
  $ gw util platform
  platform: darwin
  python: 3.12.13

  durability-tier: posix-strong (available)
    guarantee: an exclusive lock is held across the whole read-mutate-write cycle
    ...
  unavailable: none
  ```

- `gw util describe-surface [--json]` describes every command in the `gw`
  tree — the machine-readable surface freeze used to catch an undocumented or
  accidentally-renamed command.

- `gw util line-endings [--fix] [--workspace PATH] [--json]` detects (and,
  with `--fix`, repairs) CRLF reaccumulation in the bundle. `.gitattributes`
  declares every tracked bundle member LF-in-worktree, but git enforces that
  only at checkout — nothing enforces it on write, and `git status` cannot
  see a violation because it compares normalised content. The default
  invocation reports every non-binary member whose on-disk bytes contain
  CRLF, with a count per file, and exits non-zero if any are found; `--fix`
  rewrites each to LF in place and exits zero.

## Testing

    uv run --package graph-works-cli pytest packages/graph-works-cli/tests -v
    uv run --package graph-works-cli mypy --strict --platform linux packages/graph-works-cli/src
    uv run --package graph-works-cli mypy --strict --platform win32 packages/graph-works-cli/src

## Configuration

`gw config` is the sole programmatic writer for `workspace.yaml` catalog keys:

    gw config get <key> [--workspace PATH] [--json]
    gw config list [--workspace PATH] [--json]
    gw config set <key> <value> [--workspace PATH] [--json]
    gw config unset <key> [--workspace PATH] [--json]
    gw config sync [--workspace PATH] [--json]
    gw config hooks enable transcript [--repo PATH] [--json]
    gw config hooks disable transcript [--repo PATH] [--json]

`set` and `unset` refresh `.gw/cache/config.json` automatically. `sync` is the manual refresh after a
hand edit. Hook commands update only `.claude/settings.local.json`; their merge/remove behavior lives
in `graph_works_core.hooks`.

The interactive legacy `config init` wizard is intentionally absent. Guided onboarding belongs to
the graph-works plugin and composes the individual commands above.

## Dispatch configuration

`gw work next <path> --json` exposes the resolved `dispatch.profile` and
per-field provenance; orchestration dispatches expose the equivalent fields.
Human output explains agent/model/effort and resets. `gw config sync` refreshes
all shared/local dispatch inputs. Retired-key `gw config unset [--local]` is
removal-only and defers sync until cleanup is complete. See the
[dispatch guide](../graph-works-core/docs/dispatch-rules.md) for YAML examples
and the explicit cutover procedure; no command translates old routing choices.

## Platform

The CLI supports POSIX and Windows. `gw agent-config show` injects the current
POSIX user ID to resolve Claude's ownership-sensitive local settings location;
Windows uses Claude's starting-directory location and does not call `os.getuid`.
Run `gw util platform` for the live per-capability platform report.
