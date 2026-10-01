"""The only module in this package that runs a process: git, handed in by the caller.

The caller (graph-works-core) resolves the executable (`toolchain.git`, `GW_GIT`, PATH) and the environment. This
module reads neither. Every call has a timeout, and every failure is a `GitFailure` value, never an exception. That
is the shape of `code_wiki_okf.git_state`, but strict rather than best-effort.

`GIT_TERMINAL_PROMPT=0` is always set, so a private URL fails instead of waiting on a prompt nobody sees. Reads
that must not reach the network (`has_commit`, the range diff) also set `GIT_NO_LAZY_FETCH=1`: in a partial clone,
a missing object would otherwise be fetched silently.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from repositories_okf.pin import is_sha

#: Seconds for a local read or an `ls-remote`.
SHORT_TIMEOUT = 60.0
#: Seconds for clone, fetch and checkout, which move objects over the network.
LONG_TIMEOUT = 1800.0
#: git's well-known empty tree: the diff base when two histories share no commit.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

_NOT_FOUND_MARKERS = (
    "couldn't find remote ref",
    "not our ref",
    "unadvertised object",
    "no such remote ref",
    "not a valid object name",
    "reference is not a tree",
)

GitFailureCause = Literal["missing", "timeout", "nonzero", "not-found"]
ChangeStatus = Literal["A", "M", "D", "R", "T"]


@dataclass(frozen=True, slots=True)
class GitFailure:
    """Why a git call did not answer. `not-found` means the remote or repository lacks the ref or object asked for."""

    cause: GitFailureCause
    command: str
    detail: str


@dataclass(frozen=True, slots=True)
class Git:
    """The git to run and the environment to run it in, both chosen by the caller."""

    executable: str
    environ: Mapping[str, str]

    def env(self, *, lazy_fetch: bool = True) -> dict[str, str]:
        env = {**self.environ, "GIT_TERMINAL_PROMPT": "0"}
        if not lazy_fetch:
            env["GIT_NO_LAZY_FETCH"] = "1"
        return env


@dataclass(frozen=True, slots=True)
class HeadState:
    commit: str | None
    detached: bool


@dataclass(frozen=True, slots=True)
class CommitFacts:
    commit: str
    tree: str
    commit_date: str


@dataclass(frozen=True, slots=True)
class FileChange:
    """One line of `diff --name-status`. For `R`, `path` is the old name and `renamed_to` the new one."""

    status: ChangeStatus
    path: str
    renamed_to: str | None = None


@dataclass(frozen=True, slots=True)
class RangeFacts:
    """What changed between the old pin and the new commit. `base` is `None` only when the histories share no commit."""

    old: str
    new: str
    base: str | None
    rewritten: bool
    commits: int
    changes: tuple[FileChange, ...]
    merges: tuple[str, ...]
    tags: tuple[str, ...]

    @property
    def files_changed(self) -> int:
        return len(self.changes)


@dataclass(frozen=True, slots=True)
class _Ran:
    returncode: int
    stdout: str
    stderr: str


def _run(git: Git, cwd: Path, *args: str, timeout: float = SHORT_TIMEOUT, lazy_fetch: bool = True) -> _Ran | GitFailure:
    command = "git " + " ".join(args)
    try:
        done = subprocess.run(
            [git.executable, *args],
            cwd=str(cwd),
            env=git.env(lazy_fetch=lazy_fetch),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return GitFailure("timeout", command, f"no answer in {timeout:g}s")
    except OSError as exc:
        return GitFailure("missing", command, str(exc))
    return _Ran(done.returncode, done.stdout, done.stderr)


def _failure(args: tuple[str, ...], ran: _Ran) -> GitFailure:
    lowered = ran.stderr.lower()
    cause: GitFailureCause = "not-found" if any(marker in lowered for marker in _NOT_FOUND_MARKERS) else "nonzero"
    return GitFailure(cause, "git " + " ".join(args), ran.stderr.strip()[:500] or f"exit {ran.returncode}")


def _checked(
    git: Git, cwd: Path, *args: str, timeout: float = SHORT_TIMEOUT, lazy_fetch: bool = True
) -> str | GitFailure:
    ran = _run(git, cwd, *args, timeout=timeout, lazy_fetch=lazy_fetch)
    if isinstance(ran, GitFailure):
        return ran
    if ran.returncode != 0:
        return _failure(args, ran)
    return ran.stdout


def default_branch(git: Git, url: str, *, cwd: Path) -> str | GitFailure:
    """The branch the remote's `HEAD` points at (`ls-remote --symref`)."""
    out = _checked(git, cwd, "ls-remote", "--symref", url, "HEAD")
    if isinstance(out, GitFailure):
        return out
    for line in out.splitlines():
        if line.startswith("ref: ") and line.endswith("\tHEAD"):
            return line[len("ref: ") : -len("\tHEAD")].removeprefix("refs/heads/")
    return GitFailure("not-found", "git ls-remote --symref", f"{url} advertises no default branch")


