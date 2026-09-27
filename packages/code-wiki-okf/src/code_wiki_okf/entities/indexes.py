"""Conservative cleanup of generated catalogs for vanished source folders.

Only the known heading skeleton and bare generated link bullets are owned.
Descriptions, comments, frontmatter and other prose are deliberately ambiguous.
Plans capture bytes; application unlinks individual indexes, never directories.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from okf_ext.body import sections, split_lines
from okf_io import Bundle, Document

from code_wiki_okf.config import RepoConfig
from code_wiki_okf.placement import file_system_directory


@dataclass(frozen=True, slots=True)
class IndexPruneResult:
    deleted: tuple[str, ...] = ()
    declined: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class IndexPrunePlan:
    result: IndexPruneResult
    captured: tuple[tuple[str, bytes], ...] = ()


def bundle_members(bundle: Bundle) -> frozenset[str]:
    """Every loaded member, including non-concepts that need navigation."""
    return frozenset(
        [f"{key}.md" for key in bundle.concepts]
        + [f"{key}/index.md" if key else "index.md" for key in bundle.indexes]
        + [f"{key}/log.md" if key else "log.md" for key in bundle.logs]
        + list(bundle.assets | bundle.ignored)
        + list(bundle.unreadable)
    )


# Bounded to one whole line, no trailing description or arbitrary prose.
_LINK = re.compile(r"[-*] \[([^\]<>\r\n]+)\]\(([^\s()?#:]+)\)")


def is_generated_folder_index(document: Document, directory: str, file_root: str) -> bool:
    if document.has_frontmatter or document.parse_error is not None:
        return False
    body = document.body
    lines = split_lines(body)
    headings = sections(body)
    allowed = {
        (1, "File"),
        (1, "Directories"),
        (1, "Subdirectories"),
        (2, "Files"),
        (2, "Directories"),
        (2, "Subdirectories"),
    }
    seen: set[tuple[int, str]] = set()
    heading_lines: set[int] = set()
    for section in headings:
        key = (section.level, section.heading)
        if key not in allowed or key in seen:
            return False
        seen.add(key)
        heading_lines.add(section.start)
    current_heading = ""
    for number, line in enumerate(lines, 1):
        text = line.rstrip("\r\n")
        if number in heading_lines:
            # Accept only the exact ATX shapes our writers emit.
            section = next(item for item in headings if item.start == number)
            current_heading = section.heading
            if text != "#" * section.level + " " + section.heading:
                return False
        elif text.strip() and text != "_(none)_":
            match = _LINK.fullmatch(text)
            if match is None or not current_heading:
                return False
            target = match[2]
            resolved = posixpath.normpath(target.lstrip("/") if target.startswith("/") else f"{directory}/{target}")
            if not resolved.startswith(file_root + "/"):
                return False
            is_directory = target.endswith(("/index.md", "/"))
            if current_heading in {"Directories", "Subdirectories"}:
                if not is_directory:
                    return False
            elif is_directory or not target.endswith(".md"):
                return False
    return bool(headings) or not body.strip()


def plan_index_prune(
    bundle: Bundle,
    *,
    repos: tuple[RepoConfig, ...],
    members: Collection[str] | None = None,
) -> IndexPrunePlan:
    """Classify post-sync members under successfully scanned repository roots.

    Callers pass only repositories with successful scan/mirror plans. Missing
    source roots are independently excluded, so absence is never a scan result.
    ``members`` can project removals/creates while index documents retain bytes.
    """
    retained = set(bundle_members(bundle) if members is None else members)
    deleted: list[str] = []
    declined: list[tuple[str, str]] = []
    captured: list[tuple[str, bytes]] = []
    for repo in repos:
        if not repo.path.is_dir():
            continue
        prefix = file_system_directory(repo.name) + "/"
        directories = sorted(
            (directory for directory in bundle.indexes if directory.startswith(prefix)),
            key=lambda directory: (-len(PurePosixPath(directory).parts), directory),
        )
        for directory in directories:
            member = f"{directory}/index.md"
            source = repo.path / directory.removeprefix(prefix)
            # A file at this source path also counts as uncertain: fail closed.
            if source.exists() or source.is_symlink():
                continue
            if any(path != member and path.startswith(directory + "/") for path in retained):
                continue
            document = bundle.indexes[directory]
            if not is_generated_folder_index(document, directory, prefix.rstrip("/")):
                declined.append((member, "unrecognized-content"))
                continue
            deleted.append(member)
            captured.append((member, document.serialize().encode("utf-8")))
            retained.discard(member)
    return IndexPrunePlan(IndexPruneResult(tuple(deleted), tuple(sorted(declined))), tuple(captured))


def apply_index_prune(root: Path, plan: IndexPrunePlan) -> IndexPruneResult:
    """Apply captured candidates, retaining edited indexes and their ancestry."""
    deleted: list[str] = []
    declined = list(plan.result.declined)
    retained: list[str] = []
    for member, before in plan.captured:
        path = root / member
        directory = str(PurePosixPath(member).parent)
        if any(item.startswith(directory + "/") for item in retained):
            declined.append((member, "retained-descendant"))
            continue
        try:
            if path.read_bytes() != before:
                declined.append((member, "changed-since-plan"))
                retained.append(member)
                continue
            path.unlink()
        except OSError:
            declined.append((member, "unavailable-since-plan"))
            retained.append(member)
            continue
        deleted.append(member)
    return IndexPruneResult(tuple(deleted), tuple(sorted(declined)))
