# code-graph-io

Code-graph backend for agent-workspace. Owns:

- SQLite schema + store at `<graph_dir>/code.db` (the graph directory is supplied by the caller)
- Tree-sitter-backed source parsing (`code_graph_io.parser`) into a span-bearing
  `SourceTree`, projected onto `GraphRecords` (`code_graph_io.records`)
- Upsert from those `GraphRecords`
- Manifest scanning (`pyproject.toml`, `package.json`) → `kind:package` nodes
- Cross-file edge resolution sweep
- Read-only query layer (`find`, `callers`, `callees`, `imports`, `describe_package`, `describe_path`)

## Parsing quick start

```python
from pathlib import Path
from code_graph_io.parser import parse_file, to_graph_records

tree = parse_file(Path("src/foo.py"), package="my-package")
records = to_graph_records(tree)
print(records.nodes)
print(records.edges)
```

v1 covers Python, JavaScript, and TypeScript with a single graph projection.
`.tsx` files are parsed with the `tsx` tree-sitter grammar so JSX-bearing React
components are preserved; plain `.ts` files continue to use `typescript`.

## Exit codes

Stable from v1 forward — script consumers can rely on these. code-graph-io only
declares the constants below (`exit_codes.py`); the command layer that returns
them lives in the sibling `graph-works-core` package
(`graph_works_core/graph/commands.py`, `graph_tools.py`) — the live user-facing
entry point is `gw graph build`.

| Code | Name | Meaning |
|---|---|---|
| 0 | `SUCCESS` | command succeeded |
| 1 | `GENERIC` | unhandled error or "not found" for `describe-*` |
| 2 | `STALE` | **reserved** — declared in `exit_codes.py`, zero producers today |
| 3 | `NOT_INITIALIZED` | no `code.db` yet — run a full build |
| 4 | `SCHEMA_MISMATCH` | the graph's on-disk schema doesn't match this build's expectations |
| 5 | `NOT_IN_GIT_REPO` | a build/status command ran outside a git repo |
| 6 | `UPDATE_IN_PROGRESS` | a concurrent update is already writing the graph |
| 7 | `AMBIGUOUS` | an entry-point identifier resolved to more than one match |

## Ignoring directories

The scanner skips a built-in set of directories by default:

`.git`, `node_modules`, `.worktrees`, `.venv`, `venv`, `dist`, `build`, `__pycache__`, `.tox`, `.nox`

To skip additional paths, callers compile `ignore:` glob patterns (from a
workspace's `workspace.yaml`, via `code_wiki_okf.config.load_config`) into
an `IgnoreSpec` (`code_graph_io._ignore.compile_ignore`) and pass it through —
code-graph-io itself reads no ignore file. See `_ignore.py` for the pattern
syntax (git-pathspec-glob-like: `*`/`?` do not cross `/`, `**` does, a
pattern with no `/` matches only an exact path component).
