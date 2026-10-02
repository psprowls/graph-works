"""Structural read counters: documents parsed, link graphs built, git processes spawned.

Counts, not timings (D-004): they do not move with host load, so a test can assert
them. `counting()` patches three choke points for the duration of its block and
restores them on exit, including on an exception:

- `okf_io.document.Document.parse` -- the bundle walk, `Document.load` and
  `okf_io.parse` all go through it, so its call count is "files parsed".
- `okf_io.links.LinkGraph` -- constructed exactly once per `build_link_graph`. The
  class is patched rather than `build`, because consumers bind `build_link_graph`
  by name at import time and a wrapper on the module attribute would miss them.
- `subprocess.Popen` -- `subprocess.run` and every helper beneath it (core's
  `probe_git` included) construct one; a process whose program is git counts.

Not thread-aware: a read that spawns worker threads is counted in full, which is
what a per-read count wants, but two concurrent `counting()` blocks are refused.
"""

from __future__ import annotations

import importlib
import os
import shlex
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import PureWindowsPath
from typing import Any

_document = importlib.import_module("okf_io.document")
_links = importlib.import_module("okf_io.links")

_GIT_NAMES = frozenset({"git", "git.exe"})


@dataclass
class Counts:
    files_parsed: int = 0
    link_graph_builds: int = 0
    git_calls: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


_active: Counts | None = None


def _program(args: Any, *, shell: bool, executable: Any) -> str:
    if executable is not None:
        return os.fsdecode(executable)
    if isinstance(args, (str, bytes, os.PathLike)):
        text = os.fsdecode(args)
        if not shell:
            return text
        parts = shlex.split(text)
        return parts[0] if parts else ""
    sequence = list(args)
    return os.fsdecode(sequence[0]) if sequence else ""


def is_git(args: object, *, shell: bool = False, executable: object = None) -> bool:
    """Whether a `Popen(args, shell=..., executable=...)` call runs git.

    `PureWindowsPath` splits on both separators, so a POSIX path and a Windows
    path both reduce to their final component.
    """
    program = _program(args, shell=shell, executable=executable)
    return bool(program) and PureWindowsPath(program).name.lower() in _GIT_NAMES


class _CountingPopen(subprocess.Popen):  # type: ignore[type-arg]
    def __init__(self, args: Any, *rest: Any, **kwargs: Any) -> None:
        if _active is not None and is_git(
            args, shell=bool(kwargs.get("shell", False)), executable=kwargs.get("executable")
        ):
            _active.git_calls += 1
        super().__init__(args, *rest, **kwargs)


@contextmanager
def counting() -> Iterator[Counts]:
    """Count parses, link-graph builds and git processes inside the block."""
    global _active
    if _active is not None:
        raise RuntimeError("counting() is not reentrant")
    counts = Counts()
    document_class = _document.Document
    original_parse = document_class.__dict__["parse"]
    original_graph = _links.LinkGraph
    original_popen = subprocess.Popen

    def parse(cls: type, text: str, *, path: Any = None) -> Any:
        counts.files_parsed += 1
        return original_parse.__func__(cls, text, path=path)

    def link_graph(*args: Any, **kwargs: Any) -> Any:
        counts.link_graph_builds += 1
        return original_graph(*args, **kwargs)

    _active = counts
    document_class.parse = classmethod(parse)
    _links.LinkGraph = link_graph
    subprocess.Popen = _CountingPopen  # type: ignore[misc]
    try:
        yield counts
    finally:
        subprocess.Popen = original_popen  # type: ignore[misc]
        _links.LinkGraph = original_graph
        document_class.parse = original_parse
        _active = None


__all__ = ["Counts", "counting", "is_git"]
