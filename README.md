# graph-works

A uv workspace for the OKF tooling packages. The root is a workspace root only —
it is not itself distributable.

## Members

- [`packages/okf-io`](packages/okf-io) — read, derive from, and write back OKF
  v0.2 concept documents. Tier 1: the spec, nothing else.
- [`packages/okf-ext`](packages/okf-ext) — beyond-spec capabilities over any
  bundle. Tier 2: extends `okf-io`, never modifies it. Tags, schema
  validation, table read/splice, render correctness, bundle health and search
  today; budgeted context assembly later.
- [`packages/code-graph-io`](packages/code-graph-io) — code-graph core for the
  graph-works ecosystem: SQLite store, tree-sitter-backed source parsing,
  manifest scanning, and read-only queries.
- [`packages/workflow-local`](packages/workflow-local) — a subprocess
  `DispatchBackend` for `subagents-io`: a durable per-session ledger, JSONL
  event and reply files, and a reaper that settles a silent child.

### Package naming

The suffix is a band, and a permission:

| Suffix | Band | What it may couple to |
|---|---|---|
| `-io` | 1, foundation | stdlib and declared third party only — never a sibling package, never a workspace path; `os` only where a package's own boundary test names a narrow, tested exemption |
| `-okf` | 2, OKF-aware | band 1 plus the OKF document model |
| `-core` | 3, application | everything below it; exactly one package knows what a workspace is |
| `workflow-<backend>` | beside band 1 | **vendor and system coupling lives here** — one package per backend, `workflow-orca` beside `workflow-local` |

`workflow-<backend>` exists so the band-1 seam can ship with a real
implementation without the foundation carving out an exemption to hold it.

## Platform

| Channel | Supported | How to check |
|---|---|---|
| macOS, Linux | yes | `gw util platform` |
| Windows via WSL | yes (WSL is Linux) | `gw util platform` |
| Native Windows (`python.exe`, Git Bash, PowerShell) | committed, not yet | `gw util platform` |

This table is coarse and slow-moving on purpose. The per-capability truth —
durability tier, dispatch backend, file lock, process control — is *derived
at runtime* by `gw util platform`, not restated here, so it cannot drift out
of sync with what the machinery actually reports.

A package `README.md` with no `## Platform` section is platform-neutral —
that silence is itself the claim, not an omission. ADR-0021 ("Windows is
supported via WSL; native Windows is deferred"), rule 3, is the record
behind this commitment.

## Checks

Every recipe below is exactly the command a future CI job will call. CI itself is
deferred until the repository has a remote (child spec §3, decision 1).

| Recipe | Command |
|---|---|
| `just lint` | `uv run ruff check . && uv run ruff format --check .` |
| `just types` | `uv run mypy --strict` over all four packages' `src` trees |
| `just contracts` | `uv run lint-imports` |
| `just test` | `uv run pytest`, plus `code-graph-io`'s own suite |
| `just cov` | branch coverage per package, gated |
| `just check` | `lint` + `types` + `contracts` + `cov` |

Coverage is **gated at 95% branch coverage** for `okf-io`/`okf-ext` and **90%**
for `code-graph-io` — each package's suite runs and is gated
separately; see the `justfile` for the exact commands. `just contracts` checks
okf-ext's internal package boundaries; both it and the coverage gate are
opt-in in the sense that CI does not yet run them — CI is deferred until the
repository has a remote.

If `just` is not installed, run the commands from the `justfile` directly.

## Versioning and release (dormant)

Per ADR-0007, packages version independently with **static** `version` fields —
never `hatch-vcs`. The release machinery is documented here but not wired up:

- Tag scheme: `<pkg>-vX.Y.Z`, e.g. `okf-io-v0.1.0`.
- Pre-1.0 policy: **minor = breaking**, **patch = compatible**.
- Future sibling packages declare real bounds, e.g. `okf-io>=0.1,<0.2`.

No publish workflow exists. Adding one is mechanical once a remote exists.
