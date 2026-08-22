# code-wiki-okf

Generates and updates a standalone OKF v0.2 bundle from the shared code
graph — see `wiki/work/2026-08-07-epic-code-wiki-okf-package/01-design-spec.md`
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
ADR-0033). Point `sync`/`validate` at that same directory with their own
`--config-dir` on every later invocation, or point them at a real
`workspace.yaml` with `--config-path` instead — see below.

    code-wiki-okf sync <bundle-root> [--dry-run] [--config-dir <path>] [--config-path <path>]

Syncs the bundle's entity lanes (Package, App, TestSuite, Dependency,
AgentPlugin, Repository) against the shared code graph named in
`workspace.yaml`'s `repositories` block: renders each entity's owned
frontmatter and generated sections, creates pages for new entities, deletes
pages for vanished ones (guarded — a page with hand-edited prose is
declined, not deleted), reconciles every touched lane's `index.md`, and
appends one `log.md` entry. Writes by default; pass `--dry-run` to print
each lane's plan instead. This matches `init`'s convention — the flag opts
out of writing, and there is no `--no-dry-run`.

`--config-path` names where that `workspace.yaml` lives; it defaults to
`<bundle-root>/workspace.yaml`, but a bundle nested inside a graph-works
workspace can point it at that workspace's own manifest instead, to read
the same `repositories`/`ignore`/`state_gate` blocks `graph-works-core`
already resolved. Since `init` no longer seeds one, a bundle that was
`init`'d but never given a `workspace.yaml` — by hand or via
`--config-path` — surfaces `sync`/`validate` failures as an uncaught
`OSError` rather than a clean refusal; a known, deliberately unfixed gap.

Package, App, TestSuite and AgentPlugin pages nest under their owning
repository — `repositories/<repo>/packages/<slug>.md`, and so on — so two
repositories declaring a same-named entity never collide on one flat,
ecosystem-wide path. Repository (`repositories/<repo>.md`) and Dependency
(`dependencies/<slug>.md`, ecosystem-wide by design) keep their own
bundle-root placement. There is no migration for a bundle synced by an
older `code-wiki-okf`: delete the bundle and re-`sync` from empty rather
than move pages by hand.

    code-wiki-okf validate <bundle-root> [--config-dir <path>] [--config-path <path>] [--strict]

Reports drift and conformance findings; never writes. Runs okf-io's own
catalog plus five house rules: `sync.*` (commit-derived staleness),
`schemas.*`, `sections.*`, `tags.*`, and `placement.*`.

The `placement.*` codes are `okf_ext.placement`'s, composed here from the
bundle's schemas narrowed to types a flat directory-prefix check can still
express — `placement_rule(placement_directories(schema_set), depth=ENTITY_DEPTH,
severity="error")`. Repository, File and Dependency are checked; Package, App,
TestSuite and AgentPlugin are excluded because they nest under their owning
`repositories/<repo>/` directory. They cover the page-to-lane correspondence
`sync` assumes and never checks. The capability defaults them to `warn`; this
package raises both to **error**, because neither is drift a later run
reconciles away:

| code | what it catches |
|---|---|
| `placement.directory-mismatch` | the page's directory disagrees with its type's `x-okf-directory`. Deletion is scoped by lane prefix, so a misplaced page is outside `prune_lane`'s reach forever while sync keeps writing the page its type calls for — a duplicate no run can resolve. |
| `placement.duplicate-resource` | two or more pages claim the same `resource:`. Find-by-resource keeps the first in bundle order, so every other one is unreachable by resource and invisible to deletion. |

Exits non-zero on any error-severity finding, and on any `sync.*` finding
regardless of severity. `--strict` promotes every warning to an error first.
