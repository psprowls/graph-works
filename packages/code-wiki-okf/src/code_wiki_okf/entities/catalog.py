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
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from okf_ext.bundle import SECTIONS_DIRNAME
from okf_ext.generators import Render, plan_regenerate
from okf_ext.generators import apply as apply_regenerations
from okf_ext.render import escape_angle_brackets
from okf_ext.sections import render_skeleton
from okf_ext.shape import SectionSet, SectionSpec, TypeSections, load_sections
from okf_ext.writing import ApplyResult, PendingWrite, write_all
from okf_io import Bundle, Document, load_bundle

from code_wiki_okf.placement import (
    PlacementContext,
    PlacementError,
    canonical_concept_id,
    context_from_resource,
    is_code_wiki_type,
)

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
    ("Agent Plugins", "AgentPlugin"),
    ("Test Suites", "TestSuite"),
)

_ROOT_GROUPS: tuple[tuple[str, str], ...] = (
    ("Repositories", "Repository"),
    ("Packages", "Package"),
    ("Apps", "App"),
    ("Agent Plugins", "AgentPlugin"),
    ("Test Suites", "TestSuite"),
    ("Dependencies", "Dependency"),
)
_REPOSITORY_GROUPS: tuple[tuple[str, str], ...] = (
    ("Repository", "Repository"),
    ("Packages", "Package"),
    ("Apps", "App"),
    ("Agent Plugins", "AgentPlugin"),
    ("Test Suites", "TestSuite"),
    ("Files", "File"),
)
_GLOBAL_DIRECTORY_BY_TYPE = {
    "Repository": "repositories",
    "Package": "packages",
    "App": "apps",
    "AgentPlugin": "agent-plugins",
    "TestSuite": "test-suites",
}
_REPOSITORY_LANE_BY_TYPE = {
    "Package": "packages",
    "App": "apps",
    "AgentPlugin": "agent-plugins",
    "TestSuite": "test-suites",
}


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


@dataclass(frozen=True, slots=True)
class CatalogPage:
    """One canonical page projected into catalog membership."""

    type_name: str
    context: PlacementContext
    entry: CatalogEntry
    owned_frontmatter: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class CatalogPlan:
    """Catalog members a reconciliation would create or update."""

    created: tuple[str, ...] = field(default_factory=tuple)
    updated: tuple[str, ...] = field(default_factory=tuple)
    declined: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not self.created and not self.updated and not self.declined


def catalog_pages(bundle: Bundle) -> tuple[CatalogPage, ...]:
    """Canonical code-wiki concepts from the actual loaded bundle."""
    found: list[CatalogPage] = []
    for concept_id, document in sorted(bundle.concepts.items()):
        if document.parse_error is not None:
            continue
        type_name = document.fm.type or ""
        resource = document.fm.resource
        if not is_code_wiki_type(type_name) or resource is None:
            continue
        try:
            context = context_from_resource(type_name, resource)
            expected = canonical_concept_id(context)
        except PlacementError:
            continue
        if concept_id != expected:
            continue
        entry = _entry(bundle, concept_id)
        if entry is not None:
            owned_frontmatter = (
                {"package_count": document.fm_raw["package_count"]}
                if type_name == "Repository" and "package_count" in document.fm_raw
                else {}
            )
            found.append(
                CatalogPage(
                    type_name=type_name,
                    context=context,
                    entry=entry,
                    owned_frontmatter=MappingProxyType(owned_frontmatter),
                )
            )
    return tuple(found)


def _section_body(entries: Sequence[CatalogEntry]) -> str:
    return _bullets(entries) if entries else _NONE_PLACEHOLDER


def _index_entry(directory: str) -> CatalogEntry:
    return CatalogEntry(title=PurePosixPath(directory).name, concept_id=f"{directory}/index", description="")


def contents_groups_from_entries(entries: Sequence[CatalogPage]) -> dict[str, tuple[CatalogEntry, ...]]:
    """Repository Contents groups without consulting desired graph targets."""
    return {
        heading: tuple(item.entry for item in entries if item.type_name == type_name)
        for heading, type_name in CONTENT_GROUPS
    }


