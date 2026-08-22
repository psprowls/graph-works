"""Record ingested material: one Source page, and the material beside it.

**Two writes, one plan.** The page and the copy are both `mode="create"` writes
on one `PagePlan`, so `okf_ext.writing.write_all`'s probe-and-staging regime
makes them land together or not at all. A page recording material the bundle
does not carry, or material with no page explaining it, is a state this cannot
reach.

It returns `okf_ext.proposals.PagePlan` rather than a new plan type: that type
already carries `root`, `target`, `mode`, `writes`, `refusals`, `ok` and
`is_empty`, and `okf_ext.proposals.apply` already accepts it. `proposal` is
`None`, which is that field's documented meaning for the direct-request door.

**It writes nothing, and it reads nothing.** The caller supplies the material's
payload -- text where it decoded, bytes where it did not -- and owns the read.
Nothing here inspects it, so this capability's refusal vocabulary stays closed
and binary material is not a special case in it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path, PurePosixPath

from okf_ext.proposals import PagePlan, PageRender, Refusal, Write, plan_create
from okf_ext.schemas import SchemaSet
from okf_ext.sections import render_skeleton
from okf_ext.shape import SectionSet
from okf_io import Bundle

from doc_wiki_okf.ingest.layout import GRAPH_WIKI_LAYOUT, IngestLayout
from doc_wiki_okf.reading import slugify
from doc_wiki_okf.resources import seed_files

#: The `type` this subpackage writes.
SOURCE_TYPE = "Source"

#: What an unclassified document lands as. The catch-all `note` used to hold
#: this role; K-C gave it to `doc`, whose old meaning ("an in-repo design
#: document") died with the drift stamp.
DEFAULT_SOURCE_KIND = "doc"


def source_kinds(schema_set: SchemaSet) -> tuple[str, ...]:
    """The `source_kind` vocabulary *schema_set*'s `Source` declares, in its order.

    The vocabulary is authored in `schema/Source.schema.json` and nowhere
    else, so a vault that edits its own declarations changes what the CLI
    accepts and what the ingestor prompt lists, in one edit.

    Raises `KeyError` naming the declarations root when the set has no
    `Source` -- the same message `plan_ingest` raises for the same cause, so a
    caller catching one recognizes the other -- or naming the file when
    `Source` declares no `source_kind` enum.
    """
    try:
        schema = schema_set.schemas[SOURCE_TYPE]
    except KeyError as exc:
        raise KeyError(f"{SOURCE_TYPE}: no schema in `{schema_set.root.name}`") from exc
    try:
        enum = schema["properties"]["source_kind"]["enum"]
    except KeyError as exc:
        raise KeyError(f"{SOURCE_TYPE}: no `source_kind` enum in `{schema_set.root}`") from exc
    return tuple(str(value) for value in enum)


def seed_source_kinds() -> tuple[str, ...]:
    """The vocabulary *this package's own* seed `Source` schema declares.

    The answer for a caller with no bundle to read: `doc-wiki-okf ingest`
    briefs a document against a `wiki/` that need not be initialized, and
    refusing to brief it would be a regression for a command that writes
    nothing. Still one authored list -- this reads the same file the installer
    copies rather than restating its contents.
    """
    schema = json.loads(seed_files()["schema/Source.schema.json"])
    return tuple(str(value) for value in schema["properties"]["source_kind"]["enum"])


#: Where the copy goes, relative to the source page's own directory. Derived
#: rather than hardcoded to `sources/references/`, so a vault that replaces
#: `IngestLayout.source_page_template` gets its references beside its sources
#: rather than in someone else's directory.
REFERENCES_DIRECTORY = "references"

#: What a material file with no suffix is copied in as.
DEFAULT_SUFFIX = ".txt"


@dataclass(frozen=True, slots=True)
class IngestPreflight:
    """Collision information for a proposed Source page and its copy."""

    page: str
    copy: str
    origin: str
    refusals: tuple[Refusal, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether the predicted source page may be written."""
        return not self.refusals


def page_target(title: str, *, today: date, layout: IngestLayout = GRAPH_WIKI_LAYOUT) -> str:
    """Where the source page for *title* is created.

    The template is the layout's, unchanged from what
    `DocumentBrief.suggested_summary_path` already predicts: the brief and the
    writer must agree, and they agree by sharing it.
    """
    return layout.source_page_template.format(month=today.strftime("%Y-%m"), slug=slugify(title))


def copy_target(page: str, material: Path) -> str:
    """Where *material* is copied, given the page it belongs to.

    The stem is the page's, so a human reading `sources/<stem>.md` finds its
    material at `sources/references/<stem>.*` without consulting frontmatter.
    Two materials colliding on one stem collide on the *page* path first, so the
    re-ingest refusal fires before this path is ever ambiguous.
    """
    page_path = PurePosixPath(page)
    suffix = material.suffix.lower() or DEFAULT_SUFFIX
    return str(page_path.parent / REFERENCES_DIRECTORY / f"{page_path.stem}{suffix}")


