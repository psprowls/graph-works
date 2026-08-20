# graph-works-cli

`gw` is a thin Typer CLI over `graph-works-core`: it routes, parses, formats, and traces while
domain decisions stay in the core and wiki packages.

`graph-works-core` is a real, pinned dependency (see `CLAUDE.md` for the package layout and the
D-003 audit note).

## Wiki command surface

Every command accepts `--workspace PATH`. Initialized commands resolve it from the explicit path,
then `GRAPH_WORKS_DIR`, then workspace discovery.

Root commands:

- `gw bootstrap --topic TEXT [--workspace PATH] [--json]` initializes a workspace.
- `gw scan [--no-narrate | --emit-worklist | --apply]` runs, emits, or applies a scan handoff.
- `gw ingest --source PATH [--json]` ingests one source.
- `gw query --query TEXT [--limit N]` answers a query with citations.

Wiki commands:

- `gw wiki lint [--json]`
- `gw wiki stats [--top N] [--json]`
- `gw wiki index`
- `gw wiki archive [TARGET] [--dry-run]`
- `gw wiki proposals [--json]`
- `gw wiki proposal file --lane TEXT --title TEXT --id TEXT --resource PATH [--description TEXT] [--rationale TEXT] [--evidence TEXT]`
- `gw wiki proposal approve TARGET`
- `gw wiki proposal reject TARGET`

Mutating commands apply by default. The sole preview is `--dry-run`: only `gw wiki archive --dry-run`
prints its plan without applying it.

### Scan worklist handoff

Use the emitted JSON to hand per-entity prose tasks to another process. Artifacts always live below
the workspace's `_cache/scan/` directory.

```console
$ gw scan --emit-worklist --workspace .works
{
  "worklist_path": ".works/_cache/scan/worklist.json",
  "briefs_dir": ".works/_cache/scan/briefs",
  "results_dir": ".works/_cache/scan/results",
  "short_head": "abc1234",
  "entities_written": [],
  "entities_deleted": [],
  "entity_errors": []
}

$ # Write one result JSON per task into .works/_cache/scan/results/.
$ gw scan --apply --results-dir .works/_cache/scan/results --short-head abc1234 --workspace .works
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

## Testing

    uv run --package graph-works-cli pytest packages/graph-works-cli/tests -v
    uv run --package graph-works-cli mypy --strict packages/graph-works-cli/src

## Configuration

`gw config` is the sole programmatic writer for `workspace.yaml` catalog keys:

    gw config get <key> [--workspace PATH] [--json]
    gw config list [--workspace PATH] [--json]
    gw config set <key> <value> [--workspace PATH] [--json]
    gw config unset <key> [--workspace PATH] [--json]
    gw config sync [--workspace PATH] [--json]
    gw config hooks enable transcript [--repo PATH] [--json]
    gw config hooks disable transcript [--repo PATH] [--json]

`set` and `unset` refresh `_config/config.json` automatically. `sync` is the manual refresh after a
hand edit. Hook commands update only `.claude/settings.local.json`; their merge/remove behavior lives
in `graph_works_core.hooks`.

The interactive legacy `config init` wizard is intentionally absent. Guided onboarding belongs to
the graph-works plugin and composes the individual commands above.
