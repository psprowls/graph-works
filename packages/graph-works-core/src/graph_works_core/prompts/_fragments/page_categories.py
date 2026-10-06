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

`work` is the one hardcoded row: seven work types share `work/`, and one
row from `work_tracker_okf.WORK_DIR` stands for all of them. Every proposal
type's row outside `work/`, including `adr`, comes from its schema's guidance
`summary` and kebab-cased type name.

`File` declares the repository-local lane segment `file-system/`. Its row says so
explicitly: the schema annotation is not a claim that a top-level `file-system/`
catalog exists. Repository and Dependency rows likewise name the additional
resource identity that qualifies their canonical placement.

`ManagedRepository` and `ReferenceRepository` both declare the one directory
`repositories/`. Only `ManagedRepository` is keyed in `_TYPE_GLOSSES`, so a single
`repository-resource` row stands for both types; keying both would emit two rows for
one directory.

Generated types keep their glosses here because a locked type carries no
proposal guidance. Proposal types carry their own summary in
`x-okf-proposal-guidance`, so their glosses follow the schema set.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from okf_ext.schemas import SchemaSet, declared_directories, declared_proposables
from work_tracker_okf import WORK_DIR

#: Generated type_name -> (category name, gloss). Admitted proposal types
#: contribute their schema guidance separately; work-item types collapse
#: into one row as described in the module docstring.
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
    # ManagedRepository and ReferenceRepository share `repositories/`; one row stands for both.
    "ManagedRepository": (
        "repository-resource",
        "A repository the workspace embeds as a resource, managed or reference, at `repositories/<name>.md`",
    ),
    "File": ("file", "A repository-local source-file mirror under that repository's `file-system/` lane"),
    "Source": ("source", "Summary of an ingested spec, PR, article, transcript, etc."),
}

#: The one row no single schema declares: seven work types share `work/`, and
#: one row stands for all of them -- (directory, category name, gloss).
_WORK_ROW: tuple[str, str, str] = (
    f"{WORK_DIR}/",
    "work",
    "Unified bug / tech-debt / feature / epic / spike — replaces issues + roadmap",
)


def _category(type_name: str) -> str:
    """`HowTo` -> `how-to`: the kebab category name this table has always used."""
    return re.sub(r"(?<!^)(?=[A-Z])", "-", type_name).lower()


def render_page_categories(schema_set: SchemaSet) -> str:
    """## Page categories, rendered from the bundle's own declarations.

    Generated types come from `declared_directories` with the glosses above;
    every proposal-pool type outside `work/` comes from
    `declared_proposables`, its gloss being its own guidance `summary`; one
    `work` row stands for the work types. Sorted by directory.
    """
    rows = [
        (directory, *_TYPE_GLOSSES[type_name])
        for type_name, directory in declared_directories(schema_set).items()
        if type_name in _TYPE_GLOSSES
    ]
    rows.extend(
        (entry.directory, _category(entry.name), entry.guidance.summary)
        for entry in declared_proposables(schema_set).types
        if entry.directory != _WORK_ROW[0] and entry.name not in _TYPE_GLOSSES
    )
    rows.append(_WORK_ROW)
    rows.sort(key=lambda row: row[0])
    header = "## Page categories\n\n| Category | What it documents |\n|---|---|"
    body = "\n".join(f"| `{name}` | {gloss} |" for _directory, name, gloss in rows)
    return f"{header}\n{body}"


__all__ = ["render_page_categories"]