def _required_catalogs(
    classified: Sequence[CatalogPage],
) -> tuple[dict[str, tuple[str, ...]], dict[str, Render], dict[str, Render]]:
    """Return declarations, index renders, and Repository-page renders."""
    by_type: dict[str, list[CatalogPage]] = {}
    by_repository: dict[str, list[CatalogPage]] = {}
    ecosystems: set[str] = set()
    for item in classified:
        by_type.setdefault(item.type_name, []).append(item)
        if item.context.repository is not None:
            by_repository.setdefault(item.context.repository, []).append(item)
        if item.context.ecosystem is not None:
            ecosystems.add(item.context.ecosystem)

    headings: dict[str, tuple[str, ...]] = {
        "": tuple(heading for heading, _type_name in _ROOT_GROUPS),
        "repositories": ("Repositories",),
        "packages": ("Packages",),
        "apps": ("Apps",),
        "agent-plugins": ("Agent Plugins",),
        "test-suites": ("Test Suites",),
        "dependencies": ("Dependencies",),
    }
    index_renders: dict[str, Render] = {
        "": Render(
            sections={
                heading: _section_body(tuple(item.entry for item in by_type.get(type_name, ())))
                for heading, type_name in _ROOT_GROUPS
            }
        ),
        "repositories": Render(
            sections={"Repositories": _section_body(tuple(item.entry for item in by_type.get("Repository", ())))}
        ),
        "dependencies": Render(
            sections={"Dependencies": _section_body(tuple(_index_entry(f"dependencies/{item}") for item in ecosystems))}
        ),
    }
    for type_name, directory in _GLOBAL_DIRECTORY_BY_TYPE.items():
        heading = next(heading for heading, candidate in _ROOT_GROUPS if candidate == type_name)
        index_renders[directory] = Render(
            sections={heading: _section_body(tuple(item.entry for item in by_type.get(type_name, ())))}
        )

    concept_renders: dict[str, Render] = {}
    for repository, items in sorted(by_repository.items()):
        prefix = f"repositories/{repository}"
        headings[prefix] = tuple(heading for heading, _type_name in _REPOSITORY_GROUPS)
        index_renders[prefix] = Render(
            sections={
                heading: _section_body(tuple(item.entry for item in items if item.type_name == type_name))
                for heading, type_name in _REPOSITORY_GROUPS
            }
        )
        for type_name, lane in _REPOSITORY_LANE_BY_TYPE.items():
            directory = f"{prefix}/{lane}"
            heading = next(heading for heading, candidate in _REPOSITORY_GROUPS if candidate == type_name)
            headings[directory] = (heading,)
            index_renders[directory] = Render(
                sections={heading: _section_body(tuple(item.entry for item in items if item.type_name == type_name))}
            )

        file_items = tuple(item for item in items if item.type_name == "File")
        file_directories = {f"{prefix}/files"}
        for item in file_items:
            parent = PurePosixPath(item.entry.concept_id).parent.as_posix()
            while parent.startswith(f"{prefix}/files"):
                file_directories.add(parent)
                if parent == f"{prefix}/files":
                    break
                parent = PurePosixPath(parent).parent.as_posix()
        for directory in sorted(file_directories):
            direct_files = tuple(
                item.entry for item in file_items if PurePosixPath(item.entry.concept_id).parent.as_posix() == directory
            )
            child_directories = tuple(
                _index_entry(candidate)
                for candidate in sorted(file_directories)
                if PurePosixPath(candidate).parent.as_posix() == directory
            )
            headings[directory] = ("Files", "Directories")
            index_renders[directory] = Render(
                sections={
                    "Files": _section_body(direct_files),
                    "Directories": _section_body(child_directories),
                }
            )

        repository_page = next((item for item in items if item.type_name == "Repository"), None)
        if repository_page is not None:
            concept_renders[repository_page.entry.concept_id] = Render(
                frontmatter=repository_page.owned_frontmatter,
                sections={"Contents": render_contents(contents_groups_from_entries(items))},
            )

    for ecosystem in sorted(ecosystems):
        directory = f"dependencies/{ecosystem}"
        ecosystem_entries = tuple(
            item.entry for item in by_type.get("Dependency", ()) if item.context.ecosystem == ecosystem
        )
        headings[directory] = ("Dependencies",)
        index_renders[directory] = Render(sections={"Dependencies": _section_body(ecosystem_entries)})

    return headings, index_renders, concept_renders


def _catalog_section_set(section_set: SectionSet, headings: Mapping[str, Sequence[str]]) -> SectionSet:
    indexes = dict(section_set.indexes)
    for directory, required in headings.items():
        declaration = indexes.get(directory, TypeSections(sections=()))
        existing = {spec.heading.casefold() for spec in declaration.sections}
        additions = tuple(
            SectionSpec(
                heading=heading,
                ownership="generated",
                required=True,
                placeholder=_NONE_PLACEHOLDER,
            )
            for heading in required
            if heading.casefold() not in existing
        )
        indexes[directory] = TypeSections(
            sections=declaration.sections + additions,
            additional_sections=declaration.additional_sections,
            frontmatter=declaration.frontmatter,
        )
    return SectionSet(
        types=section_set.types,
        sources=section_set.sources,
        fragments=section_set.fragments,
        root=section_set.root,
        indexes=MappingProxyType(dict(sorted(indexes.items()))),
    )


