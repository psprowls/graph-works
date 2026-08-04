# okf-io workspace task runner.
# Each recipe is exactly the command a future CI job will call.

default: check

# Lint and format check
lint:
    uv run ruff check .
    uv run ruff format --check .

# Static types, strict
types:
    uv run mypy --strict packages/okf-io/src

# Test suite
test:
    uv run pytest

# Branch coverage — GATED at 95% (child 3 spec §9; supersedes child 1's
# reported-not-gated decision, which the epic's cross-cutting requirements
# always called for).
#
# The margin over the floor is thin, and `models.py` and `document.py` are
# where the slack went — they carry the coverage debt. A failure here names
# only a global percentage, so start with them and read `term-missing`.
cov:
    uv run pytest --cov=okf_io --cov-branch --cov-report=term-missing --cov-fail-under=95

# Everything CI will run. `cov` runs the suite, so `test` is not repeated.
check: lint types cov
