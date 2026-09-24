"""pip requirements-file reading for requirements-based Python roots.

A requirements root (a tracked ``requirements.txt`` with no ``pyproject.toml``
project beside or above it) is admitted as a Package by
``packages._discover_requirements_roots``. This module turns its files into the
raw PEP 508 lines that ``packages._manifest_dependencies`` already consumes.

It follows ``-r`` / ``--requirement`` includes relative to the including file,
with a visited set so an include cycle is a no-op. ``-c`` constraint files are
not followed: constraints pin versions, they do not declare dependencies.
Nothing here raises for content. A missing, undecodable or out-of-repo file is
skipped with one stderr warning, the same convention the other manifest
readers use.
"""

from __future__ import annotations

import codecs
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_DEV_TOKENS = frozenset({"dev", "test", "tests", "lint", "ci", "docs", "typing"})
_DEV_NAME_RE = re.compile(
    r"^(?:requirements[-_](?P<suffix>\w+)|(?P<prefix>\w+)[-_]requirements)\.txt$",
    re.IGNORECASE,
)
# pip treats `#` as a comment at line start or after whitespace.
_COMMENT_RE = re.compile(r"(?:^|\s)#.*$")
# Per-requirement options (`--hash=...`, `--config-settings ...`) trail the spec.
_OPTION_TAIL_RE = re.compile(r"\s+--.*$")


@dataclass(frozen=True, slots=True)
class RequirementsRead:
    runtime: tuple[str, ...]
    dev: tuple[str, ...]
    has_own_requirements: bool
    direct_includes: tuple[Path, ...]
    visited: frozenset[Path]


def is_dev_requirements_name(filename: str) -> bool:
    """``requirements-dev.txt`` / ``dev-requirements.txt`` / ``requirements_test.txt`` …"""
    match = _DEV_NAME_RE.match(filename)
    if match is None:
        return False
    token = match.group("suffix") or match.group("prefix")
    return token.lower() in _DEV_TOKENS


def _is_dev_file(path: Path, base_dir: Path) -> bool:
    if is_dev_requirements_name(path.name):
        return True
    if path.name.lower() != "requirements.txt" and path.stem.lower() in _DEV_TOKENS:
        return True  # requirements/dev.txt
    try:
        rel_parent = path.parent.relative_to(base_dir)
    except ValueError:
        return False
    return any(part.lower() in _DEV_TOKENS for part in rel_parent.parts)


def dev_sibling_files(root_file: Path) -> tuple[Path, ...]:
    """Dev-named requirements files beside *root_file* (included or not)."""
    return tuple(
        sorted(
            candidate.resolve()
            for candidate in root_file.parent.iterdir()
            if candidate.is_file() and is_dev_requirements_name(candidate.name)
        )
    )


def _decode(data: bytes) -> str:
    # pip reads a UTF-16 BOM file (PowerShell 5's `pip freeze >` default).
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16")
    return data.decode("utf-8-sig")


def _logical_lines(text: str) -> Iterator[str]:
    """Join ``\\`` continuations, then drop comments and blank lines (pip's order)."""
    pending = ""
    for raw in text.splitlines():
        if raw.endswith("\\"):
            pending += raw[:-1]
            continue
        line = _COMMENT_RE.sub("", pending + raw).strip()
        pending = ""
        if line:
            yield line
    tail = _COMMENT_RE.sub("", pending).strip()
    if tail:
        yield tail


def _include_target(line: str) -> str | None:
    if line.startswith("--requirement"):
        rest = line[len("--requirement") :]
        if rest[:1] not in {"", " ", "\t", "="}:
            return None
    elif line.startswith("-r"):
        rest = line[len("-r") :]
    else:
        return None
    target = rest.lstrip(" \t=")
    return target or None


def read_requirements(
    path: Path,
    repo_root: Path,
    *,
    dev: bool | None = None,
    skip: frozenset[Path] = frozenset(),
) -> RequirementsRead:
    """Read *path* and every file it includes.

    ``dev=None`` derives the root file's dev-ness from its name. ``dev=True``
    tags every line dev, which is how a dev sibling is read. ``skip`` pre-seeds
    the visited set, so files already read for the root are not re-tagged.
    """
    repo_root = repo_root.resolve()
    root = path.resolve()
    runtime: list[str] = []
    development: list[str] = []
    direct: list[Path] = []
    visited: set[Path] = set(skip)
    own = False

    def walk(file: Path, is_dev: bool) -> None:
        nonlocal own
        if file in visited:
            return
        visited.add(file)
        try:
            text = _decode(file.read_bytes())
        except (OSError, UnicodeDecodeError) as exc:
            print(f"warning: skipping {file} ({exc})", file=sys.stderr)
            return
        for line in _logical_lines(text):
            target = _include_target(line)
            if target is not None:
                included = (file.parent / target).resolve()
                if file == root:
                    direct.append(included)
                if not included.is_relative_to(repo_root):
                    print(
                        f"warning: {file}: include {target!r} resolves outside the repository; skipped",
                        file=sys.stderr,
                    )
                    continue
                if not included.is_file():
                    print(f"warning: {file}: include {target!r} not found; skipped", file=sys.stderr)
                    continue
                walk(included, is_dev or _is_dev_file(included, root.parent))
                continue
            if line.startswith("-"):
                continue  # -c, -e, --index-url, other pip options
            requirement = _OPTION_TAIL_RE.sub("", line).strip()
            if not requirement:
                continue
            (development if is_dev else runtime).append(requirement)
            if file == root:
                own = True

    walk(root, _is_dev_file(root, root.parent) if dev is None else dev)
    return RequirementsRead(
        runtime=tuple(runtime),
        dev=tuple(development),
        has_own_requirements=own,
        direct_includes=tuple(direct),
        visited=frozenset(visited),
    )