def _existing_source_with_origin(bundle: Bundle, origin: str) -> str | None:
    """The page path of an already-ingested `Source` carrying *origin*, or `None`.

    `origin` is not one of `okf_io`'s spec-level `Frontmatter` fields, so a
    document that carries it holds it in `.extra` -- confirmed via
    `KNOWN_KEYS`/`_extra_of` in `okf_io.models`. A linear scan over
    `bundle.concepts`, already held in memory by the one directory walk
    `load_bundle` performed: no extra I/O, and trivial at this codebase's
    target scale (a wiki's worth of `Source` pages).
    """
    for concept_id, document in bundle.concepts.items():
        if document.fm.type == SOURCE_TYPE and document.fm.extra.get("origin") == origin:
            return f"{concept_id}.md"
    return None


def preflight_ingest(
    bundle: Bundle,
    material: Path,
    *,
    title: str,
    origin: str,
    today: date,
    layout: IngestLayout = GRAPH_WIKI_LAYOUT,
) -> IngestPreflight:
    """Refuse a predicted Source page, reference copy, or origin collision.

    This is intentionally independent of page rendering so callers can avoid
    expensive work when the brief's predicted identity is already present.
    """
    page = page_target(title, today=today, layout=layout)
    copy = copy_target(page, material)
    refusals: list[Refusal] = []
    if bundle.has_member(page):
        refusals.append(Refusal(page, "target-exists", "already a member; a source page is written once"))
    existing = _existing_source_with_origin(bundle, origin)
    if existing is not None:
        refusals.append(
            Refusal(page, "target-exists", f"already ingested (origin={origin!r}); existing source: {existing}")
        )
    if bundle.has_member(copy):
        refusals.append(Refusal(copy, "target-exists", "already a member; a reference copy is written once"))
    return IngestPreflight(page=page, copy=copy, origin=origin, refusals=tuple(refusals))


def _unique_refusals(refusals: tuple[Refusal, ...]) -> tuple[Refusal, ...]:
    """Keep each planner refusal once, preserving its first-seen order."""
    unique: list[Refusal] = []
    identities: set[tuple[str, str, str]] = set()
    for refusal in refusals:
        identity = (refusal.path, refusal.kind, refusal.detail)
        if identity not in identities:
            identities.add(identity)
            unique.append(refusal)
    return tuple(unique)


def plan_ingest(
    bundle: Bundle,
    schema_set: SchemaSet,
    section_set: SectionSet,
    material: Path,
    *,
    content: str | bytes,
    title: str,
    description: str,
    source_kind: str,
    origin: str,
    by: str,
    at: datetime,
    today: date,
    layout: IngestLayout = GRAPH_WIKI_LAYOUT,
    body: str | None = None,
    **extra: object,
) -> PagePlan:
    """Plan recording *material* as a Source page plus a reference copy.

    *content* is the material's payload -- `str` where the caller could decode
    it as UTF-8, `bytes` where it could not -- and is passed through to the copy
    write without ever being inspected. `text` would contradict its own type the
    moment bytes are legal, which is why the keyword is `content`. *material* is
    its path, consulted for its suffix and named by a refusal; `copy_target`
    takes the suffix from it, so a `.pdf` lands at
    `sources/references/<stem>.pdf` with no change.

    *extra* carries the optional frontmatter the schema declares and this
    function does not name -- `source_date`, `authors`, `entity_uri`, `tokens`,
    `tags`. Blank and `None` values are dropped rather than written.

    Raises `KeyError` when either declaration set does not know `Source`. Both
    are checked, matching `diataxis.pages.new_page_text`: a page created from a
    section declaration the schema set does not carry would fail `schema_rule`
    the moment it lands.
    """
    if SOURCE_TYPE not in schema_set.schemas:
        raise KeyError(f"{SOURCE_TYPE}: no schema in `{schema_set.root.name}`")
    declaration = section_set.types[SOURCE_TYPE]

    preflight = preflight_ingest(bundle, material, title=title, origin=origin, today=today, layout=layout)
    page = preflight.page
    copy = preflight.copy

    frontmatter: dict[str, object] = {
        "title": title.strip(),
        "description": description.strip(),
        "source_kind": source_kind,
        "source_path": copy,
        "origin": origin,
        "ingested": today.isoformat(),
    }
    frontmatter.update({key: value for key, value in extra.items() if value not in (None, "", (), [])})

    render = PageRender(
        type=SOURCE_TYPE,
        body=render_skeleton(declaration) if body is None else body,
        frontmatter=frontmatter,
    )
    plan = plan_create(bundle, page, render, by=by, at=at)

    refusals = _unique_refusals((*plan.refusals, *preflight.refusals))
    if refusals:
        return replace(plan, writes=(), refusals=refusals)

    return replace(plan, writes=(*plan.writes, Write(member=copy, mode="create", text=content)))


__all__ = [
    "DEFAULT_SOURCE_KIND",
    "DEFAULT_SUFFIX",
    "REFERENCES_DIRECTORY",
    "SOURCE_TYPE",
    "IngestPreflight",
    "copy_target",
    "page_target",
    "plan_ingest",
    "preflight_ingest",
    "seed_source_kinds",
    "source_kinds",
]
