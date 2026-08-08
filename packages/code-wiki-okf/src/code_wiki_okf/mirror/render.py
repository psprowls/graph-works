"""Render one tracked file's frontmatter + generator `Render` -- rich when
`describe_path()` knows it, minimal otherwise.

Rich: owned keys `language` (from `code_graph_io.source_meta.extension_languages`
-- `describe_path()`'s `PathDescription` carries no language field of its own),
`package` (`GraphReader.containing_package`), `role_flags` (the flags that are
`true`, as a list -- `File.schema.json` declares the key as an array). Four
generated sections: `Symbols` from `description.children`, `Exports` from
`description.exports` (already the `list[ExportRecord]` shape this section
renders from -- `describe_path()` computes it internally, so reusing it costs
nothing extra), and `Imports` / `Imported By` from the reader's own dedicated
queries (`reader.imports`, `reader.imported_by`) -- `PathDescription.imports`
is a `list[NodeRecord]` (a lighter projection used for the containment view),
while `reader.imports()` returns the richer `ImportRecord` shape the Imports
section renders from. `PathDescription` carries no `imported_by` equivalent at
all, so that section has no other source.

Minimal: no owned keys at all -- not written as empty/null, simply absent from
the frontmatter dict, so `key_edits` (which only touches keys it is handed)
never claims them. An empty `Render()` -- see `okf_ext.generators.Render`'s own
docstring -- and `plan_regenerate` already treats that as "nothing to do"
against a target with no usable declaration.

Universal frontmatter (`type`, `title`, `resource`) is supplied on every page,
rich or minimal -- these are the page's own identity, not something a
generator has opinions about, so they go straight into the returned
`frontmatter` dict and never into `Render.frontmatter`. `generated`,
`last_updated_commit`, and `tokens` are this lane's write-time stamp, supplied
on every page too, but they *are* `File.yaml`'s declared `provenance` keys --
so for a rich page (the only kind `plan_regenerate` ever sees, per
`mirror/plan.py`'s `if render.sections` guard) they also go into
`Render.frontmatter`, alongside whichever of `language`/`package`/`role_flags`
this rich page has, so a later regeneration pass restamps provenance and
re-verifies owned keys instead of only refreshing section bodies.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from code_graph_io import ExportRecord, ImporterRecord, ImportRecord, NodeRecord
from code_graph_io.handle import GraphReader
from code_graph_io.source_meta import extension_languages
from code_graph_io.tokens import count_tokens
from okf_ext.generators import Render

from code_wiki_okf import __version__
from code_wiki_okf.config import RepoConfig
from code_wiki_okf.provenance import generated_value, last_updated_commit_value, tokens_value

_NONE_MARKER = "_(none)_"
_GENERATOR_ID = f"code-wiki-okf/{__version__}"


def _resource(repo: RepoConfig, rel_path: str) -> str:
    return f"file:{repo.name}/{rel_path}"


def _title(rel_path: str) -> str:
    return Path(rel_path).name


def _render_symbols(children: list[NodeRecord]) -> str:
    if not children:
        return _NONE_MARKER
    lines = [f"- `{node.name}` ({node.kind})" + (f" -- line {node.line}" if node.line else "") for node in children]
    return "\n".join(lines)


def _render_imports(imports: list[ImportRecord]) -> str:
    if not imports:
        return _NONE_MARKER
    lines = [f"- `{record.name}`" + (f" -> `{record.path}`" if record.path else " (unresolved)") for record in imports]
    return "\n".join(lines)


def _render_exports(exports: list[ExportRecord]) -> str:
    if not exports:
        return _NONE_MARKER
    lines = [
        f"- `{record.name}` ({record.kind})" + (f", line {record.line}" if record.line else "") for record in exports
    ]
    return "\n".join(lines)


def _render_imported_by(importers: list[ImporterRecord]) -> str:
    if not importers:
        return _NONE_MARKER
    lines = [
        f"- `{record.path}`" + (f" -- {', '.join(record.symbols)}" if record.symbols else "") for record in importers
    ]
    return "\n".join(lines)


def render_file(
    reader: GraphReader,
    repo: RepoConfig,
    rel_path: str,
    *,
    at: datetime,
    sha: str,
) -> tuple[dict[str, Any], Render]:
    """Frontmatter + `Render` for one tracked path. Never raises on content."""
    frontmatter: dict[str, Any] = {
        "type": "File",
        "title": _title(rel_path),
        "resource": _resource(repo, rel_path),
    }

    description = reader.describe_path(path=rel_path)
    if description is None:
        frontmatter["generated"] = generated_value(by=_GENERATOR_ID, at=at)
        frontmatter["last_updated_commit"] = last_updated_commit_value(sha)
        frontmatter["tokens"] = tokens_value(count_tokens(""))
        return frontmatter, Render()

    language = extension_languages().get(Path(rel_path).suffix)
    if language is not None:
        frontmatter["language"] = language
    package = reader.containing_package(path=rel_path)
    if package is not None:
        frontmatter["package"] = package
    if description.role_flags:
        frontmatter["role_flags"] = [flag for flag, value in description.role_flags.items() if value]

    symbols = _render_symbols(description.children)
    imports = _render_imports(reader.imports(path=rel_path))
    exports = _render_exports(description.exports)
    imported_by = _render_imported_by(reader.imported_by(path=rel_path, depth=1))
    tokens_source = "\n".join((symbols, imports, exports, imported_by))

    frontmatter["generated"] = generated_value(by=_GENERATOR_ID, at=at)
    frontmatter["last_updated_commit"] = last_updated_commit_value(sha)
    frontmatter["tokens"] = tokens_value(count_tokens(tokens_source))

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
