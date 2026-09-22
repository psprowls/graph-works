"""System prompts for the three-group semantic lint fan-out.

`page_quality`, `adr_chain`, `stale_claims` — each assembled from this
package's shared fragments plus its own check set, with an optional
`project_context` block inserted after the role intro (CTX-03).

The stale-claims checks name `sources[]`, not the `source_path` /
`package_path` frontmatter keys: provenance in OKF v0.2 is native `sources[]`,
and those keys do not exist here. The consequence is recorded in the design
spec's §9 — this group's output has no reference output to diff against.
"""

from __future__ import annotations

from graph_works_core.prompts._fragments.claude_md_disambiguation import CLAUDE_MD_DISAMBIGUATION
from graph_works_core.prompts._fragments.iron_rules import IRON_RULES
from graph_works_core.prompts._fragments.log_format import LOG_FORMAT

#: Shared by all three groups. Linter-only, so it lives here rather than in
#: `_fragments/`, which is for material more than one role reads.
LINT_PRIORITY_ORDER = """\
## Prioritization

Prioritize findings: code drift > contradictions > broken links > orphans > stale > style."""

_PAGE_QUALITY_ROLE_INTRO = """\
You are a knowledge-bundle quality linter. Review the provided pages and identify quality issues.
Report one finding per line in plain text. Do not include write operations — report only.
If no quality issues are found, output exactly: No page quality issues found."""

_ADR_CHAIN_ROLE_INTRO = """\
You are an ADR (architecture decision record) chain linter. Review the provided ADR pages and
identify chain integrity issues. Report one finding per line in plain text.
Do not include write operations — report only.
If no ADR chain issues are found, output exactly: No ADR chain issues found."""

_STALE_CLAIMS_ROLE_INTRO = """\
You are a stale-claims linter. Review the provided pages and identify claims that may be outdated
relative to the resources their `sources[]` provenance declares.
Report one finding per line in plain text. Do not include write operations — report only.
If no stale claim issues are found, output exactly: No stale claim issues found."""

_PAGE_QUALITY_CHECKS = """\
## Semantic check categories

Check all of the following:

1. **Vague or placeholder descriptions** — a `description` under ten words, obviously templated, or \
clearly not describing the page's actual content.
2. **Inconsistent terminology** — the same concept referred to by different names across pages.
3. **Missing links where expected** — plain-text mentions of a package or a known concept that \
should be a markdown link to that page.
4. **Orphaned pages** — pages nothing else in the provided set links to.
5. **Dead links** — links whose target page does not appear in the provided page set.
6. **Claims contradicting one another** — two pages in the set asserting incompatible things about \
the same subject.
7. **Missing ADRs for major decisions** — a significant architectural choice described on a page \
with no corresponding ADR in the set."""

_ADR_CHAIN_CHECKS = """\
## ADR chain checks

1. **Broken supersedes references** — a supersedes reference pointing at an ADR not present in the \
provided set.
2. **Unsuperseded superseded ADRs** — an ADR marked superseded with no link to what superseded it, \
or whose successor does not reference it back.
3. **Deprecated without reason** — a deprecated ADR with no reason or replacement in the body.
4. **Orphan ADRs** — an ADR nothing else in the set references.
5. **Cyclic chains** — A supersedes B supersedes A, or a longer loop; flag every ADR in the cycle.
6. **Missing status** — an ADR whose frontmatter declares no status."""

_STALE_CLAIMS_CHECKS = """\
## Stale claim checks

Each page below declares its provenance as `sources[]` — one entry per resource the page was written \
from, each with the commit it was read at.

1. **Claims about a resource no longer described that way** — body text asserting behaviour the \
declared source can no longer plausibly support.
2. **Out-of-date version references** — explicit version numbers contradicting what the declared \
sources say.
3. **Citations to resources absent from `sources[]`** — body text citing a resource the page never \
declared reading.
4. **Under-declared provenance** — the body clearly describes a resource that no `sources[]` entry \
names, so the page cannot go stale when that resource changes.
5. **Unresolved debt markers** — TODO, FIXME, WIP or placeholder text standing in for a real claim."""

_OUTPUT_FORMAT = """\
## Output format

Report one finding per line in plain text. Do not output JSON, bullet lists, or markdown headers.
Prefix a finding with `<page id>: ` when it is about one page — copy that id verbatim from the
page's own `--- Page: <id> ---` header above, character for character. Never invent, paraphrase,
reslugify, or shorten it: a page's title and its id often differ, and a reader can only find the
page again by the id exactly as given. Do not include write operations — report only. The user
decides what to fix."""


def _assemble(intro: str, checks: str, project_context: str) -> str:
    parts = [intro, IRON_RULES, LINT_PRIORITY_ORDER, LOG_FORMAT, CLAUDE_MD_DISAMBIGUATION, checks, _OUTPUT_FORMAT]
    if project_context:
        parts.insert(1, project_context)
    return "\n\n".join(parts)


def build_linter_page_quality_system(project_context: str = "") -> str:
    """The page-quality group's system prompt."""
    return _assemble(_PAGE_QUALITY_ROLE_INTRO, _PAGE_QUALITY_CHECKS, project_context)


def build_linter_adr_chain_system(project_context: str = "") -> str:
    """The ADR-chain group's system prompt."""
    return _assemble(_ADR_CHAIN_ROLE_INTRO, _ADR_CHAIN_CHECKS, project_context)


def build_linter_stale_claims_system(project_context: str = "") -> str:
    """The stale-claims group's system prompt."""
    return _assemble(_STALE_CLAIMS_ROLE_INTRO, _STALE_CLAIMS_CHECKS, project_context)


__all__ = [
    "LINT_PRIORITY_ORDER",
    "build_linter_adr_chain_system",
    "build_linter_page_quality_system",
    "build_linter_stale_claims_system",
]
