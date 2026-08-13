"""A directory in, a manifest out — with two severities kept apart.

Legacy conflated them into one untyped channel: a folder over 200 files came
back as `{"_error": ...}`, a key the caller could silently forget to check,
while a folder over 50 files appended a bare string to a `warnings` list. They
are different things, and they stay different here: `FolderRefusal` and
`FolderWarning`, each a closed vocabulary of `kind`s.

**The brief always comes back.** Nothing on the content path raises -- okf-io's
posture, and this package's own two writers'. A refused folder still reports its
file count and total size, the facts that explain the refusal, where legacy
returned the sentinel and nothing else. It does not report the manifest: a
200-file listing is precisely what the refusal declines to produce.

`as_data()` re-emits `_error` and the bare-string `warnings` list, so the
plugin's existing check keeps working against a brief that is now honest
underneath.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from doc_wiki_okf.ingest.layout import resolve_source_path
from doc_wiki_okf.ingest.seams import StateGate, read_state_gate
from doc_wiki_okf.reading import language_for, list_folder_files, pick_representative

#: A file at least this large makes the folder worth a second look.
LARGE_FILE_BYTES = 200 * 1024

#: More files than this warns.
WARN_FILE_COUNT = 50

#: More files than this refuses.
ERROR_FILE_COUNT = 200

#: Why a folder was declined. Closed and small is the design goal, not a
#: placeholder -- the same posture `proposals/migrate.py` takes.
RefusalKind = Literal["folder-too-large"]

#: What is worth saying about a folder that is still briefed.
WarningKind = Literal["folder-size", "large-file"]

#: The bare strings legacy put in `warnings`, keyed by our kind. The hyphenated
#: kind is this package's vocabulary; the underscored string is the plugin's,
#: and `as_data()` owes it verbatim.
LEGACY_WARNING_NAMES: Mapping[WarningKind, str] = MappingProxyType(
    {"folder-size": "folder_size", "large-file": "large_file"}
)


@dataclass(frozen=True, slots=True)
class FolderRefusal:
    """The folder was not briefed, and why."""

    kind: RefusalKind
    detail: str


@dataclass(frozen=True, slots=True)
class FolderWarning:
    """The folder was briefed, with a caveat."""

    kind: WarningKind
    detail: str


@dataclass(frozen=True, slots=True)
class FolderFile:
    """One file in the manifest."""

    path: str  # root-relative, posix
    size: int
    language: str

    def as_data(self) -> dict[str, Any]:
        return {"path": self.path, "size": self.size, "language": self.language}


@dataclass(frozen=True, slots=True)
class FolderBrief:
    """What a directory holds, or why it was declined."""

    root: Path
    file_count: int
    total_size: int
    files: tuple[FolderFile, ...]
    representative_file: str | None
    warnings: tuple[FolderWarning, ...]
    refusals: tuple[FolderRefusal, ...]
    state_gate: Mapping[str, Any] | None

    @property
    def ok(self) -> bool:
        return not self.refusals

    def as_data(self) -> dict[str, Any]:
        """The legacy dict, verbatim -- including the `_error` sentinel."""
        gate = None if self.state_gate is None else dict(self.state_gate)
        if self.refusals:
            return {"is_folder": True, "_error": self.refusals[0].detail, "state_gate": gate}
        return {
            "is_folder": True,
            "file_count": self.file_count,
            "total_size": self.total_size,
            "files": [entry.as_data() for entry in self.files],
            "representative_file": self.representative_file,
            "warnings": [LEGACY_WARNING_NAMES[warning.kind] for warning in self.warnings],
            "state_gate": gate,
        }


def plan_folder_brief(
    source_path: Path,
    *,
    repo: Path,
    workspace_root: Path,
    state_gate: StateGate | None = None,
) -> FolderBrief:
    """Compute the brief for a directory. Writes nothing, and never raises.

    Legacy took a `wiki` it used only to compute a `rel_to_wiki` its callee
    ignored, and derived the state gate's workspace as `wiki.parent`. Both are
    gone: the workspace is named, and the dead parameter is not.
    """
    root = resolve_source_path(source_path, repo)
    entries = list_folder_files(root)
    file_count = len(entries)
    total_size = sum(size for _, size in entries)
    gate = read_state_gate(state_gate, repo, workspace_root)

    if file_count > ERROR_FILE_COUNT:
        detail = f"folder has {file_count} files (>{ERROR_FILE_COUNT}); pass a specific file instead"
        return FolderBrief(
            root=root,
            file_count=file_count,
            total_size=total_size,
            files=(),
            representative_file=None,
            warnings=(),
            refusals=(FolderRefusal(kind="folder-too-large", detail=detail),),
            state_gate=gate,
        )

    warnings: list[FolderWarning] = []
    if file_count > WARN_FILE_COUNT:
        warnings.append(FolderWarning(kind="folder-size", detail=f"folder has {file_count} files (>{WARN_FILE_COUNT})"))
    large = [(rel, size) for rel, size in entries if size > LARGE_FILE_BYTES]
    if large:
        rel, size = large[0]
        warnings.append(FolderWarning(kind="large-file", detail=f"{rel} is {size} bytes (>{LARGE_FILE_BYTES})"))

    return FolderBrief(
        root=root,
        file_count=file_count,
        total_size=total_size,
        files=tuple(FolderFile(path=rel, size=size, language=language_for(Path(rel))) for rel, size in entries),
        representative_file=pick_representative(root, entries),
        warnings=tuple(warnings),
        refusals=(),
        state_gate=gate,
    )
