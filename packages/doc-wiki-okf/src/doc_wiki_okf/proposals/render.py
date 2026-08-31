"""The review artifact a proposal carries while it is `proposed`.

Seven sections, reading `sources[]`, injected into
`okf_ext.proposals.plan_propose` as its `render=`. The capability computes the
merge; this writes the body, and it is called with the merged result.

Three properties worth stating outright:

**Suggested Action derives its verb.** Mode comes from whether the target is
already a bundle member rather than from a stored `mode:` frontmatter key,
matching the capability's own stance that nothing which is not written down can
drift from reality.

**Links are root-absolute markdown, never wikilinks.** The vault is converting
to `[/sources/...](...)`, and no new tooling constructs a `[[sources/...]]`.

**Byte-stable by construction.** No clock, no set iteration, no hash-dependent
ordering -- the precondition the capability's byte-stability guarantee rests on
once a renderer is injected.

`description` is accepted because `BodyRenderer` declares it and is deliberately
not rendered: the frontmatter directly above the body already carries it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from okf_ext.proposals import HEADER, Mode

from doc_wiki_okf.proposals.lanes import Lane

#: The legacy's explicit "nothing was captured" lines, kept verbatim. An empty
#: heading tells a reviewer nothing; these tell them the reasoner had nothing.
_NO_EVIDENCE = "- No source evidence was captured."
_NO_PAGES = "- No existing pages were cited by the proposal reasoner."
_NO_SUMMARY = "No reasoning summary was captured."
_NO_CONFLICTS = "- No conflicts identified."
_NO_NOTES = "- No implementation notes captured."
_NO_ORIGINS = "No origins were captured."


def _as_list(value: Any) -> list[str]:  # noqa: ANN401 -- reads an arbitrary raw YAML value
    """*value* as a list of non-blank strings; a scalar becomes one item.

    `sources[]` keys OKF does not name ride through the merge verbatim, so a
    producer writing `evidence: one line` rather than a list is content to
    tolerate, not an error to raise on -- okf-io's own rule.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _link(reference: str) -> str:
    """A root-absolute markdown link for a page reference, the bare text
    otherwise. Never a wikilink."""
    text = reference.strip()
    if "/" not in text:
        return text
    destination = text if text.startswith("/") else f"/{text}"
    return f"[{destination}]({destination})"


def _label(source: Mapping[str, Any]) -> str:
    """What the Origins heading calls this source: title, else id, else
    resource. `okf_ext.proposals.render._label`'s chain, restated because that
    one is private to its module."""
    for key in ("title", "id", "resource"):
        value = str(source.get(key) or "").strip()
        if value:
            return value
    return "(untitled source)"


@dataclass(frozen=True, slots=True)
class ReviewRenderer:
    """A `BodyRenderer` closed over the resolved target and its lane."""

    lane: Lane
    target: str
    mode: Mode

    def _suggested_action(self) -> str:
        verb = "Update existing" if self.mode == "update" else "Create new"
        return f"{verb} {self.lane.type_name} page `{self.target}`."

    def __call__(
        self,
        *,
        description: str,
        sources: Sequence[Mapping[str, Any]],
        newline: str = "\n",
    ) -> str:
        gathered: dict[str, list[str]] = {
            "evidence": [],
            "existing_pages_considered": [],
            "reasoning_summary": [],
            "potential_conflicts": [],
            "implementation_notes": [],
        }
        for source in sources:
            for key, collected in gathered.items():
                collected.extend(_as_list(source.get(key)))

        lines: list[str] = [HEADER, "", "## Suggested Action", "", self._suggested_action()]

        lines.extend(("", "## Evidence From Source", ""))
        lines.extend([f"- {item}" for item in gathered["evidence"]] or [_NO_EVIDENCE])

        lines.extend(("", "## Existing Pages Considered", ""))
        considered = gathered["existing_pages_considered"]
        lines.extend([f"- {_link(item)}" for item in considered] or [_NO_PAGES])

        lines.extend(("", "## Reasoning Summary", ""))
        lines.extend(gathered["reasoning_summary"] or [_NO_SUMMARY])

        lines.extend(("", "## Potential Conflicts", ""))
        lines.extend([f"- {item}" for item in gathered["potential_conflicts"]] or [_NO_CONFLICTS])

        lines.extend(("", "## Implementation Notes", ""))
        lines.extend([f"- {item}" for item in gathered["implementation_notes"]] or [_NO_NOTES])

        lines.extend(("", "## Origins", ""))
        if not sources:
            lines.append(_NO_ORIGINS)
        for index, source in enumerate(sources):
            if index:
                lines.append("")
            resource = str(source.get("resource") or "").strip()
            heading = f"**{_label(source)} · {_link(resource)}**" if resource else f"**{_label(source)}**"
            lines.append(heading)
            rationale = str(source.get("rationale") or "").strip()
            if rationale:
                lines.extend(("", rationale))

        return newline.join(lines) + newline


__all__ = ["ReviewRenderer"]
