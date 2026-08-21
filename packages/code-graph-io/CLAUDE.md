# packages/code-graph-io

Python ≥3.12 (the workspace floor). Tests are pytest.

## Layout

- `src/code_graph_io/` — library only
- `src/code_graph_io/parser/` — tree-sitter-backed source parsing (formerly the standalone `code-parser` package)
- `tests/` — pytest tests (unit + integration)
- `tests/parser/` — parser tests, with `tests/parser/fixtures/{python,javascript,typescript}/`
- `conftest.py` — pytest configuration

## Parsing (`code_graph_io.parser`)

Tree-sitter setup and AST traversal, the `SourceNode` / `Reference` / `Span` data
model, per-language parsers (Python, JavaScript, TypeScript at v1), and the
`to_graph_records()` projection. Parsing stays code-graph-io-private: other modules
reach the record types through `code_graph_io.records` and language metadata through
`code_graph_io.source_meta` rather than importing `code_graph_io.parser` directly —
enforced by the `"Parsing sits below the graph"` import-linter contract (root
`pyproject.toml`).

`.tsx` files must use the `tsx` tree-sitter grammar, not `typescript`, because the
plain TypeScript grammar cannot parse JSX and will drop component bodies into
error-laden trees. The emitted logical language remains `typescript`; only the
grammar changes for `.tsx` input.

Parser tests are fixture-driven: `tests/parser/fixtures/<lang>/` paired with
`*.expected.json` (parser output) and `*.graph.expected.json` (graph projection
output), parametrized via `@pytest.mark.parametrize` over `_fixture_loader.fixtures_for(...)`.

## Conventions

- Read-only queries always go through `store.read_only_connect()`.
- All updates run inside one SQLite transaction (`store.transaction()`).
- Errors go to stderr, JSON output goes to stdout. Never mix.
- Exit codes are stable from v1 forward — see `exit_codes.py`.

## Graph DB boundary (invariant)

Only `code-graph-io` may build the `code.db` path, open a connection to it, or run
SQL against the code graph. Every other package reaches the graph through a
`GraphReader` / `GraphStore` obtained from `code_graph_io.open_reader(graph_dir=…)` /
`code_graph_io.open_writer(graph_dir=…)`. The conn-level modules (`queries`, `upsert`,
`resolve`, `store`, `schema`) are code-graph-io-internal —
callers import the handle API, record dataclasses, and error classes from the
`code_graph_io` top level instead. Enforced by `tests/test_db_boundary.py`.

## Testing

`pytest tests/ -v` from the package root, or via the workspace from the repo root.
