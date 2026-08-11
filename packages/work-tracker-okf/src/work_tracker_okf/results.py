"""The mechanical results stub, and the one direct write in this lane.

`write_results` does **not** plan-then-apply (C2-H). A results stub is derived
entirely from its facts, so a plan would show nothing `render(facts)` does not
already show — the split earns its keep when the *destination* or the *existing
content* is what a caller wants to inspect, and here neither is in question.

It lives here rather than in a composing CLI because the destination *is* this
package's layout contract: putting the write upstream means re-deriving
`artifact_path(..., kind="results")` there, which is the drift the carrier exists
to prevent.

Git is out of scope: `ResultsFacts` takes its facts, it never gathers them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from work_tracker_okf.paths import artifact_path


@dataclass(frozen=True, slots=True)
class ResultsFacts:
    """What a results stub is rendered from.

    A frozen input rather than `work-io`'s five positional arguments: five
    same-typed parameters at a call site is a transposition waiting to happen.
    `commits` holds already-formatted `git log --oneline` lines; `files` holds
    paths already flattened from `git diff --name-status`.
    """

    phase: str
    start_sha: str
    end_sha: str
    files: tuple[str, ...]
    commits: tuple[str, ...]


def render(facts: ResultsFacts) -> str:
    """The stub body. Pure: no clock, no filesystem, no git."""
    commit_word = "commit" if len(facts.commits) == 1 else "commits"
    lines = [
        f"## {facts.phase.capitalize()} — results",
        "",
        f"**Commits:** `{facts.start_sha[:7]}`..`{facts.end_sha[:7]}` ({len(facts.commits)} {commit_word})",
        f"**Files changed:** {len(facts.files)}",
        "",
        *(f"- {name}" for name in facts.files),
        "",
        *(f"- {commit}" for commit in facts.commits),
        "",
        "_Mechanical stub._",
    ]
    return "\n".join(lines) + "\n"


def write_results(root: Path, slug: str, facts: ResultsFacts, *, archived: bool = False) -> Path:
    """Write *facts* to the results slot for *slug*, and return where it landed.

    `artifact_path` validates the phase first, so an unknown one raises before a
    directory is created.
    """
    target = artifact_path(slug, facts.phase, "results", archived=archived).path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(facts), encoding="utf-8")
    return target


__all__ = ["ResultsFacts", "render", "write_results"]