def _projected_bundle(
    bundle: Bundle,
    pages: Sequence[CatalogPage],
    section_set: SectionSet,
    headings: Mapping[str, Sequence[str]],
) -> Bundle:
    """Overlay not-yet-written indexes and Repository values in memory."""
    indexes = dict(bundle.indexes)
    for directory in headings:
        if directory in indexes:
            continue
        member = f"{directory}/index.md" if directory else "index.md"
        indexes[directory] = Document.parse(
            render_skeleton(section_set.indexes[directory]),
            path=bundle.root / member,
        )

    concepts = dict(bundle.concepts)
    repository_declaration = section_set.types["Repository"]
    for page in pages:
        if page.type_name != "Repository":
            continue
        concept_id = page.entry.concept_id
        current = concepts.get(concept_id)
        if current is None:
            document = Document.parse("", path=bundle.root / f"{concept_id}.md")
            document.set("type", "Repository")
            document.set("title", page.entry.title)
            document.set("resource", page.context.resource)
            document.set("description", page.entry.description)
            document.set_body(render_skeleton(repository_declaration))
        else:
            document = Document.parse(current.serialize(), path=current.path)
        for key in repository_declaration.frontmatter.owned:
            if key in page.owned_frontmatter:
                document.set(key, page.owned_frontmatter[key])
            elif key in document.fm_raw:
                document.delete(key)
        concepts[concept_id] = document

    return replace(
        bundle,
        concepts=MappingProxyType(concepts),
        indexes=MappingProxyType(indexes),
    )


def plan_catalogs(
    bundle: Bundle,
    *,
    pages: Sequence[CatalogPage] | None = None,
    declarations_dir: Path | None = None,
) -> CatalogPlan:
    """Preview catalog creates and regenerations without touching disk."""
    projected_pages = catalog_pages(bundle) if pages is None else tuple(pages)
    headings, index_renders, concept_renders = _required_catalogs(projected_pages)
    declarations_root = bundle.root if declarations_dir is None else declarations_dir
    section_set = _catalog_section_set(load_sections(declarations_root / SECTIONS_DIRNAME), headings)
    created = tuple(
        f"{directory}/index.md" if directory else "index.md"
        for directory in sorted(headings)
        if directory not in bundle.indexes
    )
    created_set = set(created)
    projected = _projected_bundle(bundle, projected_pages, section_set, headings)
    regeneration = plan_regenerate(projected, section_set, concept_renders, index_renders=index_renders)
    return CatalogPlan(
        created=created,
        updated=tuple(item.path for item in regeneration.regenerations if item.path not in created_set),
        declined=tuple((item.path, item.reason) for item in regeneration.skipped),
    )


def reconcile_catalogs(
    bundle: Bundle,
    *,
    today: date,
    declarations_dir: Path | None = None,
) -> ApplyResult:
    """Reconcile every catalog from canonical pages retained on actual disk."""
    _ = today
    classified = catalog_pages(bundle)
    headings, index_renders, concept_renders = _required_catalogs(classified)
    declarations_root = bundle.root if declarations_dir is None else declarations_dir
    section_set = _catalog_section_set(load_sections(declarations_root / SECTIONS_DIRNAME), headings)

    pending = [
        PendingWrite(
            member=f"{directory}/index.md" if directory else "index.md",
            path=bundle.root / (f"{directory}/index.md" if directory else "index.md"),
            rendered=render_skeleton(section_set.indexes[directory]),
            on_written=lambda: None,
            create=True,
        )
        for directory in sorted(headings)
        if directory not in bundle.indexes
    ]
    created = write_all(pending)
    if created.failed:
        return created

    current = load_bundle(bundle.root) if created.written else bundle
    plan = plan_regenerate(current, section_set, concept_renders, index_renders=index_renders)
    regenerated = apply_regenerations(current, plan)
    return ApplyResult(
        written=created.written + regenerated.written,
        failed=created.failed + regenerated.failed,
        skipped=created.skipped + regenerated.skipped,
    )


__all__ = [
    "CONTENT_GROUPS",
    "CatalogEntry",
    "CatalogPage",
    "CatalogPlan",
    "catalog_pages",
    "plan_catalogs",
    "reconcile_catalogs",
    "render_contents",
]
