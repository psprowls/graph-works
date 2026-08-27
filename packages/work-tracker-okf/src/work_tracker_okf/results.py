"""The mechanical results stub, and the one direct write in this lane.

`write_results` does **not** plan-then-apply (C2-H). A results stub is derived
entirely from its facts, so a plan would show nothing `render(facts)` does not
already show — the split earns its keep when the *destination* or the *existing
content* is what a caller wants to inspect, and here neither is in question.

It lives here rather than in a composing CLI because the destination *is* this
package's layout contract: putting the write upstream means re-deriving the
managed-artifact mapping there, which is the drift the carrier prevents.

Git is out of scope: `ResultsFacts` takes its facts, it never gathers them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from okf_io import load

from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref, item_page
from work_tracker_okf.sources import upsert


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
    scope: tuple[str, ...]
    start_predates_item: bool


def render(facts: ResultsFacts) -> str:
    """The stub body. Pure: no clock, no filesystem, no git."""
    commit_word = "commit" if len(facts.commits) == 1 else "commits"
    warnings: list[str] = []
    if facts.start_sha == facts.end_sha:
        warnings.append("_Range is empty: the stage recorded no commits in this scope. The start commit may be wrong._")
    if facts.start_predates_item:
        warnings.append("_Range starts before the item was opened; attribution may be too wide._")
    lines = [
        f"## {facts.phase.capitalize()} — results",
        "",
        f"**Commits:** `{facts.start_sha[:7]}`..`{facts.end_sha[:7]}` ({len(facts.commits)} {commit_word})",
        f"**Files changed:** {len(facts.files)}",
        f"**Scope:** {', '.join(facts.scope)}",
        "",
        *warnings,
        *([""] if warnings else []),
        *(f"- {name}" for name in facts.files),
        "",
        *(f"- {commit}" for commit in facts.commits),
        "",
        "_Mechanical stub._",
    ]
    return "\n".join(lines) + "\n"


def write_results(root: Path, item_path: str, facts: ResultsFacts) -> Path:
    """Write and register *facts* in the canonical results slot for *item_path*.

    The managed-artifact lookup validates the phase first, so an unknown one
    raises before a directory is created.
    """
    key = f"{facts.phase}-results"
    if key not in MANAGED_ARTIFACTS:
        raise ValueError(f"results are supported only for execute/finish, got {facts.phase!r}")
    ref = artifact_ref(item_path, MANAGED_ARTIFACTS[key])
    document = load(item_page(item_path).path(root))
    target = ref.path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(facts), encoding="utf-8", newline="")
    upsert(document, ref, title=f"{facts.phase.capitalize()} results")
    document.save()
    return target


__all__ = ["ResultsFacts", "render", "write_results"]
