"""Render one tracked file's frontmatter + generator `Render` -- rich when
the exact repository-scoped File URI is in the graph, minimal otherwise.

Rich: owned keys `language` (from `code_graph_io.source_meta.extension_languages`
-- `FileDescription` carries no language field of its own), `package` (from
the exact File's containment edge), `role_flags` (the flags that are
`true`, as a list -- `File.schema.json` declares the key as an array). Four
generated sections: `Symbols`, `Exports`, `Imports`, and `Imported By` from
that same dossier. The single URI-scoped read prevents identical relative
paths in sibling repositories from contributing package, symbol, or
relationship data.

Minimal: no owned keys at all -- not written as empty/null, simply absent from
the frontmatter dict, so `key_edits` (which only touches keys it is handed)
never claims them. An empty `Render()` -- see `okf_ext.generators.Render`'s own
docstring -- and `plan_regenerate` already treats that as "nothing to do"
against a target with no usable declaration.

Universal frontmatter (`type`, `title`, `resource`) is supplied on every page,
rich or minimal -- these are the page's own identity, not something a
generator has opinions about, so they go straight into the returned
`frontmatter` dict and never into `Render.frontmatter`. `generated` and
`last_updated_commit` are this lane's write-time stamp, supplied on every page
too, but they *are* `File.yaml`'s declared `provenance` keys -- so for a rich
page (the only kind `plan_regenerate` ever sees, per `mirror/plan.py`'s
`if render.sections` guard) they also go into `Render.frontmatter`, alongside
whichever of `language`/`package`/`role_flags` this rich page has, so a later
regeneration pass restamps provenance and re-verifies owned keys instead of
only refreshing section bodies. `tokens` is also a declared `provenance` key
but is not stamped by this lane -- `run_tokens_update` (graph-works-core) is
its sole writer; see 2026-08-19-tech-debt-tokens-metric-proxy-string.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from code_graph_io import ExportRecord, ImporterRecord, ImportRecord, NodeRecord
from code_graph_io.handle import GraphReader
from code_graph_io.source_meta import extension_languages
from okf_ext.generators import Render

from code_wiki_okf import __version__
from code_wiki_okf.placement import PlacementContext, PlacementError, canonical_member, context_from_resource
from code_wiki_okf.provenance import generated_value, last_updated_commit_value

_NONE_MARKER = "_(none)_"
_GENERATOR_ID = f"code-wiki-okf/{__version__}"


def _title(rel_path: str) -> str:
    return Path(rel_path).name


def _repository_identity(context: PlacementContext) -> tuple[str, str]:
    payload = context.resource.removeprefix("file:")
    parts = payload.split("/", 2)
    if len(parts) != 3:
        raise PlacementError(resource=context.resource, reason="File resource has no repository identity")
    return parts[0], parts[1]


def _file_link(context: PlacementContext, path: str, *, label: str) -> str:
    organization, repository = _repository_identity(context)
    related = context_from_resource("File", f"file:{organization}/{repository}/{path}")
    return f"[{label}](/{canonical_member(related)})"


def _package_link(context: PlacementContext, name: str) -> str:
    organization, repository = _repository_identity(context)
    package = context_from_resource("Package", f"pkg:{organization}/{repository}/{name}")
    return f"[{name}](/{canonical_member(package)})"


def _render_symbols(children: list[NodeRecord]) -> str:
    if not children:
        return _NONE_MARKER
    lines = [f"- `{node.name}` ({node.kind})" + (f" -- line {node.line}" if node.line else "") for node in children]
    return "\n".join(lines)


def _render_imports(imports: list[ImportRecord], context: PlacementContext) -> str:
    if not imports:
        return _NONE_MARKER
    lines = [
        f"- `{record.name}`"
        + (f" -> {_file_link(context, record.path, label=record.path)}" if record.path else " (unresolved)")
        for record in imports
    ]
    return "\n".join(lines)


def _render_exports(exports: list[ExportRecord]) -> str:
    if not exports:
        return _NONE_MARKER
    lines = [
        f"- `{record.name}` ({record.kind})" + (f", line {record.line}" if record.line else "") for record in exports
    ]
    return "\n".join(lines)


def _render_imported_by(importers: list[ImporterRecord], context: PlacementContext) -> str:
    if not importers:
        return _NONE_MARKER
    lines = [
        f"- {_file_link(context, record.path, label=record.path)}"
        + (f" -- {', '.join(record.symbols)}" if record.symbols else "")
        for record in importers
    ]
    return "\n".join(lines)


def render_file(
    reader: GraphReader,
    context: PlacementContext,
    *,
    at: datetime,
    sha: str,
) -> tuple[dict[str, Any], Render]:
    """Frontmatter + `Render` for one tracked path. Never raises on content."""
    canonical_member(context)
    rel_path = context.source_path
    if rel_path is None:
        raise PlacementError(resource=context.resource, reason="File placement context has no source path")
    frontmatter: dict[str, Any] = {
        "type": "File",
        "title": _title(rel_path),
        "resource": context.resource,
    }

    description = reader.describe_file(uri=context.resource)
    if description is None:
        frontmatter["generated"] = generated_value(by=_GENERATOR_ID, at=at)
        frontmatter["last_updated_commit"] = last_updated_commit_value(sha)
        return frontmatter, Render()

    language = extension_languages().get(Path(rel_path).suffix)
    if language is not None:
        frontmatter["language"] = language
    if description.package is not None:
        package_name, _package_uri = description.package
        frontmatter["package"] = _package_link(context, package_name)
    if description.role_flags:
        frontmatter["role_flags"] = [flag for flag, value in description.role_flags.items() if value]

    symbols = _render_symbols(description.children)
    imports = _render_imports(description.imports, context)
    exports = _render_exports(description.exports)
    imported_by = _render_imported_by(description.imported_by, context)

    frontmatter["generated"] = generated_value(by=_GENERATOR_ID, at=at)
    frontmatter["last_updated_commit"] = last_updated_commit_value(sha)

    render_frontmatter = {key: value for key, value in frontmatter.items() if key not in {"type", "title", "resource"}}
    render = Render(
        frontmatter=render_frontmatter,
        sections={
            "Symbols": symbols,
            "Imports": imports,
            "Exports": exports,
            "Imported By": imported_by,
        },
    )
    return frontmatter, render
