# packages/code-graph-io

Python ≥3.12 (the workspace floor). Tests are pytest.

## Layout

- `src/code_graph_io/` — library + CLI
- `tests/` — pytest tests (unit + integration + CLI subprocess)
- `conftest.py` — pytest configuration

## Conventions

- Read-only queries always go through `store.read_only_connect()`.
- All updates run inside one SQLite transaction (`store.transaction()`).
- Errors go to stderr, JSON output goes to stdout. Never mix.
- Exit codes are stable from v1 forward — see `exit_codes.py`.

## Graph DB boundary (invariant)

Only `code-graph-io` may build the `code.db` path, open a connection to it, or run
SQL against the code graph. Every other package reaches the graph through a
`GraphReader` / `GraphStore` obtained from `code_graph_io.open_reader(workspace)` /
`code_graph_io.open_writer(workspace)`. The conn-level modules (`queries`, `upsert`,
`resolve`, `store`, `schema`) are code-graph-io-internal —
callers import the handle API, record dataclasses, and error classes from the
`code_graph_io` top level instead. Enforced by `tests/test_db_boundary.py`.

Note: `commands/query.py` keeps `import sqlite3` for its `search.db` embeddings
cache — that is NOT graph data and NOT `code.db`, so it is outside this boundary.

## Testing

`pytest tests/ -v` from the package root, or via the workspace from the repo root.
