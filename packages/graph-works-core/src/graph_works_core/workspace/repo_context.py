"""Immutable Git evidence for one declared code repository.

The orchestration shell gathers this once per Git common directory. Planning
then receives plain values and never probes Git or the filesystem itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from graph_works_core.workspace.provenance import default_base, probe_git


@dataclass(frozen=True, slots=True)
class RepositoryContext:
    identity: str
    path: str
    default_base: str
    checkout_usable: bool
    inventory: Mapping[str, tuple[str, ...]]
    path_exists: Mapping[str, bool | None]
    inventory_known: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "inventory", MappingProxyType(dict(self.inventory)))
        object.__setattr__(self, "path_exists", MappingProxyType(dict(self.path_exists)))


def _canonical(path: Path) -> str:
    return str(path.resolve())


def _exists(path: str) -> bool | None:
    try:
        return Path(path).is_dir()
    except OSError:
        return None


def repository_identity(repo: Path) -> str:
    """Canonical common directory, or a path-scoped unavailable identity."""
    common = probe_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common.returncode == 0 and common.stdout.strip():
        return _canonical(Path(common.stdout.strip()))
    return _canonical(repo)


def _inventory(output: str) -> Mapping[str, tuple[str, ...]]:
    branches: dict[str, list[str]] = {}
    path: str | None = None
    branch: str | None = None
    prunable = False

    def finish() -> None:
        if path and branch and not prunable:
            observed = branches.setdefault(branch, [])
            canonical = _canonical(Path(path))
            if canonical not in observed:
                observed.append(canonical)

    for line in (*output.splitlines(), ""):
        if not line:
            finish()
            path = branch = None
            prunable = False
        elif line.startswith("worktree "):
            path = line[len("worktree ") :]
        elif line.startswith("branch "):
            ref = line[len("branch ") :]
            branch = ref.removeprefix("refs/heads/")
        elif line.startswith("prunable"):
            prunable = True
    return MappingProxyType({name: tuple(paths) for name, paths in branches.items()})


def observe_repository(repo: Path, *, paths: Iterable[Path] = (), identity: str | None = None) -> RepositoryContext:
    """Observe identity, checkout safety, worktrees and selected stamp paths."""
    canonical = _canonical(repo)
    identity = identity or repository_identity(repo)
    status = probe_git(repo, "status", "--porcelain")
    usable = status.returncode == 0 and not status.stdout.strip() and _exists(canonical) is True
    listed = probe_git(repo, "worktree", "list", "--porcelain")
    known = listed.returncode == 0
    inventory = _inventory(listed.stdout) if known else MappingProxyType({})
    candidates = {canonical, *(_canonical(path) for path in paths)}
    for members in inventory.values():
        candidates.update(members)
    observed = MappingProxyType({path: _exists(path) for path in candidates})
    return RepositoryContext(
        identity=identity,
        path=canonical,
        default_base=default_base(repo),
        checkout_usable=usable,
        inventory=inventory,
        path_exists=observed,
        inventory_known=known,
    )
