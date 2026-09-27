"""Immutable Git evidence for one declared code repository.

The orchestration shell gathers this once per Git common directory. Planning
then receives plain values and never probes Git or the filesystem itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
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
    # Declared checkouts and every inventoried worktree, including undeclared anchors.
    checkout_usable_by_path: Mapping[str, bool] = MappingProxyType({})
    identity_known: bool = True
    branches: frozenset[str] = frozenset()
    branches_known: bool = True
    branch_tips: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    branch_tips_known: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "branches", frozenset(self.branches))
        object.__setattr__(self, "inventory", MappingProxyType(dict(self.inventory)))
        object.__setattr__(self, "path_exists", MappingProxyType(dict(self.path_exists)))
        object.__setattr__(self, "checkout_usable_by_path", MappingProxyType(dict(self.checkout_usable_by_path)))
        object.__setattr__(self, "branch_tips", MappingProxyType(dict(self.branch_tips)))


def _canonical(path: Path) -> str:
    return str(path.resolve())


def _exists(path: str) -> bool | None:
    try:
        return Path(path).is_dir()
    except OSError:
        return None


def repository_identity(repo: Path) -> str | None:
    """Canonical common directory, or `None` when Git cannot prove one."""
    common = probe_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common.returncode == 0 and common.stdout.strip():
        return _canonical(Path(common.stdout.strip()))
    return None


def observe_branch_tips(repo: Path) -> Mapping[str, str] | None:
    """Return local branch names and committed object IDs, or `None` when Git cannot answer."""
    refs = probe_git(repo, "for-each-ref", "--format=%(refname:short)%00%(objectname)", "refs/heads/")
    if refs.returncode != 0:
        return None
    tips: dict[str, str] = {}
    for line in refs.stdout.splitlines():
        name, _, oid = line.partition("\0")
        if name and oid:
            tips[name] = oid
    return MappingProxyType(tips)


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


def observe_repository(
    repo: Path, *, paths: Iterable[Path] = (), checkouts: Iterable[Path] = (), identity: str | None = None
) -> RepositoryContext:
    """Observe identity, checkout safety, worktrees and selected stamp paths."""
    canonical = _canonical(repo)
    proven_identity = identity if identity is not None else repository_identity(repo)
    selected_checkouts = {canonical, *(_canonical(path) for path in checkouts)}
    listed = probe_git(repo, "worktree", "list", "--porcelain")
    known = listed.returncode == 0
    inventory = _inventory(listed.stdout) if known else MappingProxyType({})
    tips = observe_branch_tips(repo)
    candidates = selected_checkouts | {_canonical(path) for path in paths}
    for members in inventory.values():
        candidates.update(members)
    eligibility: dict[str, bool] = {}
    inventoried_checkouts = selected_checkouts | {path for members in inventory.values() for path in members}
    for checkout in inventoried_checkouts:
        status = probe_git(Path(checkout), "status", "--porcelain")
        eligibility[checkout] = status.returncode == 0 and not status.stdout.strip() and _exists(checkout) is True
    observed = MappingProxyType({path: _exists(path) for path in candidates})
    return RepositoryContext(
        identity=proven_identity or canonical,
        path=canonical,
        default_base=default_base(repo),
        checkout_usable=eligibility[canonical],
        inventory=inventory,
        path_exists=observed,
        inventory_known=known,
        checkout_usable_by_path=eligibility,
        identity_known=proven_identity is not None,
        branches=frozenset(tips) if tips is not None else frozenset(),
        branches_known=tips is not None,
        branch_tips=tips or {},
        branch_tips_known=tips is not None,
    )
