# packages/code-wiki-okf

Python ≥3.12 (the workspace floor). Tests are pytest.

## Layout

- `src/code_wiki_okf/` — library + CLI (`cli.py`, `__main__.py`)
- `src/code_wiki_okf/assets/` — package-data seed files copied byte-for-byte
  by `plan_install()`
- `tests/` — pytest tests

## Conventions

- Never reads the clock: `today=`/`now=` are arguments everywhere except
  `cli.py`, the one module allowed to call it.
- Never discovers paths: the bundle root is always explicit.
- Config raises (`ConfigError`); bundle content never does — okf-io's
  never-raise rule is for concept content, not this package's own
  `_repositories.yaml`.
- Tier 3 per ADR-0005: depends on `okf-io`, `okf-ext[schemas]`,
  `code-graph-io`. Nothing depends on this package.

## Testing

`uv run --package code-wiki-okf pytest packages/code-wiki-okf/tests -v` from
the repo root.
