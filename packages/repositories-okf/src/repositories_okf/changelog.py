"""`RepositoryChangelog`: a reconciled list of a repository's snapshots, newest first (D-002).

Reconciled like `okf_io.update_index`: gw owns *which* entries appear and their order, and the human owns *what
they say*. An entry is a list line under `## Entries` whose first link targets one of this repository's snapshot
pages. Such a line is kept byte-for-byte while its snapshot exists, removed when the snapshot is gone, and
generated only when it is missing. Every other line — prose, links elsewhere, the frontmatter — is copied through.
Non-entry lines that sat between entries move below the reconciled block. Line endings follow the file.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from okf_io import Bundle

from repositories_okf.lane import LANE_DIR
from repositories_okf.pages import new_page_text
from repositories_okf.snapshots import SNAPSHOT_TYPE, Snapshot, summary

CHANGELOG_TYPE = "RepositoryChangelog"
ENTRIES_HEADING = "Entries"
_ENTRY = re.compile(r"^- \[[^\]]*\]\((?P<link>/[^)\s]+\.md)\)")


@dataclass(frozen=True, slots=True)
class ChangelogEntry:
    path: str
    day: str
    label: str
    summary: str

    @property
    def link(self) -> str:
        return f"/{self.path}"

    @property
    def line(self) -> str:
        return f"- [{self.day} — {self.label}]({self.link}) — {self.summary}"


def changelog_path(name: str) -> str:
    return f"{LANE_DIR}/{name}/changelog.md"


def entry_for(snapshot: Snapshot) -> ChangelogEntry:
    return ChangelogEntry(snapshot.path, snapshot.day, snapshot.label, summary(snapshot))


def entries_from_bundle(bundle: Bundle, name: str) -> tuple[ChangelogEntry, ...]:
    """One entry per `RepositorySnapshot` page directly under `repositories/<name>/snapshots/`."""
    prefix = f"{LANE_DIR}/{name}/snapshots/"
    found: list[ChangelogEntry] = []
    for concept_id, document in sorted(bundle.concepts.items()):
        if not concept_id.startswith(prefix) or "/" in concept_id[len(prefix) :]:
            continue
        data = document.fm_data(dates="iso")
        commit = data.get("commit")
        if data.get("type") != SNAPSHOT_TYPE or not isinstance(commit, str):
            continue
        describe = data.get("describe")
        label = describe if isinstance(describe, str) and describe else commit[:7]
        found.append(
            ChangelogEntry(
                f"{concept_id}.md", str(data.get("fetched_at", ""))[:10], label, str(data.get("description", ""))
            )
        )
    return tuple(found)


def _ordered(entries: Sequence[ChangelogEntry]) -> list[ChangelogEntry]:
    unique = {entry.path: entry for entry in entries}
    return sorted(unique.values(), key=lambda entry: (entry.day, entry.path), reverse=True)


def reconcile_changelog(before: str | None, *, name: str, entries: Sequence[ChangelogEntry]) -> str:
    ordered = _ordered(entries)
    if before is None:
        frontmatter: dict[str, object] = {
            "type": CHANGELOG_TYPE,
            "title": f"{name} changelog",
            "description": f"Pins of reference repository {name}, newest first.",
            "repository": f"/{LANE_DIR}/{name}.md",
        }
        return new_page_text(frontmatter, f"## {ENTRIES_HEADING}\n\n" + "".join(f"{entry.line}\n" for entry in ordered))
    newline = "\r\n" if "\r\n" in before else "\n"
    lines = before.splitlines(keepends=True)
    heading = next((index for index, line in enumerate(lines) if line.rstrip("\r\n") == f"## {ENTRIES_HEADING}"), None)
    if heading is None:
        tail = "" if before.endswith(("\n", "\r")) else newline
        new_block = "".join(f"{entry.line}{newline}" for entry in ordered)
        return f"{before}{tail}{newline}## {ENTRIES_HEADING}{newline}{newline}{new_block}"
    end = next((index for index in range(heading + 1, len(lines)) if lines[index].startswith("## ")), len(lines))
    region = lines[heading + 1 : end]
    own = f"/{LANE_DIR}/{name}/snapshots/"
    existing: dict[str, str] = {}
    positions: list[int] = []
    for index, line in enumerate(region):
        match = _ENTRY.match(line.rstrip("\r\n"))
        if match and match.group("link").startswith(own):
            existing.setdefault(match.group("link"), line.rstrip("\r\n"))
            positions.append(index)
    block = [f"{existing.get(entry.link, entry.line)}{newline}" for entry in ordered]
    if positions:
        first, last = positions[0], positions[-1]
        between = [line for index, line in enumerate(region[first : last + 1], start=first) if index not in positions]
        region = region[:first] + block + between + region[last + 1 :]
    else:
        lead = 0
        while lead < len(region) and not region[lead].strip():
            lead += 1
        if lead == 0:
            block = [newline, *block]
        region = region[:lead] + block + region[lead:]
    return "".join(lines[: heading + 1] + region + lines[end:])


__all__ = [
    "CHANGELOG_TYPE",
    "ENTRIES_HEADING",
    "ChangelogEntry",
    "changelog_path",
    "entries_from_bundle",
    "entry_for",
    "reconcile_changelog",
]
