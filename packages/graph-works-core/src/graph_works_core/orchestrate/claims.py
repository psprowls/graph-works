"""Claims: what a live or planned dispatch holds, and when two collide.

Pure and IO-free, like `commands.plan()` above it. A claim is `(scope, mode,
owner)`: *owner* is the canonical work-item path, so an item never conflicts
with itself; *mode* is explicit so a stage policy could define read
claims without changing the predicate. Scopes are tagged by kind -- a code
path, an observed worktree, the workspace -- so values of different kinds can
never compare equal by accident.

Code overlap is containment by path *segment* in either direction:
`packages/a` holds `packages/a/src` but not `packages/ab`. A `CodeScope`
with `path=None` is the whole repository and overlaps every path under the
same Git identity. Worktree and workspace scopes overlap by equality.
`affects_claims` maps an item's `affects` to write claims; `claims_for` is the
stage policy over it -- only `CODE_WRITE_PHASES` (execute, finish) hold them,
design and plan hold none -- and is the only route `plan()` takes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from work_tracker_okf.affects import code_affects, touches_workspace

Mode = Literal["read", "write"]


@dataclass(frozen=True, slots=True)
class CodeScope:
    """A repository-relative POSIX path under one Git common-directory identity."""

    repo: str
    path: str | None  # None: the whole repository


@dataclass(frozen=True, slots=True)
class WorktreeScope:
    """An observed absolute worktree path."""

    path: str


@dataclass(frozen=True, slots=True)
class WorkspaceScope:
    """The singleton workspace."""


WORKSPACE = WorkspaceScope()

Scope = CodeScope | WorktreeScope | WorkspaceScope


@dataclass(frozen=True, slots=True)
class Claim:
    scope: Scope
    mode: Mode
    owner: str


def _parts(path: str) -> tuple[str, ...]:
    # PurePosixPath drops `.` segments and trailing slashes; `..` is kept
    # literally -- an affects entry is a declaration, not a path to resolve.
    return tuple(part for part in PurePosixPath(path.strip()).parts if part != "/")


def paths_overlap(a: str | None, b: str | None) -> bool:
    """Segment-aware containment in either direction; `None` or blank is the whole repository."""
    if a is None or b is None:
        return True
    left, right = _parts(a), _parts(b)
    shared = min(len(left), len(right))
    return left[:shared] == right[:shared]


def scopes_overlap(a: Scope, b: Scope) -> bool:
    if isinstance(a, CodeScope) and isinstance(b, CodeScope):
        return a.repo == b.repo and paths_overlap(a.path, b.path)
    if isinstance(a, WorktreeScope) and isinstance(b, WorktreeScope):
        return a.path == b.path
    return isinstance(a, WorkspaceScope) and isinstance(b, WorkspaceScope)


def conflicts(a: Claim, b: Claim) -> bool:
    """Different owners, both writing, overlapping scopes of the same kind."""
    return a.owner != b.owner and a.mode == "write" and b.mode == "write" and scopes_overlap(a.scope, b.scope)


def first_conflicts(claims: Iterable[Claim], held: Iterable[Claim]) -> tuple[tuple[Claim, Claim], ...]:
    """Every `(mine, theirs)` conflicting pair, in input order."""
    held = tuple(held)
    return tuple((mine, theirs) for mine in claims for theirs in held if conflicts(mine, theirs))


def code_claims(owner: str, pairs: Iterable[tuple[str, str]], mode: Mode = "write") -> tuple[Claim, ...]:
    """`(identity, member)` pairs, as `plan()` has always computed them, as code claims."""
    return tuple(Claim(CodeScope(identity, member), mode, owner) for identity, member in pairs)


def affects_claims(owner: str, identity: str, affects: Iterable[str], mode: Mode = "write") -> tuple[Claim, ...]:
    """An item's `affects` under one Git identity, as claims.

    Each code path is one `CodeScope` claim. `gw:workspace` adds the
    `WORKSPACE` claim. No code path and no `gw:workspace` (empty `affects`)
    is one whole-repository claim: an item that declares nothing serializes
    against every write in its repository rather than never dispatching.
    """
    entries = tuple(affects)
    paths = code_affects(entries)
    claims = [Claim(CodeScope(identity, path), mode, owner) for path in paths]
    if touches_workspace(entries):
        claims.append(Claim(WORKSPACE, mode, owner))
    elif not paths:
        claims.append(Claim(CodeScope(identity, None), mode, owner))
    return tuple(claims)


CODE_WRITE_PHASES: frozenset[str] = frozenset({"execute", "finish"})


def claims_for(owner: str, phase: str, identity: str, affects: Iterable[str]) -> tuple[Claim, ...]:
    """The `affects`-derived claims a stage holds: write claims at execute/finish, none otherwise.

    A positive allow-list (epic D-002): design, plan and any phase not named
    here write no code, so they hold no code claim and no workspace claim --
    even for a `gw:workspace` item, whose workspace write happens at execute.
    """
    return affects_claims(owner, identity, affects) if phase in CODE_WRITE_PHASES else ()
