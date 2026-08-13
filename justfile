# Workspace task runner — okf-io, okf-ext, code-graph-io, code-wiki-okf,
# work-tracker-okf, config-io, models-io, subagents-io, doc-wiki-okf.
# Each recipe is exactly the command a future CI job will call.
#
# okf-io and okf-ext share the root `testpaths` and run under a plain
# `uv run`. code-graph-io resolves its own dependency closure, so it runs
# under `--package` and names its test directory.

default: check

# Lint and format check — repo-wide, so every package is covered by both.
lint:
    uv run ruff check .
    uv run ruff format --check .

# Static types, strict — every package.
types:
    uv run mypy --strict packages/okf-io/src packages/okf-ext/src
    uv run --package code-graph-io mypy --strict packages/code-graph-io/src
    uv run --package code-wiki-okf mypy --strict packages/code-wiki-okf/src
    uv run --package work-tracker-okf mypy --strict packages/work-tracker-okf/src
    uv run --package config-io mypy --strict packages/config-io/src
    uv run --package models-io --extra bedrock --extra vercel mypy --strict packages/models-io/src
    uv run --package subagents-io mypy --strict packages/subagents-io/src
    uv run --package doc-wiki-okf mypy --strict packages/doc-wiki-okf/src

# Internal package boundaries (okf-ext README, "Boundaries"). Opt-in until CI
# exists: nothing enforces this but the person who runs it.
contracts:
    uv run lint-imports

# Full test suite, every package.
test:
    uv run pytest
    uv run --package code-graph-io pytest packages/code-graph-io/tests
    uv run --package code-wiki-okf pytest packages/code-wiki-okf/tests
    uv run --package work-tracker-okf pytest packages/work-tracker-okf/tests
    uv run --package config-io pytest packages/config-io/tests
    uv run --package models-io --extra bedrock --extra vercel pytest packages/models-io/tests
    uv run --package subagents-io pytest packages/subagents-io/tests
    uv run --package doc-wiki-okf pytest packages/doc-wiki-okf/tests

# Branch coverage — GATED, per package.
#
# okf-io / okf-ext hold the original 95% floor. The margin there is thin, and
# `models.py` and `document.py` are where the slack went — they carry the debt.
#
# code-graph-io gates at 90% (its parser subtree is folded into this one
# gate, not a second one). A failure here names only a global percentage, so
# start with the lowest-covered module and read `term-missing`.
cov:
    uv run pytest --cov=okf_io --cov=okf_ext --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package code-graph-io pytest packages/code-graph-io/tests --cov=code_graph_io --cov-branch --cov-report=term-missing --cov-fail-under=90
    uv run --package code-wiki-okf pytest packages/code-wiki-okf/tests --cov=code_wiki_okf --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package work-tracker-okf pytest packages/work-tracker-okf/tests --cov=work_tracker_okf --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package config-io pytest packages/config-io/tests --cov=config_io --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package models-io --extra bedrock --extra vercel pytest packages/models-io/tests --cov=models_io --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package subagents-io pytest packages/subagents-io/tests --cov=subagents_io --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package doc-wiki-okf pytest packages/doc-wiki-okf/tests --cov=doc_wiki_okf --cov-branch --cov-report=term-missing --cov-fail-under=95

# Everything CI will run. `cov` runs every suite, so `test` is not repeated.
check: lint types contracts cov
