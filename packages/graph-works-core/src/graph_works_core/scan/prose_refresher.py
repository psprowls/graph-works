"""The prose refresher's prompt surface, its parser, and the sanitizer.

The sanitizer lives here rather than in `commands.scan` because both the
in-process path and the file surface must filter identically -- putting it
beside the prompt that produced the text keeps the allow-list and the
instruction that describes it in one file.

What the refresher may write is decided by the *declaration*: the task carries
its type's `ownership == "prose"` headings and nothing else, so a section a
`sections/*.yaml` marks `generated` is unrepresentable in the answer rather
than defended against downstream.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from graph_works_core.agent_substrate.agent_tools import strip_code_fence, truncate_text
from graph_works_core.scan.scan_contract import ProseRefreshTask

#: The refresher's own budget for the page it is rewriting.
MAX_PROMPT_PAGE_CHARS = 20_000

#: And for the rendered diff.
MAX_PROMPT_DIFF_CHARS = 8_000

#: A body opening with any of these is the model handing back the placeholder.
#: Compared against the first non-blank line, `>`-stripped and casefolded.
TODO_PREFIXES: tuple[str, ...] = ("todo", "tbd", "fixme")

PROSE_REFRESHER_SYSTEM = """\
You write the prose sections of a code-wiki entity page.

You are given one entity, the page as it stands, the headings you may write, and
tools for reading the entity's own source files, its wiki neighbours, and the
code graph. Read before you write: a claim you cannot ground in a file you read
does not belong on the page.

Rules:
- Write ONLY the headings you are given. A heading not in that list is generated
  by machine and anything you write under it is discarded.
- Write the section body only. Do not repeat the heading.
- No placeholders. If you cannot say something true and specific about a
  section, omit that heading from your answer entirely -- an omitted section is
  kept as it was; a "TODO" is thrown away and the page is retried next scan.
- Cite code as backticked `path:line` relative to the entity root.
- Markdown, no frontmatter, no top-level heading.

Return a single JSON object and nothing else:

{"sections": {"## Heading": "body markdown", ...}}
"""


def build_prose_refresh_prompt(task: ProseRefreshTask) -> str:
    """The refresher's human message for one entity.

    The "why now" is selected from `(trigger, diff)`, not from `trigger` alone.
    `first_fill` wins the trigger when a page is both never-written and
    diff-stale (`scan_contract.TRIGGERS`), and reading only the trigger meant a
    task that carried a real `diff` and `changed_files` asked the model to
    rewrite already-good prose with no statement of what had moved under it.
    """
    headings = "\n".join(
        f"### {heading}\nCurrent body:\n{body.strip() or '(empty)'}\n" for heading, body in task.prose_sections.items()
    )
    changed = "Changed files:\n" + truncate_text(task.diff or "", MAX_PROMPT_DIFF_CHARS)
    if task.trigger == "diff" and task.diff is None:
        why = (
            "Why now: this page's prose anchor is unknown to the repository -- its history was "
            "rewritten, so no diff can be computed. Re-read the entity from scratch rather than "
            "trusting what the page already says."
        )
    elif task.trigger == "diff":
        why = "Why now: the code under this entity changed since its prose was last written.\n" + changed
    elif task.diff is not None:
        why = (
            "Why now: one or more of these sections has never been written and still holds its "
            "placeholder -- and the code under this entity has also moved since the page was last "
            "written. Write the empty sections, and check the written ones against what changed.\n"
        ) + changed
    else:
        why = "Why now: one or more of these sections has never been written and still holds its placeholder."

    return (
        f"Entity: {task.name} ({task.kind})\n"
        f"URI: {task.uri}\n"
        f"Wiki page: {task.page_path}\n"
        f"Entity root (the only directory your file tools can reach): {task.entity_root}\n"
        f"Owning repository HEAD: {task.owning_short_head or '(unknown)'}\n\n"
        f"{why}\n\n"
        "Graph context:\n"
        f"{task.graph_context or '(none)'}\n\n"
        "The page as it stands:\n"
        f"{truncate_text(task.page_content, MAX_PROMPT_PAGE_CHARS)}\n\n"
        "Headings you may write:\n"
        f"{headings or '(none)'}\n"
        'Return {"sections": {...}} carrying only the headings you are rewriting.'
    )


def parse_prose_refresher_output(text: str) -> tuple[dict[str, str], bool]:
    """Split the model's answer into `(sections, parsed)`.

    `parsed` is `True` for any well-formed mapping, **including an empty one**.
    It is `False` only for a JSON error or a top-level shape that is neither a
    mapping of headings nor an object carrying a `sections` mapping.
    """
    body = strip_code_fence(text or "")
    if not body:
        return {}, False
    try:
        loaded: Any = json.loads(body)
    except ValueError:
        return {}, False
    if not isinstance(loaded, dict):
        return {}, False
    raw = loaded.get("sections", loaded) if "sections" in loaded else loaded
    if raw is None:
        return {}, True
    if not isinstance(raw, dict):
        return {}, False
    return {str(key): str(value) for key, value in raw.items()}, True


def _is_todo_shaped(body: str) -> bool:
    stripped = body.strip()
    if not stripped:
        return True
    first = stripped.splitlines()[0].lstrip(">").strip().casefold()
    return any(first.startswith(prefix) for prefix in TODO_PREFIXES)


def sanitize_prose_result(sections: Mapping[str, str], *, allowed: Sequence[str]) -> dict[str, str]:
    """*sections* narrowed to what may actually land on a page.

    Two filters, both provider-agnostic so the file surface gets exactly what
    the in-process path gets: a heading the task did not declare is dropped, and
    a body that is blank or still placeholder-shaped is dropped. Surviving
    bodies are edge-stripped -- the splice re-terminates every line itself.
    """
    permitted = set(allowed)
    return {
        heading: body.strip()
        for heading, body in sections.items()
        if heading in permitted and not _is_todo_shaped(body)
    }


__all__ = [
    "MAX_PROMPT_DIFF_CHARS",
    "MAX_PROMPT_PAGE_CHARS",
    "PROSE_REFRESHER_SYSTEM",
    "TODO_PREFIXES",
    "build_prose_refresh_prompt",
    "parse_prose_refresher_output",
    "sanitize_prose_result",
]
