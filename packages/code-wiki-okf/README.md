# code-wiki-okf

Generates and updates a standalone OKF v0.2 bundle from the shared code
graph — see `wiki/work/2026-08-07-epic-code-wiki-okf-package/01-design-spec.md`
in the graph-wiki workspace for the full architecture.

## Usage

    code-wiki-okf init <bundle-root> [--dry-run]

Initializes a fresh, empty-but-valid OKF v0.2 bundle: root `index.md`,
`log.md`, `_repositories.yaml`, the seven `_schema/*.schema.json` files, the
seven `_sections/*.yaml` declarations, and `_tags.yaml`.

    code-wiki-okf sync <bundle-root> [--dry-run/--no-dry-run]

Syncs the bundle's entity lanes (Package, App, TestSuite, Dependency,
AgentPlugin, Repository) against the shared code graph named in
`_repositories.yaml`: renders each entity's owned frontmatter and generated
sections, creates pages for new entities, deletes pages for vanished ones
(guarded — a page with hand-edited prose is declined, not deleted),
reconciles every touched lane's `index.md`, and appends one `log.md` entry.
Defaults to `--dry-run` (touches nothing); pass `--no-dry-run` to apply.
