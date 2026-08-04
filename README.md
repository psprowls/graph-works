# agent-workspace

A uv workspace for the OKF tooling packages. The root is a workspace root only —
it is not itself distributable.

## Members

- [`packages/okf-io`](packages/okf-io) — read, derive from, and write back OKF
  v0.2 concept documents.

## Checks

Every recipe below is exactly the command a future CI job will call. CI itself is
deferred until the repository has a remote (child spec §3, decision 1).

| Recipe | Command |
|---|---|
| `just lint` | `uv run ruff check . && uv run ruff format --check .` |
| `just types` | `uv run mypy --strict packages/okf-io/src` |
| `just test` | `uv run pytest` |
| `just cov` | `uv run pytest --cov=okf_io --cov-branch --cov-report=term-missing` |
| `just check` | all of the above |

Coverage is **reported, not gated** (child spec §3, decision 2). The ≥95% branch
target stands; enforcing it is a later child.

If `just` is not installed, run the commands in the right-hand column directly.

## Versioning and release (dormant)

Per ADR-0007, packages version independently with **static** `version` fields —
never `hatch-vcs`. The release machinery is documented here but not wired up:

- Tag scheme: `<pkg>-vX.Y.Z`, e.g. `okf-io-v0.1.0`.
- Pre-1.0 policy: **minor = breaking**, **patch = compatible**.
- Future sibling packages declare real bounds, e.g. `okf-io>=0.1,<0.2`.

No publish workflow exists. Adding one is mechanical once a remote exists.
