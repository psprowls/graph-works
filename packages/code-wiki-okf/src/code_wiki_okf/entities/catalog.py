"""The two catalog renders: what the bundle contains, and what a repository
contains.

Pure -- a `Bundle` in, a section body out. No graph reads, no clock, no I/O,
matching `entities/render.py`'s style.

**Root-absolute markdown links, never wikilinks.** The convention
`entities/render.py`'s `_files_section` already set, and here it is
load-bearing: `okf_io.LinkGraph` cannot see a wikilink, so a catalog written
in them would be invisible to backlinks, broken-link validation and
traversal -- and `okf_io.update_index` would not recognise its own entries.

**The one-liner is each page's own `description`, carried and never
claimed.**
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from okf_ext.render import escape_angle_brackets
from okf_io import Bundle

#: What this module writes for an empty list.
_NONE_PLACEHOLDER = "_(none)_"

#: `## Contents` H3 groups, in fixed render order, and the `type` each holds.
#:
#: `Dependency`, `File` and `Repository` are absent: a dependency is
#: ecosystem-wide rather than repo-scoped, a mirrored file already has its
#: own generated `## Files` section on the owning page, and a repository does
#: not contain itself.
CONTENT_GROUPS: tuple[tuple[str, str], ...] = (
    ("Apps", "App"),
    ("Packages", "Package"),
    ("Agent plugins", "AgentPlugin"),
    ("Test suites", "TestSuite"),
)


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One bullet's worth of a page: its title, where it lives, what it says."""

    title: str
    concept_id: str
    description: str


def _entry(bundle: Bundle, concept_id: str) -> CatalogEntry | None:
    """*concept_id* as an entry, or `None` when it is not a readable page.

    A page that is absent or failed to parse is dropped rather than rendered
    as a bullet pointing at nothing: content never raises here, and a broken
    page is already reported by the rules that exist for it.
    """
    document = bundle.concepts.get(concept_id)
    if document is None or document.parse_error is not None:
        return None
    return CatalogEntry(
        title=(document.fm.title or concept_id.rsplit("/", 1)[-1]).strip(),
        concept_id=concept_id,
        description=(document.fm.description or "").strip(),
    )


def _bullet(entry: CatalogEntry) -> str:
    line = f"- [{escape_angle_brackets(entry.title)}](/{entry.concept_id}.md)"
    if entry.description:
        line += f" — {escape_angle_brackets(entry.description)}"
    return line


def _bullets(entries: Sequence[CatalogEntry]) -> str:
    ordered = sorted(entries, key=lambda entry: (entry.title.casefold(), entry.concept_id))
    return "\n".join(_bullet(entry) for entry in ordered) + "\n"


def repository_entries(bundle: Bundle) -> tuple[CatalogEntry, ...]:
    """Every Repository page in *bundle*.

    Read off the bundle rather than off the config or the graph, so a
    repository whose page was just pruned is gone from the catalog on the
    same run that deleted it, with no second list to keep in step.
    """
    found = (_entry(bundle, concept_id) for concept_id in bundle.by_type("Repository"))
    return tuple(entry for entry in found if entry is not None)


def render_repositories(entries: Sequence[CatalogEntry]) -> str:
    """The bundle root's `## Repositories` body."""
    if not entries:
        return _NONE_PLACEHOLDER
    return _bullets(entries)


def contents_groups(
    bundle: Bundle,
    concept_ids: Sequence[str],
    *,
    synthetic: Mapping[str, tuple[str, CatalogEntry]] = MappingProxyType({}),
) -> dict[str, tuple[CatalogEntry, ...]]:
    """*concept_ids* bucketed into `CONTENT_GROUPS` by each page's `type`.

    A page of any other type -- `Dependency`, `File`, the repository's own
    `Repository` page -- lands in no bucket and is simply not rendered. So
    does any id that is absent from the bundle or failed to parse and has no
    entry in `synthetic`.

    `synthetic` maps a concept id to a `(type_name, CatalogEntry)` stand-in
    used only when `bundle` has no readable page for that id -- a real page
    always wins. This is how a preview caller (`code_wiki_okf.entities.sync`)
    can bucket a sibling page its own run would create but that does not
    exist in `bundle` yet: the id is genuinely absent from *this* bundle, not
    broken, so it must not simply be dropped the way a truly missing or
    unparseable page is.
    """
    by_type: dict[str, list[CatalogEntry]] = {}
    for concept_id in concept_ids:
        entry = _entry(bundle, concept_id)
        if entry is not None:
            document = bundle.concepts[concept_id]
            type_name = (document.fm.type or "").strip()
        else:
            stand_in = synthetic.get(concept_id)
            if stand_in is None:
                continue
            type_name, entry = stand_in
        by_type.setdefault(type_name, []).append(entry)
    return {group: tuple(by_type.get(type_name, ())) for group, type_name in CONTENT_GROUPS}


def render_contents(groups: Mapping[str, Sequence[CatalogEntry]]) -> str:
    """A Repository page's `## Contents` body: four H3 groups, empties omitted.

    Deliberately flat rather than nested per-package sub-lists: frontmatter
    already owns `depends_on`, `test_suites` and `entry_points`, and each
    entity page already generates its own `## Files` section, so repeating
    those edges here would write every edge twice, in two places that can
    disagree.
    """
    blocks = [f"### {group}\n\n{_bullets(groups[group])}" for group, _type_name in CONTENT_GROUPS if groups.get(group)]
    if not blocks:
        return _NONE_PLACEHOLDER
    return "\n".join(blocks)


__all__ = [
    "CONTENT_GROUPS",
    "CatalogEntry",
    "contents_groups",
    "render_contents",
    "render_repositories",
    "repository_entries",
]