def resolve_ref(git: Git, url: str, ref: str, *, cwd: Path) -> str | GitFailure:
    """The commit *ref* names on the remote. A full SHA is returned as-is: `materialize` verifies it by fetching it.

    Tags win over branches, as in `git rev-parse`, and an annotated tag is peeled to its commit.
    """
    if is_sha(ref):
        return ref
    out = _checked(git, cwd, "ls-remote", url, ref, f"{ref}^{{}}")
    if isinstance(out, GitFailure):
        return out
    advertised: dict[str, str] = {}
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        if name:
            advertised[name] = sha
    for name in (f"refs/tags/{ref}^{{}}", f"refs/tags/{ref}", f"refs/heads/{ref}", f"{ref}^{{}}", ref):
        if name in advertised:
            return advertised[name]
    return GitFailure("not-found", f"git ls-remote {ref}", f"{url} has no ref {ref!r}")


def clone_partial(git: Git, url: str, dest: Path) -> GitFailure | None:
    """`clone --filter=blob:none --no-checkout` into *dest*, which must not exist."""
    out = _checked(
        git,
        dest.parent,
        "clone",
        "--quiet",
        "--filter=blob:none",
        "--no-checkout",
        "--",
        url,
        str(dest),
        timeout=LONG_TIMEOUT,
    )
    return out if isinstance(out, GitFailure) else None


def fetch(git: Git, repo: Path, ref: str) -> str | GitFailure:
    """Fetch *ref* (a branch, a tag or a full SHA) from `origin` and return the commit it names. HEAD never moves."""
    fetched = _checked(git, repo, "fetch", "--quiet", "--tags", "origin", ref, timeout=LONG_TIMEOUT)
    if isinstance(fetched, GitFailure):
        return fetched
    out = _checked(git, repo, "rev-parse", "--verify", "--quiet", "FETCH_HEAD^{commit}")
    return out if isinstance(out, GitFailure) else out.strip()


def has_commit(git: Git, repo: Path, sha: str) -> bool:
    """Whether the commit object is already local. Never fetches."""
    ran = _run(git, repo, "cat-file", "-e", f"{sha}^{{commit}}", lazy_fetch=False)
    return not isinstance(ran, GitFailure) and ran.returncode == 0


def detach(git: Git, repo: Path, commit: str) -> GitFailure | None:
    """`checkout --detach` at *commit*; the partial clone fetches that commit's blobs here."""
    out = _checked(
        git,
        repo,
        "-c",
        "advice.detachedHead=false",
        "checkout",
        "--quiet",
        "--no-overwrite-ignore",
        "--detach",
        commit,
        timeout=LONG_TIMEOUT,
    )
    return out if isinstance(out, GitFailure) else None


def head_state(git: Git, repo: Path) -> HeadState | GitFailure:
    ran = _run(git, repo, "rev-parse", "--verify", "--quiet", "HEAD")
    if isinstance(ran, GitFailure):
        return ran
    commit = ran.stdout.strip() if ran.returncode == 0 and ran.stdout.strip() else None
    symbolic = _run(git, repo, "symbolic-ref", "--quiet", "HEAD")
    if isinstance(symbolic, GitFailure):
        return symbolic
    return HeadState(commit=commit, detached=symbolic.returncode != 0)


def origin_url(git: Git, repo: Path) -> str | GitFailure | None:
    ran = _run(git, repo, "config", "--get", "remote.origin.url")
    if isinstance(ran, GitFailure):
        return ran
    if ran.returncode == 1:
        return None
    if ran.returncode != 0:
        return _failure(("config", "--get", "remote.origin.url"), ran)
    return ran.stdout.strip()


def is_clean(git: Git, repo: Path) -> bool | GitFailure:
    """No tracked change and no untracked file: `restore` and `advance` never discard work."""
    out = _checked(git, repo, "status", "--porcelain=v1", "--untracked-files=all")
    return out if isinstance(out, GitFailure) else not out.strip()


