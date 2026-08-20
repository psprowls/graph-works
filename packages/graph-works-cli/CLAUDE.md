# packages/graph-works-cli

`gw` — a thin Typer CLI over `graph-works-core` (ADR-0013: all logic stays in core, this package
routes, parses, formats, and traces). Python >=3.12. Tests are pytest.

## Layout

- `src/graph_works_cli/cli.py` — the root Typer app: `-v/-vv` callback, `version`, `help [--json]`,
  and `add_typer()` for the five sub-apps.
- `src/graph_works_cli/workspace_resolution.py` — `resolve_workspace()` (D-002), the first call
  every sub-app makes.
- `src/graph_works_cli/exit_codes.py` — re-exports `code_graph_io.exit_codes` by explicit name.
  The one exit-code numbering, CLI-wide (ADR-0013 rule 1).
- `src/graph_works_cli/provenance.py` / `logging_config.py` — ported from legacy
  `graph_wiki_cli`, identifiers updated for this package.
- `src/graph_works_cli/{config,graph,wiki,work,util}_cli/main.py` — one namespace module per
  sub-app. `config_cli` currently contains the implemented `get`/`list` commands from the
  concurrent C5 work; the other namespace modules remain focused extension points that C2/C4/C3/C6
  fill independently without touching `cli.py` again.

## `graph-works-core` is a real dependency

As of the E6 epic's 2026-08-18 merge to `main` (this branch rebased onto it the same day),
`packages/graph-works-core` is a real, `py.typed`, `mypy --strict`-clean workspace member, and a
pinned dependency of this package (`graph-works-core>=0.1,<0.2` in `pyproject.toml`).
`workspace_resolution.py` imports it at module level, while `provenance.py` resolves it inside
`_source_checkout_root()` because only the provenance guard needs it — neither uses a deferred-import
workaround or `sys.modules` test injection. Tests patch the real
`graph_works_core.workspace.discovery.resolve` (via `monkeypatch.setattr` on the module reference
`workspace_resolution.py` holds, so the patch is visible through it) or, for `provenance.py`, exercise
the real `_source_checkout_root()` against the genuinely-installed package directly.

If you're reading this from a stale mental model of this package (an earlier revision of this
`CLAUDE.md`, or a plan snapshot written before 2026-08-18): `graph-works-core` not existing in this
worktree is history, not current state. Don't reintroduce the deferred-import pattern for a new
`graph_works_core` import in this package — import it plainly, without a missing-package fallback.
Function-local scope in `provenance.py` is an ownership choice, not a deferred-import workaround.

## `typer.core` vs `click` — a version-specific gotcha

This workspace resolves `typer` to a version whose `typer.main.get_command()` returns
`typer._click.core.Command` instances — typer's own internal fork of click's core classes, not the
real `click.core.Command`/`Option`/`Argument`/`Group`. `isinstance(param, click.Option)` never
matches under this version; it silently reports empty option/subcommand lists instead of erroring,
which is the dangerous failure mode. `cli.py`'s `help --json` introspection uses
`typer.core.TyperOption` / `TyperArgument` / `TyperGroup` instead — typer's own public re-exports of
the classes `get_command()` actually returns — plus one `typing.cast` where a real `click.Context`
needs to be constructed from typer's command object (structurally compatible at runtime; nominally
unrelated, so mypy needs telling). If you add another command that introspects the command tree
(C6's `gw util describe-surface --json`, D-001, is the obvious next one), reuse this pattern rather
than copying legacy `graph_wiki_cli/cli.py`'s `click.*` isinstance checks verbatim — they are broken
under this workspace's resolved `typer`.

## `--trace` is out of scope here (D-024)

The epic spec's original C1 description named a `--trace` writer. Corrected in this item's own
design spec: `graph_works_core.scan.commands` and `graph_works_core.query.commands` already write
JSONL traces unconditionally to `layout.cache_dir / "traces"` — there is no CLI flag in the legacy
CLI or in core today, so there is nothing for this package to build. `gw util trace <file>` (C6,
D-014) is the read-side renderer for whatever JSONL file the caller points it at, ported as-is; it
was never wired to a write-side flag, in legacy or here.

## D-003 — renderer-overlap audit

`code_graph_io.render` (`packages/code-graph-io/src/code_graph_io/render.py`) ships a generic
`render()` dispatcher (JSON / aligned-column, with row-capping), a `describe_block()` sectioned-spine
builder, and one `format_<kind>` per graph entity kind (package, app, path, repo, entry_point, suite,
dependency, builtin, agent_plugin, symbol), plus `format_matches` — built for exactly the
`GraphTarget`/`GraphResult` shape `gw graph`'s four verbs (`build`/`describe`/`find`/`export`) return.

**Recommendation, not a mechanical rule:**

- `gw graph` (C2) should delegate directly to `code_graph_io.render` / `format_*`.
- Every other sub-app (`wiki`/`work`/`config`/`util`) keeps bespoke formatting: their result types
  (`LintReport`, `IngestResult`, `ArchiveRun`, `RegisterResult`, etc.) don't fit
  `describe_block`'s graph-entity spine.
- `lint_format.py` (legacy's `format_wiki_lint`/`format_work_lint`) is **not** ported by this
  package's scaffold — those functions render `LintReport`/work-lint result shapes owned by C4/C3
  respectively, and neither exists yet. Built by whichever of C3/C4/C6 first needs it.

If C2 finds graph-specific formatting that genuinely doesn't fit `describe_block`'s spine once it
implements for real, not delegating remains fine — this is a starting recommendation, not a frozen
contract.
