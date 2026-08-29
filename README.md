# graph-works

A uv workspace for the graph-works tooling packages. The root is a workspace
root only — it is not itself distributable. `plugins/graph-works` sits
alongside it: a Claude Code plugin vendored verbatim as a `git subtree` of
[obra/superpowers](https://github.com/obra/superpowers) (see
`plugins/SYNC.md`), not a workspace member and not Python.

## Members

- [`packages/okf-io`](packages/okf-io) — read, derive from, and write back OKF
  v0.2 concept documents. Band 1: the spec, nothing else.
- [`packages/okf-ext`](packages/okf-ext) — beyond-spec capabilities over any
  bundle. Band 2: extends `okf-io`, never modifies it. Tags, schema
  validation, table read/splice, render correctness, bundle health and search
  today; budgeted context assembly later.
- [`packages/config-io`](packages/config-io) — schema-driven, git-like scoped
  configuration over YAML: precedence resolution, validated writes, JSON
  projection.
- [`packages/models-io`](packages/models-io) — guarded Bedrock and Vercel AI
  Gateway model constructors, chat and embeddings: an access-denied taxonomy,
  content normalization, and caller-supplied credentials.
- [`packages/subagents-io`](packages/subagents-io) — a bounded async fan-out
  pool for role-bound model dispatch: per-item failure isolation, a JSONL
  trace record per invocation, and opt-in USD cost accounting.
- [`packages/code-graph-io`](packages/code-graph-io) — code-graph core for the
  graph-works ecosystem: SQLite store, tree-sitter-backed source parsing,
  manifest scanning, and read-only queries.
- [`packages/workflow-local`](packages/workflow-local) — a subprocess
  `DispatchBackend` for `subagents-io`: a durable per-session ledger, JSONL
  event and reply files, and a reaper that settles a silent child.
- [`packages/workflow-orca`](packages/workflow-orca) — the Orca
  `DispatchBackend` for `subagents-io`: launch, enumerate, supervise and
  resume workers on an Orca Run.
- [`packages/code-wiki-okf`](packages/code-wiki-okf) — generates and updates a
  standalone OKF v0.2 bundle from the shared code graph.
- [`packages/doc-wiki-okf`](packages/doc-wiki-okf) — the documentation-wiki
  lane over OKF v0.2: source reading, ingest briefs, and proposals.
- [`packages/work-tracker-okf`](packages/work-tracker-okf) — work-item
  tracking as an OKF v0.2 lane: declarations, vocabulary, and the item view.
- [`packages/graph-works-core`](packages/graph-works-core) — what a workspace
  is: discovery, the layout object, the manifest, and init. Band 3: the one
  package that knows what a workspace is.
- [`packages/graph-works-cli`](packages/graph-works-cli) — `gw`, the
  graph-works CLI: a thin Typer interface over `graph-works-core` (ADR-0013).

Each package carries its own `README.md` and an `AGENTS.md` (reached from a
`CLAUDE.md` that just `@`-imports it) with its module layout and gotchas. The
root [`AGENTS.md`](AGENTS.md) covers what spans packages.

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

## Checks

Every recipe below is exactly the command a future CI job will call. No CI
workflow exists yet — enforcement is local, by design (ADR-0010).

| Recipe | What it does |
|---|---|
| `just sync` | `uv sync --all-packages` — provisions every member's own deps, not just the root's |
| `just normalization` | Unicode-normalization check on tracked filenames |
| `just lint` | `uv run ruff check . && uv run ruff format --check .` |
| `just types` | `uv run mypy --strict`, once per package |
| `just contracts` | `uv run lint-imports` — the workspace's band/suffix boundaries plus okf-ext's internal capability boundaries |
| `just test` | `uv run pytest`, plus one run per non-okf-io/okf-ext package under `uv run --package <name>` |
| `just cov` | branch coverage, gated per package (95% for most, 90% for `code-graph-io`) |
| `just subtree-base` | asserts the `plugins/graph-works` subtree merge-base is intact |
| `just test-plugin` | the offline, code-executing subset of the vendored plugin's own test suites |
| `just check` | `sync` + `subtree-base` + `normalization` + `lint` + `types` + `contracts` + `cov` + `test-plugin` — the full gate |

A coverage failure reports only a global percentage per package; start with
the lowest-covered module and read `term-missing`. `just audit-delta` and
`just plugin-contract` are advisory checks on the vendored plugin, run
explicitly rather than gated — see `plugins/SYNC.md`.

If `just` is not installed, run the commands from the `justfile` directly.

## Versioning and release (dormant)

Per ADR-0007, packages version independently with **static** `version` fields —
never `hatch-vcs`. The release machinery is documented here but not wired up:

- Tag scheme: `<pkg>-vX.Y.Z`, e.g. `okf-io-v0.1.0`.
- Pre-1.0 policy: **minor = breaking**, **patch = compatible**.
- Future sibling packages declare real bounds, e.g. `okf-io>=0.1,<0.2`.

No publish workflow exists yet.