@dataclass(frozen=True, slots=True)
class Worktree:
    """One entry of `git worktree list --porcelain -z`. `branch` is the short name, or None when detached."""

    path: str
    head: str | None
    branch: str | None


def worktree_list(git: Git, repo: Path) -> tuple[Worktree, ...] | GitFailure:
    """Every worktree of *repo*'s repository, the main one first, in git's order."""
    out = _checked(git, repo, "worktree", "list", "--porcelain", "-z")
    if isinstance(out, GitFailure):
        return out
    found: list[Worktree] = []
    for block in out.split("\0\0"):
        fields: dict[str, str] = {}
        for field in block.split("\0"):
            key, _, value = field.partition(" ")
            fields[key] = value
        if "worktree" in fields:
            branch = fields.get("branch")
            short_branch = branch.removeprefix("refs/heads/") if branch else None
            found.append(Worktree(fields["worktree"], fields.get("HEAD") or None, short_branch))
    return tuple(found)


def worktree_root(git: Git, directory: Path) -> Path | GitFailure:
    """The actual worktree root containing *directory*, including for a nested path."""
    out = _checked(git, directory, "rev-parse", "--show-toplevel")
    return out if isinstance(out, GitFailure) else Path(out.strip()).resolve()


def common_dir(git: Git, directory: Path) -> Path | GitFailure:
    """The shared Git directory for a clone or linked checkout."""
    out = _checked(git, directory, "rev-parse", "--git-common-dir")
    return out if isinstance(out, GitFailure) else (directory / out.strip()).resolve()


def branch_tip(git: Git, repo: Path, branch: str) -> str | GitFailure:
    """The commit the local *branch* names. Never fetches: a managed clone and its checkout share refs."""
    out = _checked(git, repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}")
    if isinstance(out, GitFailure):
        return GitFailure("not-found", out.command, f"no local branch {branch!r}") if out.cause == "nonzero" else out
    return out.strip()


def worktree_add(git: Git, repo: Path, dest: Path, branch: str, *, force_stale: bool = False) -> GitFailure | None:
    """Link *dest* on *branch*; force only a known stale registration at a missing path."""
    if force_stale and (dest.exists() or dest.is_symlink()):
        return GitFailure("nonzero", "git worktree add", f"destination {dest} already exists")
    created: list[Path] = []
    try:
        missing_parents: list[Path] = []
        parent = dest.parent
        while not parent.exists():
            missing_parents.append(parent)
            parent = parent.parent
        for parent in reversed(missing_parents):
            parent.mkdir()
            created.append(parent)
    except OSError as exc:
        failure = GitFailure("nonzero", "git worktree add", str(exc))
    else:
        local = branch_tip(git, repo, branch)
        if isinstance(local, GitFailure) and local.cause != "not-found":
            failure = local
        else:
            force = ("--force",) if force_stale else ()
            args: tuple[str, ...] = (
                ("worktree", "add", "--quiet", *force, str(dest), branch)
                if not isinstance(local, GitFailure)
                else ("worktree", "add", "--quiet", *force, "--track", "-b", branch, str(dest), f"origin/{branch}")
            )
            out = _checked(git, repo, *args, timeout=LONG_TIMEOUT)
            if not isinstance(out, GitFailure):
                return None
            failure = out
    for parent in reversed(created):
        try:
            parent.rmdir()
        except OSError:
            break  # Keep a directory that another process populated.
    return failure


def worktree_remove(git: Git, repo: Path, dest: Path) -> GitFailure | None:
    """Unlink the worktree at *dest*, discarding changes, and prune git's record of it."""
    out = _checked(git, repo, "worktree", "remove", "--force", str(dest))
    if isinstance(out, GitFailure):
        return out
    pruned = _checked(git, repo, "worktree", "prune")
    return pruned if isinstance(pruned, GitFailure) else None


def toplevel(git: Git, directory: Path) -> Path | GitFailure:
    """The top level of the work tree containing *directory*, resolved."""
    return worktree_root(git, directory)


