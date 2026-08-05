# [CLAUDE.md](http://CLAUDE.md) — code-parser

This package is a sibling Python library in the agent-workspace monorepo. It is NOT a  
Claude Code plugin. It is consumed by `packages/code-graph-io/` (and  
potentially other Python projects) to parse source files into an intermediate  
`SourceTree` and project that tree onto graph records.

## Boundary

This package owns:

- Tree-sitter setup and AST traversal
- The `SourceNode` / `Reference` / `Span` data model
- Per-language parsers (Python, JavaScript, TypeScript at v1)
- The `to_graph_records()` projection

This package does NOT own:

- SQLite
- Manifest scanning (`package.json`, `pyproject.toml`)
- Cross-file edge resolution at v1
- Git-diff incremental update orchestration

Those live in `packages/code-graph-io/`.

## Parser note

`.tsx` files must use the `tsx` tree-sitter grammar, not `typescript`, because
the plain TypeScript grammar cannot parse JSX and will drop component
bodies into error-laden trees. The emitted logical language remains
`typescript`; only the grammar changes for `.tsx` input.

## Tests

`pytest`. Install with the `test` extra and run from this package's root:

```bash
pip install -e '.[test]'
pytest
```

Fixture-driven tests live under `fixtures/<lang>/` paired with
`*.expected.json` (parser output) and `*.graph.expected.json` (graph
projection output). Parametrized via `@pytest.mark.parametrize` over
`fixtures_for(...)`.

## Stdlib stance

This package depends on `tree-sitter` and `tree-sitter-language-pack`. Other  
agent-workspace packages that don't need parsing don't inherit those deps. This is  
the "local stdlib break" the wiki spec describes; it stays inside this  
package's dependency closure.
