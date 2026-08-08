# Workspace task runner — okf-io, okf-ext, code-graph-io, code-parser,
# code-wiki-okf.
# Each recipe is exactly the command a future CI job will call.
#
# okf-io and okf-ext share the root `testpaths` and run under a plain
# `uv run`. code-graph-io and code-parser each resolve their own dependency
# closure, so they run under `--package` and name their test directory.

default: check

# Lint and format check — repo-wide, so every package is covered by both.
lint:
    uv run ruff check .
    uv run ruff format --check .

# Static types, strict — every package.
types:
    uv run mypy --strict packages/okf-io/src packages/okf-ext/src packages/code-graph-io/src packages/code-parser/src packages/code-wiki-okf/src

# Internal package boundaries (okf-ext README, "Boundaries"). Opt-in until CI
# exists: nothing enforces this but the person who runs it.
contracts:
    uv run lint-imports

# Full test suite, all four packages.
test:
    uv run pytest
    uv run --package code-graph-io pytest packages/code-graph-io/tests
    uv run --package code-parser pytest packages/code-parser/tests
    uv run --package code-wiki-okf pytest packages/code-wiki-okf/tests

# Branch coverage — GATED, per package.
#
# okf-io / okf-ext hold the original 95% floor. The margin there is thin, and
# `models.py` and `document.py` are where the slack went — they carry the debt.
#
# code-graph-io and code-parser gate at 90%. A failure here names only a global
# percentage, so start with the lowest-covered module and read `term-missing`.
cov:
    uv run pytest --cov=okf_io --cov=okf_ext --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package code-graph-io pytest packages/code-graph-io/tests --cov=code_graph_io --cov-branch --cov-report=term-missing --cov-fail-under=90
    uv run --package code-parser pytest packages/code-parser/tests --cov=code_parser --cov-branch --cov-report=term-missing --cov-fail-under=90
    uv run --package code-wiki-okf pytest packages/code-wiki-okf/tests --cov=code_wiki_okf --cov-branch --cov-report=term-missing --cov-fail-under=95

# Everything CI will run. `cov` runs every suite, so `test` is not repeated.
check: lint types contracts cov
