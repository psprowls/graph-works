# migrate-source-paths — point dead `source_path` values at `sources/references/` copies

One-shot repair for C1. Every `sources/**/*.md` whose `source_path` names no
bundle member (the same test `schemas.unresolved-member` applies) gets its
target copied to `sources/references/<page-stem><ext>` and its `source_path`
rewritten to that copy — the ingest contract's destination. Deleted `raw/`
files are restored from git. Any refusal writes nothing.

## Use

```bash
WS=/Users/pat/Personal/graph-works/gw-workspace

uv run python scripts/migrate_source_paths.py "$WS/okf"            # dry run
uv run python scripts/migrate_source_paths.py "$WS/okf" --write    # apply
uv run python scripts/migrate_source_paths.py "$WS/okf"            # again: "0 planned"
```

`--restore-from` (default `fdbeb856^`) is the workspace revision that still
holds the deleted `raw/_archive/**` files.
