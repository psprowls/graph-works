# code-wiki-okf

Generates and updates a standalone OKF v0.2 bundle from the shared code
graph — see `.works/okf/work/_archive/epic-code-wiki-okf-package/references/01-design.md`
in the graph-wiki workspace for the full architecture.

## Usage

    code-wiki-okf init <bundle-root> [--dry-run] [--config-dir <path>]

Installs this package's files into a bundle, additively and idempotently:
root `index.md`, `log.md` and `tags.yaml` (the shared scaffold every OKF
bundle has, whoever installs into it first), the seven
`schema/*.schema.json` files, and the seven `sections/*.yaml` declarations.
Running it again is a no-op; running it against a bundle another
`code-wiki-okf`-adjacent package already created installs alongside that
package's own files rather than refusing. The refusal narrows to the
per-file case — a file this package owns already exists with content it did
not write — so one hand-edited seed is reported by name without blocking its
neighbours. `--config-dir` relocates the declaration files
(`schema/`, `sections/`, `tags.yaml`) to a directory shared across several
bundles, for this run only — it is not persisted anywhere, since `init` no
longer seeds a `workspace.yaml` of its own (2026-08-22 tech-debt item;
ADR 2026-08-22-single-workspace-config). Point `sync`/`validate` at that same directory with their own
`--config-dir` on every later invocation, or point them at a real
`workspace.yaml` with `--config-path` instead — see below.

    code-wiki-okf sync <bundle-root> [--dry-run] [--config-dir <path>] [--config-path <path>]

Syncs all seven code-wiki types (Repository, Package, App, AgentPlugin,
TestSuite, File, and Dependency) against the shared code graph and repositories
named in `workspace.yaml`. The composite sync plans and preflights the entity
and File lanes before applying either one, then reloads the post-write bundle
to reconcile catalogs against what is actually on disk. Fully generated stale
pages may be deleted; stale pages with human prose are retained and reported.
Writes by default; pass `--dry-run` to print the complete plan instead. This
matches `init`'s convention — the flag opts out of writing, and there is no
`--no-dry-run`.

`--config-path` names where that `workspace.yaml` lives; it defaults to
`<bundle-root>/workspace.yaml`, but a bundle nested inside a graph-works
workspace can point it at that workspace's own manifest instead, to read
the same `repositories`/`ignore`/`state_gate` blocks `graph-works-core`
already resolved. Since `init` no longer seeds one, a bundle that was
`init`'d but never given a `workspace.yaml` — by hand or via
`--config-path` — surfaces `sync`/`validate` failures as an uncaught
`OSError` rather than a clean refusal; a known, deliberately unfixed gap.

Graph resource identity decides placement; the physical location of the
workspace does not. Canonical members follow this matrix:

| Type | Canonical member |
| --- | --- |
| Repository | `code-graph/<repo>.md` |
| Package | `code-graph/<repo>/entities/packages/<slug>.md` |
| App | `code-graph/<repo>/entities/apps/<slug>.md` |
| AgentPlugin | `code-graph/<repo>/entities/agent-plugins/<slug>.md` |
| TestSuite | `code-graph/<repo>/entities/test-suites/<slug>.md` |
| File | `code-graph/<repo>/file-system/<source-path>.md` |
| Dependency | `code-graph/<repo>/entities/dependencies/<ecosystem>/<slug>.md` |

The catalog indexes follow the same tree:

```
code-graph/index.md                                   Repositories
code-graph/<repo>.md                                  Repository page
code-graph/<repo>/index.md                            stub: Repository link + Subdirectories
code-graph/<repo>/entities/index.md                   Packages, Apps, Agent Plugins, Test Suites, Dependencies
code-graph/<repo>/entities/{packages,apps,agent-plugins,test-suites}/<slug>.md
code-graph/<repo>/entities/dependencies/<ecosystem>/<slug>.md
code-graph/<repo>/file-system/<repo-relative-path>.md
```

The bundle root `index.md` lists repositories only; there is no top-level
`repositories/`, `dependencies/`, `packages/`, `apps/`, `agent-plugins/` or
`test-suites/` lane. A repository named `index` is refused at config load,
because it would collide with `code-graph/index.md`. Dependencies are
repository-owned: one Dependency per (repository, dependency),
`dependency:<org>/<repo>/<ecosystem>/<name>` (e.g. `dependency:acme/web/npm/react`);
`used_by` and `versions_in_use` describe that repository only, and the
implementing Package page aggregates across repositories. A scoped npm name is
slugged (`@babel/core` → `@babel__core`).

`x-okf-directory` in a type schema declares its lane segment, not a complete
member path (`code-graph/` for Repository, `file-system/` for File, `packages/`,
`apps/`, `agent-plugins/`, `test-suites/` and `dependencies/` for the rest). `code_wiki_okf.placement` composes the repository or ecosystem
context, constructs canonical IDs, and validates all seven types; generic
`okf-ext` placement remains vocabulary-neutral.

Every distributable manifest Package also has one ecosystem-qualified
Dependency. Consumer `used_by` edges always target that Dependency,
`depends_on_package` keeps direct internal topology, and zero or more
`implemented_by` edges identify Package implementations present in the
workspace. Multiple implementations are retained and reported as ambiguous.

This pre-1.0 cutover has no migration, legacy aliases, or redirects. Delete
bundles generated by older releases and scan or sync from empty; do not move
old pages by hand.

    code-wiki-okf validate <bundle-root> [--config-dir <path>] [--config-path <path>] [--strict]

Reports drift and conformance findings; never writes. Runs okf-io's own
catalog plus five house rules: `sync.*` (commit-derived staleness),
`schemas.*`, `sections.*`, `tags.*`, and the code-wiki-owned
`placement_rule(severity="error")`. Placement validation covers all seven
types with the same resource-derived policy used by writers, links, pruning,
and catalogs. Both placement findings are errors because routine sync refuses,
rather than silently repairing, a page at the wrong canonical member:

| code | what it catches |
|---|---|
| `placement.directory-mismatch` | the page is not at the canonical member derived from its type and graph resource. |
| `placement.duplicate-resource` | two or more pages claim the same `resource:`. |

Exits non-zero on any error-severity finding, and on any `sync.*` finding
regardless of severity. `--strict` promotes every warning to an error first.
