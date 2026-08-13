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

#: The `type` this subpackage writes.
SOURCE_TYPE = "Source"

#: The nine values `_schema/Source.schema.json` enumerates, in its own order.
#: Duplicated from the schema deliberately: the CLI validates against this
#: before a plan is built, so a bad `--source-type` is a usage error rather than
#: a `schemas.invalid` finding on a page that already landed.
SOURCE_TYPES: tuple[str, ...] = (
    "spec",
    "article",
    "pr",
    "ticket",
    "transcript",
    "example",
    "skill",
    "doc",
    "note",
)

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


def plan_ingest(
    bundle: Bundle,
    schema_set: SchemaSet,
    section_set: SectionSet,
    material: Path,
    *,
    text: str,
    title: str,
    description: str,
    source_type: str,
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
        "source_type": source_type,
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
    "DEFAULT_SUFFIX",
    "REFERENCES_DIRECTORY",
    "SOURCE_TYPE",
    "SOURCE_TYPES",
    "copy_target",
    "page_target",
    "plan_ingest",
]
