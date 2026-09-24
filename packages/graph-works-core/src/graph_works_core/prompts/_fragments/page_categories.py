# Source: plugins/graph-wiki/skills/graph-wiki/SKILL.md §Page categories —
# rewritten as a renderer, C2 §6.3's reasoning applied to categories.

"""What kinds of page the wiki holds, rendered from the bundle's own
declarations rather than hand-maintained.

The old `PAGE_CATEGORIES` constant was the legacy graph-wiki plugin's
category list, not this bundle's: it named a `concept` row and a `kind:`
frontmatter field neither schema declares, and it omitted seven of the twelve
directories the bundle's own schemas *do* declare
(`okf_ext.schemas.declared_directories`). A renderer cannot drift the way a
hand-written table did -- add, rename or remove a schema's
`x-okf-directory` and this table follows, the same move
`architecture_overview.py` already made for the layout half of these
prompts.

`adr` and `work` stay hardcoded rows. `adr` because the row must render for a
workspace whose declarations predate the `Adr` schema
(`doc_wiki_okf.proposals.lanes.ADR_DIRECTORY` names the directory either way);
`Adr` is deliberately absent from `_TYPE_GLOSSES`, so a schema-declared `adrs/`
does not add a second row. `work_tracker_okf`'s own
schemas (`Epic`, `Bug`, `Feature`, `Spike`, `TechDebt`, `TestGap`) do declare
`x-okf-directory: "work/"` and are installed alongside the rest -- `work` is
hardcoded rather than schema-derived because `_TYPE_GLOSSES` is keyed on only
the twelve top-level page kinds. None of those six work-item types appear in
it, and a single `work` row (from `work_tracker_okf.WORK_DIR`) stands in for
all six.

`File` declares the repository-local lane segment `file-system/`. Its row says so
explicitly: the schema annotation is not a claim that a top-level `file-system/`
catalog exists. Repository and Dependency rows likewise name the additional
resource identity that qualifies their canonical placement.

Glosses cannot come off the schema either -- no schema property carries
prose. Four (`tutorial`, `how-to`, `reference`, `explanation`) are reused
verbatim from `lane_list.LANE_GLOSSES` rather than re-authored. The rest
carry over from the retired `PAGE_CATEGORIES` constant's own prose
unchanged (`app`, `package`, `dependency`, `source`, `adr`, `work`), or are
new one-liners for the four directories the old table omitted
(`test-suite`, `agent-plugin`, `repository`, `file`).
"""

from __future__ import annotations

from collections.abc import Mapping

from doc_wiki_okf.proposals.lanes import ADR_DIRECTORY
from okf_ext.schemas import SchemaSet, declared_directories
from work_tracker_okf import WORK_DIR

from graph_works_core.prompts._fragments.lane_list import LANE_GLOSSES

#: type_name -> (category name, gloss), for every schema type this table
#: names a row for. Types absent here contribute no row -- see the module
#: docstring for the deliberately collapsed work-item types.
_TYPE_GLOSSES: Mapping[str, tuple[str, str]] = {
    "App": ("app", "One application workspace (web, mobile, CLI) — platform, entry points, deployment"),
    "Package": ("package", "One library/service workspace — what it exports, who depends on it, key patterns"),
    "Dependency": (
        "dependency",
        "An external package one repository declares, under a repository's `entities/dependencies/<ecosystem>/`",
    ),
    "AgentPlugin": (
        "agent-plugin",
        "An agent or skill plugin the workspace installs — what it does, how it's invoked",
    ),
    "TestSuite": ("test-suite", "The test coverage for a package or app — what's covered, how to run it"),
    "Repository": (
        "repository",
        "One version-controlled repository, represented by `code-graph/<repo>.md`",
    ),
    "File": ("file", "A repository-local source-file mirror under that repository's `file-system/` lane"),
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
