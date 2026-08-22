"""The ingestor system prompt: shared fragments plus ingestor-local prose.

Four properties of this prompt are deliberate and easy to lose in an edit.

**Root-absolute markdown links, never wikilinks** (C2 §6.4). There is no
`## Wikilink discipline` section here, because nothing strips links any more
(spec §4.7): a broken link is an `okf_io.validate()` finding on the bundle,
which is a thing a human can act on.

**`raw/` is not mentioned.** `ab906786` retired it from the code; material is
ingested from wherever it lives, and `source_kind` is classified from content
rather than read off a folder name.

**`page_type` is not mentioned.** Routing is fixed, so naming the field would
only tell the model to ignore something it would otherwise never see.

**The output format names the sections the `Source` declaration actually
declares**, so the page the model writes and the skeleton
`doc_wiki_okf.sources` would otherwise render agree.

The architecture overview is a renderer, not a constant (C2 §6.3), so this is
`build_ingestor_system(layout=…, kinds=…)` rather than a module-level string.
There is deliberately no `INGESTOR_SYSTEM` backward-compat constant: it could
not be built without a layout, and now also could not be built without
`kinds` -- the `source_kind` vocabulary is the bundle's own, read at run time
from its `schema/Source.schema.json` (K-D), not this package's -- and
nothing in the rebuild imports either as a constant.
"""

from __future__ import annotations

from collections.abc import Sequence

from doc_wiki_okf.sources import DEFAULT_SOURCE_KIND
from okf_ext.schemas import SchemaSet

from graph_works_core.prompts._fragments.architecture_overview import render_architecture_overview
from graph_works_core.prompts._fragments.citation_rules import CITATION_RULES
from graph_works_core.prompts._fragments.claude_md_disambiguation import CLAUDE_MD_DISAMBIGUATION
from graph_works_core.prompts._fragments.frontmatter_rules import FRONTMATTER_RULES
from graph_works_core.prompts._fragments.iron_rules import IRON_RULES
from graph_works_core.prompts._fragments.page_categories import render_page_categories
from graph_works_core.prompts._fragments.style_rules import STYLE_RULES
from graph_works_core.workspace.layout import WorkspaceLayout

_ROLE_INTRO = (
    "You are a code wiki ingestor. Read one source document and write the Source\n"
    "page that records it in the wiki.\n\n"
    "Output ONLY YAML frontmatter followed by a markdown body. No commentary outside\n"
    "these sections."
)


def _source_landing(kinds: Sequence[str]) -> str:
    """The landing section, naming the vocabulary *this bundle* declares.

    A function rather than a module constant for the same reason the
    architecture overview is one: the list is the vault's, read at run time
    from `schema/Source.schema.json`, and a constant here could only restate
    it -- which is exactly the duplication K-D removes.
    """
    return (
        "## Source landing\n\n"
        "Every ingested document becomes a **Source page** under `sources/`, and a copy "
        "of the material itself is written beside it under `sources/references/`. You do "
        "NOT choose either destination -- the writer owns both, and they land together "
        "or not at all.\n\n"
        "Emit a `description` field: one line, what this material is. Emit a "
        "`source_kind` field from this closed enum, classified from the document's "
        "content:\n\n"
        + "\n".join(f"- `{kind}`" for kind in kinds)
        + f"\n\nOmit the field when the material has no clear genre. An omitted "
        "value, or one outside the enum, is discarded and the caller's own hint "
        f"decides -- which defaults to `{DEFAULT_SOURCE_KIND}`. `source_kind` is "
        "descriptive metadata; it does not control where the page is written.\n\n"
        "Do NOT author a page for a code entity. Packages, apps, dependencies and test "
        "suites are scanner-owned. To associate this source with one, link it from the "
        "`## Touches` section as a root-absolute markdown link -- "
        "`[/packages/okf-io.md](/packages/okf-io.md)`. The index and the log are "
        "reconciled automatically; omit those steps."
    )


_INGESTOR_RULES = (
    "## Ingestor rules\n\n"
    "- The source material is read-only. You never propose editing it.\n"
    "- Code is the source of truth; when the wiki and the code disagree, say so.\n"
    "- Cite aggressively -- every claim links to a wiki page or a code path.\n"
    "- Flag contradictions: wiki-vs-wiki with a `> WARNING: Contradiction:` callout, "
    "wiki-vs-code with the path.\n"
    "- A decision worth recording belongs in `## Decisions triggered`; a separate "
    "pass proposes the ADR.\n"
    "- Link only to pages that exist. A link to a page that does not is reported as "
    "a broken-link finding on the bundle -- it is not silently removed, and it is "
    "not invisible."
)