def is_linked_worktree(git: Git, directory: Path) -> bool | GitFailure:
    """Whether *directory* is a linked worktree rather than a repository's primary checkout."""
    ran = _checked(git, directory, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir")
    if isinstance(ran, GitFailure):
        return ran
    git_dir, common = ran.splitlines()[:2]
    return Path(git_dir).resolve() != Path(common).resolve()


def is_pristine(git: Git, directory: Path) -> bool | GitFailure:
    """No tracked change and no untracked file. Ignored files (`.venv`, `node_modules`) do not count."""
    return is_clean(git, directory)


def set_config(git: Git, repo: Path, key: str, value: str) -> GitFailure | None:
    ran = _checked(git, repo, "config", "--local", key, value)
    return ran if isinstance(ran, GitFailure) else None


def attach(git: Git, repo: Path, branch: str) -> GitFailure | None:
    """Check out local *branch* (the undo of `detach`)."""
    ran = _checked(git, repo, "switch", "--quiet", branch)
    return ran if isinstance(ran, GitFailure) else None


def worktree_repair(git: Git, repo: Path, paths: Sequence[Path]) -> GitFailure | None:
    """Re-link *paths* with the repository at *repo*, in both directions (after either side moved)."""
    if not paths:
        return None
    ran = _checked(git, repo, "worktree", "repair", *(str(path) for path in paths))
    return ran if isinstance(ran, GitFailure) else None


def current_branch(git: Git, directory: Path) -> str | GitFailure | None:
    """The branch checked out in *directory*, or None when its HEAD is detached."""
    ran = _run(git, directory, "symbolic-ref", "--quiet", "--short", "HEAD")
    if isinstance(ran, GitFailure):
        return ran
    if ran.returncode == 1:
        probe = _checked(git, directory, "rev-parse", "--verify", "--quiet", "HEAD")
        return probe if isinstance(probe, GitFailure) else None
    if ran.returncode != 0:
        return _failure(("symbolic-ref", "--quiet", "--short", "HEAD"), ran)
    return ran.stdout.strip()


def rev_count(git: Git, repo: Path, old: str, new: str) -> int | GitFailure:
    """How many commits *new* has that *old* does not."""
    out = _checked(git, repo, "rev-list", "--count", f"{old}..{new}", lazy_fetch=False)
    if isinstance(out, GitFailure):
        return out
    return int(out.strip() or "0")


def describe(git: Git, repo: Path, commit: str) -> str | None:
    """`git describe --tags`, or `None` when no tag reaches *commit*."""
    ran = _run(git, repo, "describe", "--tags", commit)
    if isinstance(ran, GitFailure) or ran.returncode != 0:
        return None
    return ran.stdout.strip() or None


def commit_facts(git: Git, repo: Path, commit: str) -> CommitFacts | GitFailure:
    out = _checked(git, repo, "show", "-s", "--format=%H%x00%T%x00%cI", commit)
    if isinstance(out, GitFailure):
        return out
    sha, tree, when = out.strip().split("\0")
    return CommitFacts(commit=sha, tree=tree, commit_date=when)


def _parse_name_status(out: str) -> tuple[FileChange, ...]:
    tokens = out.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    changes: list[FileChange] = []
    index = 0
    while index < len(tokens):
        letter = tokens[index][:1]
        if letter in ("R", "C"):
            old, new = tokens[index + 1], tokens[index + 2]
            changes.append(FileChange("R", old, new) if letter == "R" else FileChange("A", new))
            index += 3
            continue
        status: ChangeStatus = letter if letter in ("A", "M", "D", "T") else "M"  # type: ignore[assignment]
        changes.append(FileChange(status, tokens[index + 1]))
        index += 2
    return tuple(changes)


def range_facts(git: Git, repo: Path, old: str, new: str) -> RangeFacts | GitFailure:
    """Range facts from the merge base of *old* and *new* (D-004). Both commits must already be local."""
    ancestor = _run(git, repo, "merge-base", "--is-ancestor", old, new, lazy_fetch=False)
    if isinstance(ancestor, GitFailure):
        return ancestor
    if ancestor.returncode == 0:
        base: str | None = old
        rewritten = False
    elif ancestor.returncode == 1:
        rewritten = True
        merged = _run(git, repo, "merge-base", old, new, lazy_fetch=False)
        if isinstance(merged, GitFailure):
            return merged
        if merged.returncode not in (0, 1):
            return _failure(("merge-base", old, new), merged)
        base = merged.stdout.strip() if merged.returncode == 0 else None
    else:
        return _failure(("merge-base", "--is-ancestor", old, new), ancestor)
    span = f"{base}..{new}" if base else new
    count = _checked(git, repo, "rev-list", "--count", span, lazy_fetch=False)
    if isinstance(count, GitFailure):
        return count
    diff = _checked(
        git, repo, "diff", "--name-status", "-z", "--find-renames=100%", base or EMPTY_TREE, new, lazy_fetch=False
    )
    if isinstance(diff, GitFailure):
        return diff
    merges = _checked(git, repo, "log", "--first-parent", "--merges", "--format=%s", span, lazy_fetch=False)
    if isinstance(merges, GitFailure):
        return merges
    tag_args = ["for-each-ref", "--sort=creatordate", "--format=%(refname:short)", "--merged", new]
    if base:
        tag_args += ["--no-merged", base]
    tags = _checked(git, repo, *tag_args, "refs/tags", lazy_fetch=False)
    if isinstance(tags, GitFailure):
        return tags
    return RangeFacts(
        old=old,
        new=new,
        base=base,
        rewritten=rewritten,
        commits=int(count.strip() or "0"),
        changes=_parse_name_status(diff),
        merges=tuple(line for line in merges.splitlines() if line),
        tags=tuple(line for line in tags.splitlines() if line),
    )


def _clear_readonly(function: Callable[..., object], path: str, _exc: BaseException) -> None:
    Path(path).chmod(stat.S_IWRITE | stat.S_IREAD)
    function(path)


def remove_tree(path: Path) -> None:
    """`rmtree` that also removes git's read-only pack files on Windows. Absent is a no-op."""
    if path.exists() or path.is_symlink():
        shutil.rmtree(path, onexc=_clear_readonly)


def materialize(git: Git, url: str, clone_dir: Path, commit: str, *, incoming: Path) -> GitFailure | None:
    """Clone *url* into *incoming*, detach at *commit*, then move it to *clone_dir*.

    *incoming* is caller-chosen scratch space outside the bundle. Both paths must be absent and are reserved
    exclusively. Existing paths are left untouched; failures clean only directories reserved by this call.
    Filesystem and cleanup failures are returned as values.
    """
    owned_incoming = False
    owned_clone = False
    failure: GitFailure | None = None
    try:
        if clone_dir.exists() or clone_dir.is_symlink():
            return GitFailure("nonzero", "git materialize", f"destination already exists: {clone_dir}")
        incoming.parent.mkdir(parents=True, exist_ok=True)
        incoming.mkdir()
        owned_incoming = True
        failure = clone_partial(git, url, incoming)
        if failure is None and not has_commit(git, incoming, commit):
            fetched = fetch(git, incoming, commit)
            failure = fetched if isinstance(fetched, GitFailure) else None
        if failure is None:
            failure = detach(git, incoming, commit)
        if failure is None:
            clone_dir.parent.mkdir(parents=True, exist_ok=True)
            clone_dir.mkdir()
            owned_clone = True
            # Reserve the destination exclusively. Moving the incoming root into an
            # existing directory would nest it; transfer its children into our reservation.
            for child in incoming.iterdir():
                shutil.move(str(child), str(clone_dir / child.name))
            incoming.rmdir()
            owned_incoming = False
            return None
    except OSError as exc:
        failure = GitFailure("nonzero", "git materialize", str(exc))
    for owned, path in ((owned_clone, clone_dir), (owned_incoming, incoming)):
        if owned:
            try:
                remove_tree(path)
            except OSError as exc:
                detail = failure.detail if failure is not None else "materialization failed"
                failure = GitFailure("nonzero", "git materialize", f"{detail}; cleanup failed: {exc}")
    return failure


__all__ = [
    "EMPTY_TREE",
    "LONG_TIMEOUT",
    "SHORT_TIMEOUT",
    "CommitFacts",
    "FileChange",
    "Git",
    "GitFailure",
    "HeadState",
    "RangeFacts",
    "Worktree",
    "attach",
    "branch_tip",
    "clone_partial",
    "commit_facts",
    "common_dir",
    "current_branch",
    "default_branch",
    "describe",
    "detach",
    "fetch",
    "has_commit",
    "head_state",
    "is_clean",
    "is_linked_worktree",
    "is_pristine",
    "materialize",
    "origin_url",
    "range_facts",
    "remove_tree",
    "resolve_ref",
    "rev_count",
    "set_config",
    "toplevel",
    "worktree_add",
    "worktree_list",
    "worktree_remove",
    "worktree_repair",
    "worktree_root",
]
