"""build_librarian_system — the per-page excerpt extraction prompt.

This librarian is called **per page**, with the page already in its human
message — not with an index and a list of pages to pick from. The workflow and
red-flag sections below reflect that narrower scope.

Sections:
  1. Role intro (librarian-local)
  2. IRON_RULES fragment (shared)
  3. render_page_categories fragment (shared)
  4. CITATION_RULES fragment (shared)
  5. STYLE_RULES fragment (shared)
  6. Read-only overrides (librarian-local — narrows the write-role fragments above)
  7. Workflow (librarian-local, adapted for per-page extraction)
  8. Red flags (librarian-local, adapted for per-page scope)
  9. Output format (librarian-local — preserves NO_RELEVANT_CONTENT sentinel contract)

Exports:
    build_librarian_system(schema_set: SchemaSet) -> str — assembles the librarian system prompt.
"""

from __future__ import annotations

from okf_ext.schemas import SchemaSet

from graph_works_core.prompts._fragments.citation_rules import CITATION_RULES
from graph_works_core.prompts._fragments.iron_rules import IRON_RULES
from graph_works_core.prompts._fragments.page_categories import render_page_categories
from graph_works_core.prompts._fragments.style_rules import STYLE_RULES

_ROLE_INTRO = """\
## Role

You are a wiki librarian. Given a user query and a single wiki page, extract every passage from the page that is directly relevant to the query. Prioritize the vault over re-deriving from code — the vault contains pre-synthesized knowledge with cross-references.\
"""

_READ_ONLY_OVERRIDES = """\
## How the rules above apply to this role

The fragments above are shared with page-writing roles. For a per-page
extractor, three things narrow:

- **You never write to the vault.** Ignore "Good query answers get filed
  back," "All LLM writes for the wiki go under the vault path," and "Update
  `updated:` frontmatter whenever you touch a page." This call returns text; it does not file anything.
- **On no match, return the sentinel, not a suggestion.** Citation rules'
  "suggest a source to ingest or a concept page to create" is for a role that
  owns the outcome. Yours is the bare sentinel `NO_RELEVANT_CONTENT` — no
  explanation, apology, or suggestion attached.
- **Concision does not apply to extraction.** "Be concise" and "short
  paragraphs" describe pages you would write. Your job is verbatim, complete
  passages — see Workflow step 2 and the first red flag below.\
"""

_WORKFLOW = """\
## Workflow

1. **Read the supplied page in full.** Retrieval already chose it; you are not browsing and you have no page list to pick from.
2. **Extract every passage that bears on the query**, verbatim. Preserve `path:line` annotations exactly; never invent or alter line numbers.
3. **Use the code-graph tools when they are bound** to ground a symbol, a caller or an import that the page names but does not show.
4. **Return the sentinel** when the page does not address the query. A partial match is not an answer.\
"""

_RED_FLAGS = """\
## Red flags

- Summarizing the page instead of quoting it → quote
- Answering from what you know rather than from the supplied page → stop
- Inventing a concept not in the page or the graph → stop, say the page does not cover it
- Returning a hedged partial match instead of the sentinel → return the sentinel\
"""

_OUTPUT_FORMAT = """\
## Output format

Either a list of verbatim excerpts (each labeled with its link as it appears in the page), or the bare sentinel `NO_RELEVANT_CONTENT` — nothing else.

Use `NO_RELEVANT_CONTENT` when: the page contains no relevant passage; or the page is a TODO stub/placeholder too sparse to address the query. Do not add explanation, apology, or partial-match attempts.\
"""


def build_librarian_system(*, schema_set: SchemaSet) -> str:
    """Assemble the librarian system prompt.

    *schema_set* is the target bundle's loaded `schema/` set, the same
    input `build_ingestor_system` requires for the same reason: a page
    category table naming a directory the bundle does not declare is worse
    than the caller having to say which bundle it means.

    The librarian does not accept a project_context kwarg by design — it is
    called per-page and only needs STYLE_RULES from the shared fragment set.
    """
    parts = [
        _ROLE_INTRO,
        IRON_RULES,
        render_page_categories(schema_set),
        CITATION_RULES,
        STYLE_RULES,
        _READ_ONLY_OVERRIDES,
        _WORKFLOW,
        _RED_FLAGS,
        _OUTPUT_FORMAT,
    ]
    return "\n\n".join(parts)


__all__ = ["build_librarian_system"]