_RED_FLAGS = (
    "## Red flags\n\n"
    "Say so in `## Surprises / contradictions` rather than proceeding silently if: the\n"
    "material duplicates a page the wiki already has; recording it would require\n"
    "deleting wiki pages; or it contradicts the code in more than five places."
)

_OUTPUT_FORMAT = (
    "## Output format\n\n"
    "1. YAML frontmatter (`---`) per the **Frontmatter rules** section above --\n"
    "   `title` and `description` required, the rest emitted when the material\n"
    "   supports them. `title` is authoritative: the page's path, and the name\n"
    "   of its reference copy, are derived from it. The provisional path in the\n"
    "   request is a prediction from the material's first heading, not a\n"
    "   commitment.\n"
    "2. A body using these headings, in this order, omitting none:\n\n"
    "   - `## TL;DR` -- two or three sentences on what this material is.\n"
    "   - `## Key claims` -- the load-bearing assertions, one bullet each.\n"
    "   - `## Touches` -- root-absolute markdown links to the wiki pages and code\n"
    "     entities this material is about.\n"
    "   - `## Evidence / rationale` -- why the key claims hold.\n"
    "   - `## Surprises / contradictions` -- what disagrees with the wiki or the code.\n"
    "   - `## Decisions triggered` -- decisions this material records or implies.\n"
    "   - `## Where it's cited in this wiki` -- leave the body of this one empty; it\n"
    "     is filled in as pages come to cite this source.\n\n"
    "Under 1500 tokens. Synthesize -- do not reproduce the source."
)

# Live ingestor runs occasionally wrap the frontmatter in a markdown code
# fence, which is not frontmatter at all. This goes LAST in the composition so
# it is the most recent instruction the model reads. `parse_ingestor_response`
# strips a leading fence anyway; both halves of that belt-and-braces are load
# bearing, and the parser-side half is the one that has actually fired.
_NO_CODE_FENCE = (
    "## Frontmatter format (strict)\n\n"
    "Begin the response with `---` on its own line. "
    "Do NOT wrap the frontmatter in a markdown code fence "
    "(no ```yaml, no ``` of any kind around the `---` block). "
    "The first three characters of the response MUST be `---`."
)


def build_ingestor_system(
    *, layout: WorkspaceLayout, kinds: Sequence[str], schema_set: SchemaSet, project_context: str = ""
) -> str:
    """Assemble the ingestor system prompt for one workspace.

    *kinds* is the `source_kind` vocabulary the target bundle declares --
    `doc_wiki_okf.sources.source_kinds(schema_set)` for a caller holding one,
    `seed_source_kinds()` for a caller with no bundle. It is required rather
    than defaulted: a prompt listing a vocabulary the bundle does not declare
    is worse than a caller having to say which bundle it means.

    *schema_set* is the target bundle's loaded `schema/` set --
    `okf_ext.schemas.load_schemas(config.declarations_dir / "schema")` for a
    caller holding a `Config`. It drives `render_page_categories`, for the
    same reason *kinds* is required: a category table naming a directory the
    bundle does not declare is worse than the caller having to say which
    bundle it means.

    *project_context* is `prompts.render_project_context`'s output. When
    non-empty it is inserted at position 1 -- after the role intro, before the
    iron rules -- which is where the legacy put it and where a model reads it
    as context rather than as one more rule.
    """
    parts = [
        _ROLE_INTRO,
        IRON_RULES,
        render_architecture_overview(layout),
        render_page_categories(schema_set),
        FRONTMATTER_RULES,
        CITATION_RULES,
        STYLE_RULES,
        CLAUDE_MD_DISAMBIGUATION,
        _source_landing(kinds),
        _INGESTOR_RULES,
        _RED_FLAGS,
        _OUTPUT_FORMAT,
        _NO_CODE_FENCE,
    ]
    if project_context:
        parts.insert(1, project_context)
    return "\n\n".join(parts)


__all__ = ["build_ingestor_system"]
