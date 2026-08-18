# Source: plugins/graph-wiki/skills/graph-wiki/SKILL.md §Page categories —
# rewritten as a renderer, C2 §6.3's reasoning applied to categories.

"""What kinds of page the wiki holds, rendered from the bundle's own
declarations rather than hand-maintained.

The old `PAGE_CATEGORIES` constant was the legacy graph-wiki plugin's
category list, not this bundle's: it named a `concept` row and a `kind:`
frontmatter field neither schema declares, and it omitted six of the eleven
directories the bundle's own schemas *do* declare
(`okf_ext.schemas.declared_directories`). A renderer cannot drift the way a
hand-written table did -- add, rename or remove a schema's
`x-okf-directory` and this table follows, the same move
`architecture_overview.py` already made for the layout half of these
prompts.

`adr` and `work` stay hardcoded rows: `adrs/` has no schema at all
(deliberately -- the cutover epic's decision,
`doc_wiki_okf.proposals.lanes.ADR_DIRECTORY`). `work_tracker_okf`'s own
schemas (`Epic`, `Bug`, `Feature`, `Spike`, `TechDebt`, `TestGap`) do declare
`x-okf-directory: "work/"` and are installed alongside the rest -- `work` is
hardcoded rather than schema-derived for the same reason `File` contributes
no second `repositories/` row below: `_TYPE_GLOSSES` is keyed on only the
eleven top-level page kinds, so none of those six work-item types appear in
it, and a single `work` row (from `work_tracker_okf.WORK_DIR`) stands in for
all six.

`File` also declares `x-okf-directory: "repositories/"` -- the same
directory `Repository` declares, for `File`'s own mirror sub-pages
(`code_wiki_okf.entities.lanes.ENTITY_DEPTH` already names this exact
ambiguity: `{"Repository": "exact", "File": "nested"}`). `_TYPE_GLOSSES`
is keyed on only the eleven top-level page kinds, so `File` contributes no
second `repositories/` row.

Glosses cannot come off the schema either -- no schema property carries
prose. Four (`tutorial`, `how-to`, `reference`, `explanation`) are reused
verbatim from `lane_list.LANE_GLOSSES` rather than re-authored. The rest
carry over from the retired `PAGE_CATEGORIES` constant's own prose
unchanged (`app`, `package`, `dependency`, `source`, `adr`, `work`), or are
new one-liners for the three directories the old table omitted
(`test-suite`, `agent-plugin`, `repository`).
"""

from __future__ import annotations

from collections.abc import Mapping

from doc_wiki_okf.proposals.lanes import ADR_DIRECTORY
from okf_ext.schemas import SchemaSet, declared_directories
from work_tracker_okf import WORK_DIR

from graph_works_core.prompts._fragments.lane_list import LANE_GLOSSES

#: type_name -> (category name, gloss), for every schema type this table
#: names a row for. A type declaring `x-okf-directory` but absent here (e.g.
#: `File`) contributes no row -- see the module docstring.
_TYPE_GLOSSES: Mapping[str, tuple[str, str]] = {
    "App": ("app", "One application workspace (web, mobile, CLI) — platform, entry points, deployment"),
    "Package": ("package", "One library/service workspace — what it exports, who depends on it, key patterns"),
    "Dependency": ("dependency", "An external package or service the monorepo depends on"),
    "AgentPlugin": (
        "agent-plugin",
        "An agent or skill plugin the workspace installs — what it does, how it's invoked",
    ),
    "TestSuite": ("test-suite", "The test coverage for a package or app — what's covered, how to run it"),
    "Repository": ("repository", "One version-controlled repository this workspace tracks"),
    "Tutorial": ("tutorial", LANE_GLOSSES["tutorial"]),
    "HowTo": ("how-to", LANE_GLOSSES["how-to"]),
    "Reference": ("reference", LANE_GLOSSES["reference"]),
    "Explanation": ("explanation", LANE_GLOSSES["explanation"]),
    "Source": ("source", "Summary of an ingested spec, PR, article, transcript, etc."),
}

#: The two rows no schema declares -- (directory, category name, gloss).
_CONSTANT_ROWS: tuple[tuple[str, str, str], ...] = (
    (ADR_DIRECTORY, "adr", "Architecture Decision Record — a dated, citable decision with context + consequences"),
    (f"{WORK_DIR}/", "work", "Unified bug / tech-debt / feature / epic / spike — replaces issues + roadmap"),
)


def render_page_categories(schema_set: SchemaSet) -> str:
    """## Page categories, rendered from the bundle's own declarations.

    Every row but `adr` and `work` comes from `declared_directories(schema_set)`
    -- add, rename, or remove a schema's `x-okf-directory` and this table
    follows. Rows are sorted by directory so the render is stable regardless
    of `declared_directories`' own (unordered-by-contract) `dict` return.
    """
    rows = [
        (directory, *_TYPE_GLOSSES[type_name])
        for type_name, directory in declared_directories(schema_set).items()
        if type_name in _TYPE_GLOSSES
    ]
    rows.extend(_CONSTANT_ROWS)
    rows.sort(key=lambda row: row[0])
    header = "## Page categories\n\n| Category | What it documents |\n|---|---|"
    body = "\n".join(f"| `{name}` | {gloss} |" for _directory, name, gloss in rows)
    return f"{header}\n{body}"


__all__ = ["render_page_categories"]
