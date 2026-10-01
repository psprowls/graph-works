"""`RepositorySnapshot` pages: one per pin (the `add` baseline and every advance), D-002.

A snapshot's body never links into the clone. Changed files render as inline code, so a snapshot never flags itself
on the next advance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from repositories_okf.git import FileChange, RangeFacts
from repositories_okf.lane import LANE_DIR
from repositories_okf.pages import new_page_text

SNAPSHOT_TYPE = "RepositorySnapshot"
CHANGED_FILES_CAP = 200


class FlaggedLike(Protocol):
    """What the renderer reads from a `flagging.FlaggedPage` (Task 5)."""

    @property
    def page(self) -> str: ...
    @property
    def title(self) -> str: ...
    @property
    def reasons(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class Snapshot:
    name: str
    commit: str
    fetched_at: str
    describe: str | None = None
    commit_date: str | None = None
    previous: str | None = None
    range: RangeFacts | None = None
    flagged: tuple[FlaggedLike, ...] = ()

    @property
    def baseline(self) -> bool:
        return self.range is None

    @property
    def label(self) -> str:
        return self.describe or self.commit[:7]

    @property
    def day(self) -> str:
        return self.fetched_at[:10]

    @property
    def path(self) -> str:
        return snapshot_path(self.name, self.day, self.commit)


def snapshot_path(name: str, day: str, commit: str) -> str:
    return f"{LANE_DIR}/{name}/snapshots/{day}-{commit[:7]}.md"


def summary(snapshot: Snapshot) -> str:
    """The changelog's generated text: `N commits, M files`, then `rewritten`, then the newest tag crossed."""
    if snapshot.range is None:
        return "baseline"
    facts = snapshot.range
    parts = [f"{facts.commits} commits", f"{facts.files_changed} files"]
    if facts.rewritten:
        parts.append("rewritten")
    if facts.tags:
        parts.append(facts.tags[-1])
    return ", ".join(parts)


def _range_section(snapshot: Snapshot) -> str:
    if snapshot.range is None:
        return f"Baseline: `{snapshot.name}` pinned at `{snapshot.commit[:7]}` ({snapshot.label}).\n"
    facts = snapshot.range
    line = f"`{facts.old[:7]}`..`{facts.new[:7]}`: {facts.commits} commits, {facts.files_changed} files changed."
    if facts.rewritten:
        origin = (
            f"the merge base `{facts.base[:7]}`" if facts.base else "the root, since the two histories share no commit"
        )
        line += (
            " Upstream history was rewritten: the old pin is not an ancestor of the new one, "
            f"so the range runs from {origin}."
        )
    return line + "\n"


def _bullets(items: tuple[str, ...]) -> str:
    return "".join(f"- {item}\n" for item in items) if items else "None.\n"


def _change_line(change: FileChange) -> str:
    if change.renamed_to is not None:
        return f"  - `{change.status}` `{change.path}` → `{change.renamed_to}`\n"
    return f"  - `{change.status}` `{change.path}`\n"


def _changed_files_section(changes: tuple[FileChange, ...]) -> str:
    if not changes:
        return "None.\n"
    shown = sorted(changes, key=lambda change: change.path)[:CHANGED_FILES_CAP]
    groups: dict[str, list[FileChange]] = {}
    for change in shown:
        top = change.path.split("/", 1)[0] + "/" if "/" in change.path else "(root)"
        groups.setdefault(top, []).append(change)
    text = "".join(
        f"- `{top}`\n" + "".join(_change_line(change) for change in group) for top, group in sorted(groups.items())
    )
    if len(changes) > CHANGED_FILES_CAP:
        text += f"\nShowing {CHANGED_FILES_CAP} of {len(changes)} changed files.\n"
    return text


def _flagged_section(flagged: tuple[FlaggedLike, ...]) -> str:
    if not flagged:
        return "None.\n"
    return "".join(f"- [{page.title}](/{page.page}) — " + "; ".join(page.reasons) + "\n" for page in flagged)


def render_snapshot(snapshot: Snapshot) -> str:
    facts = snapshot.range
    frontmatter: dict[str, object] = {
        "type": SNAPSHOT_TYPE,
        "title": f"{snapshot.name} @ {snapshot.label}",
        "description": summary(snapshot),
        "repository": f"/{LANE_DIR}/{snapshot.name}.md",
        "commit": snapshot.commit,
    }
    if snapshot.describe:
        frontmatter["describe"] = snapshot.describe
    if snapshot.commit_date:
        frontmatter["commit_date"] = snapshot.commit_date
    frontmatter["fetched_at"] = snapshot.fetched_at
    frontmatter["previous"] = snapshot.previous
    frontmatter["merge_base"] = facts.base if facts is not None else None
    frontmatter["rewritten"] = facts.rewritten if facts is not None else False
    frontmatter["range"] = {
        "commits": facts.commits if facts else 0,
        "files_changed": facts.files_changed if facts else 0,
    }
    body = "## Range\n\n" + _range_section(snapshot)
    if facts is not None:
        body += "\n## Tags crossed\n\n" + _bullets(tuple(f"`{tag}`" for tag in facts.tags))
        body += "\n## Merges\n\n" + _bullets(facts.merges)
        body += "\n## Changed files\n\n" + _changed_files_section(facts.changes)
        body += "\n## Flagged pages\n\n" + _flagged_section(snapshot.flagged)
    body += "\n## Doc impact\n"
    return new_page_text(frontmatter, body)


__all__ = [
    "CHANGED_FILES_CAP",
    "SNAPSHOT_TYPE",
    "FlaggedLike",
    "Snapshot",
    "render_snapshot",
    "snapshot_path",
    "summary",
]
