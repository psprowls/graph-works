"""The cross-page drift judge: is this curated page stale relative to the
current state of the changed entities that backlink it?

Kind-aware. A concept page is stale when the behaviour it describes no longer
matches the entity narratives; an ADR is **annotate-only** — stale only when
its Status, Consequences or Supersedes have been overtaken by code reality,
never because decision history could be reworded.

The verdict is a small JSON object with one finding per triggering entity, so
each proposal source gets precise attribution.
`parse_drift_propagator_verdict` fails **safe** — any unparseable or malformed
reply is not-stale, because a false "stale" costs a human a review of a
proposal that should not exist.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from graph_works_core.agent_substrate.agent_tools import strip_code_fence

_KIND_RUBRIC = {
    "concept": (
        "This is a CONCEPT page. It is stale when the behaviour or design it "
        "describes no longer matches what the entity narratives now say."
    ),
    "adr": (
        "This is an ADR (architecture decision record). Treat it as "
        "ANNOTATE-ONLY: flag it stale ONLY when the decision's Status, "
        "Consequences, or Supersedes have been overtaken by code reality (for "
        "example the decision was reversed or superseded by what the narrative "
        "now describes). Do NOT flag it merely because prose describing the "
        "original decision could be reworded, and never propose rewriting "
        "decision history."
    ),
}

DRIFT_PROPAGATOR_SYSTEM = """\
You judge whether a curated wiki page has gone STALE relative to the CURRENT state of the code \
entities it references.

You are given:
- the curated page's kind and full body, and
- for each changed entity the page references: that entity's current narrative (regenerated from the \
code as it exists now) and the list of files that changed since the page's claims were last checked.

Decide whether the page's claims now CONTRADICT or materially misdescribe what the narratives say. \
Do NOT flag a page for covering different ground, being shorter, or stylistic differences — only a \
genuine contradiction or material drift.

Output ONLY a single JSON object, no prose and no code fences:
{"stale": true|false,
 "findings": [{"entity_stem": "<one of the entity stems given to you>",
               "stale_claim": "<the page claim that is now wrong>",
               "rationale": "<one short line: why the narrative overtakes it>"}]}
Emit exactly one findings entry per entity that drives the staleness. When not stale, return \
{"stale": false, "findings": []}."""

_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)

#: The not-stale verdict, rebuilt per call so no caller can mutate a shared one.
_NOT_STALE: dict[str, Any] = {"stale": False, "findings": []}


def build_drift_propagator_prompt(
    kind: str,
    page_title: str,
    page_body: str,
    entities: Sequence[tuple[str, str, Sequence[str]]],
) -> tuple[str, str]:
    """`(system, human)` for one curated page and the entities that changed.

    *entities* is `(entity_stem, narrative, changed_files)`. An unknown *kind*
    takes the concept rubric: the judge still has a page and narratives to
    compare, and refusing would drop a real finding over a vocabulary the
    caller owns.
    """
    rubric = _KIND_RUBRIC.get(kind, _KIND_RUBRIC["concept"])
    lines = [
        f"Page kind: {kind}",
        rubric,
        "",
        f"Curated page title: {page_title}",
        "",
        "Curated page body:",
        page_body.strip(),
        "",
        "Referenced entities that changed:",
    ]
    for stem, narrative, changed_files in entities:
        files = ", ".join(changed_files) if changed_files else "(no specific files identified)"
        lines += ["", f"### entity: {stem}", f"Changed files: {files}", "Current narrative:", narrative.strip()]
    lines += ["", "Is the page stale relative to these narratives? Reply with the JSON verdict."]
    return DRIFT_PROPAGATOR_SYSTEM, "\n".join(lines)


def parse_drift_propagator_verdict(text: str) -> dict[str, Any]:
    """Parse a `{stale, findings[]}` verdict. Fails SAFE to not-stale.

    A finding survives only with a non-empty `entity_stem` — a proposal source
    needs attribution — and a `stale: true` verdict whose findings all fail
    that collapses to not-stale rather than filing an unattributed source.
    """
    raw = strip_code_fence(text or "")
    match = _OBJ_RE.search(raw)
    if match is None:
        return dict(_NOT_STALE, findings=[])
    try:
        obj = json.loads(match.group(0))
    except ValueError:
        return dict(_NOT_STALE, findings=[])
    if not isinstance(obj, dict) or obj.get("stale") is not True:
        return dict(_NOT_STALE, findings=[])
    raw_findings = obj.get("findings")
    raw_findings = raw_findings if isinstance(raw_findings, list) else []
    findings: list[dict[str, str]] = []
    for entry in raw_findings:
        if not isinstance(entry, dict):
            continue
        stem_raw = entry.get("entity_stem")
        stem = stem_raw.strip() if isinstance(stem_raw, str) else ""
        if not stem:
            continue
        findings.append(
            {
                "entity_stem": stem,
                "stale_claim": str(entry.get("stale_claim", "")),
                "rationale": str(entry.get("rationale", "")),
            }
        )
    if not findings:
        return dict(_NOT_STALE, findings=[])
    return {"stale": True, "findings": findings}


__all__ = ["DRIFT_PROPAGATOR_SYSTEM", "build_drift_propagator_prompt", "parse_drift_propagator_verdict"]
