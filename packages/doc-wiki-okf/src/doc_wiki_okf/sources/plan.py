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
decoded text; the CLI owns the read and the `UnicodeDecodeError`, because the
first cut is UTF-8 text only and a decode failure is a command-level error
rather than a plan refusal. That keeps this capability's refusal vocabulary
closed and puts the check where the decoding actually happens.
"""

from __future__ import annotations

import json
from dataclasses import replace
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

    The vocabulary is authored in `_schema/Source.schema.json` and nowhere
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
    schema = json.loads(seed_files()["_schema/Source.schema.json"])
    return tuple(str(value) for value in schema["properties"]["source_kind"]["enum"])


#: Where the copy goes, relative to the source page's own directory. Derived
#: rather than hardcoded to `sources/references/`, so a vault that replaces
#: `IngestLayout.source_page_template` gets its references beside its sources
#: rather than in someone else's directory.
REFERENCES_DIRECTORY = "references"

#: What a material file with no suffix is copied in as.
DEFAULT_SUFFIX = ".txt"


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


def plan_ingest(
    bundle: Bundle,
    schema_set: SchemaSet,
    section_set: SectionSet,
    material: Path,
    *,
    text: str,
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

    *text* is the material already decoded as UTF-8; *material* is its path,
    consulted for its suffix and named by a refusal.

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

    page = page_target(title, today=today, layout=layout)
    copy = copy_target(page, material)

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

    refusals = plan.refusals
    existing = _existing_source_with_origin(bundle, origin)
    if existing is not None:
        refusals = (
            *refusals,
            Refusal(
                path=page,
                kind="target-exists",
                detail=f"already ingested (origin={origin!r}); a source with this origin already exists at {existing}",
            ),
        )
    if bundle.has_member(copy):
        refusals = (
            *refusals,
            Refusal(
                path=copy,
                kind="target-exists",
                detail="already a member; a reference copy is written once, never reconciled",
            ),
        )
    if refusals:
        return replace(plan, writes=(), refusals=refusals)

    return replace(plan, writes=(*plan.writes, Write(member=copy, mode="create", text=text)))


__all__ = [
    "DEFAULT_SOURCE_KIND",
    "DEFAULT_SUFFIX",
    "REFERENCES_DIRECTORY",
    "SOURCE_TYPE",
    "copy_target",
    "page_target",
    "plan_ingest",
    "seed_source_kinds",
    "source_kinds",
]
