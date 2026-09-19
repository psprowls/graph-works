"""Read only the project-trust decision from an agent's trust record."""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from plugin_fork_io.adapters import TrustRecordSpec

from .git_state import RepositoryContext, SubprocessGit
from .records import Finding, TrustStatus

type _Decision = bool | Literal["invalid"] | None


def _norm(raw: str) -> str:
    return os.path.normcase(str(Path(raw).resolve()))


def _load(path: Path, *, toml: bool) -> object:
    text = path.read_bytes().decode("utf-8")
    return tomllib.loads(text) if toml else json.loads(text)


def _claude(entry: object) -> _Decision:
    if not isinstance(entry, dict):
        return "invalid"
    if "hasTrustDialogAccepted" not in entry:
        return None
    value = entry["hasTrustDialogAccepted"]
    return value if isinstance(value, bool) else "invalid"


def _codex(entry: object) -> _Decision:
    if not isinstance(entry, dict):
        return "invalid"
    if "trust_level" not in entry:
        return None
    value = entry["trust_level"]
    if value == "trusted":
        return True
    if value == "untrusted":
        return False
    return "invalid"


def _pi(entry: object) -> _Decision:
    if entry is None:
        return None
    return entry if isinstance(entry, bool) else "invalid"


_SHAPES: dict[str, tuple[bool, Callable[[object], _Decision]]] = {
    "claude-projects": (True, _claude),
    "codex-projects": (True, _codex),
    "pi-map": (False, _pi),
}


def _unknown(path: Path | None, message: str, key: str | None = None) -> tuple[TrustStatus, tuple[Finding, ...]]:
    return (
        TrustStatus("unknown", path, key, None, False),
        (Finding("agent-config.trust", message, str(path) if path is not None else None),),
    )


def _table(data: object, *, projects: bool) -> dict[str, object] | None:
    if not isinstance(data, dict):
        raise ValueError("Trust record has an unexpected top-level shape")
    if not projects:
        return data
    if "projects" not in data:
        return None
    table = data["projects"]
    if not isinstance(table, dict):
        raise ValueError("Trust record projects has an unexpected shape")
    return table


def read_trust(
    spec: TrustRecordSpec | None,
    record_path: Path | None,
    project: Path,
    *,
    context: RepositoryContext | None = None,
) -> tuple[TrustStatus, tuple[Finding, ...]]:
    """Return the nearest supported trust decision without raising for content errors."""
    if spec is None or record_path is None:
        return _unknown(None, "This agent has no known trust record")
    try:
        resolved_project = project.resolve()
        normalized_project = tuple(
            os.path.normcase(str(path)) for path in (resolved_project, *resolved_project.parents)
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return _unknown(record_path, f"Project path could not be resolved: {exc}")
    try:
        data = _load(record_path, toml=spec.shape == "codex-projects")
        projects, decide = _SHAPES[spec.shape]
        table = _table(data, projects=projects)
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        return _unknown(record_path, f"Trust record could not be read: {exc}")
    if spec.shape != "pi-map":
        context = context if context is not None else SubprocessGit().context(resolved_project)
        if context.error:
            return _unknown(record_path, f"Repository identity is uncertain: {context.error}")
    if table is None:
        return TrustStatus("unrecorded", record_path, None, None, False), ()

    entries: dict[str, tuple[str, object]] = {}
    try:
        for key, value in table.items():
            raw_key = str(key)
            entries[_norm(raw_key)] = (raw_key, value)
    except (OSError, RuntimeError, ValueError) as exc:
        return _unknown(record_path, f"Trust record contains an invalid project path: {exc}")

    candidates = normalized_project if spec.inherits else normalized_project[:1]
    repository_key = None
    if spec.shape == "claude-projects" and context is not None and context.root is not None:
        if context.main is None:
            return _unknown(record_path, "Main checkout identity is unavailable")
        repository_key = _norm(str(context.main))
        candidates = (repository_key,)
    elif spec.shape == "codex-projects" and context is not None:
        try:
            project_root = _codex_project_root(data, resolved_project, context)
            candidates = tuple(
                dict.fromkeys(
                    _norm(str(path)) for path in (resolved_project, project_root, context.main) if path is not None
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return _unknown(record_path, f"Project root is uncertain: {exc}")
    for candidate in candidates:
        hit = entries.get(candidate)
        if hit is None:
            continue
        key, value = hit
        decision = decide(value)
        if decision is None:
            continue
        if decision == "invalid":
            return _unknown(record_path, f"Trust entry {key!r} has an unexpected value", key)
        state: Literal["trusted", "untrusted"] = "trusted" if decision else "untrusted"
        return TrustStatus(
            state,
            record_path,
            key,
            "exact" if candidate == normalized_project[0] or candidate == repository_key else "ancestor",
            False,
        ), ()
    return TrustStatus("unrecorded", record_path, None, None, False), ()


def _codex_project_root(data: object, project: Path, context: RepositoryContext) -> Path:
    assert isinstance(data, dict)
    markers = data.get("project_root_markers", [".git"])
    if not isinstance(markers, list) or any(not isinstance(marker, str) for marker in markers):
        raise ValueError("project_root_markers must be an array of strings")
    if markers == [".git"]:
        return context.root or project
    for parent in (project, *project.parents):
        for marker in markers:
            target = parent / marker
            try:
                target.stat()
            except FileNotFoundError:
                continue
            if marker == ".git" and target.is_dir() and not (target / "HEAD").exists():
                continue
            return parent
    return project
