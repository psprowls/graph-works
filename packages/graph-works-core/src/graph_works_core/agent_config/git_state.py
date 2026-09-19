"""Git state for a project-rooted agent configuration file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from graph_works_core.agent_config.records import GitState
from graph_works_core.workspace.provenance import GitOutcome, probe_git

_CAUSES = {"missing": "git-missing", "timeout": "git-timeout", "error": "git-error"}


class GitProbe(Protocol):
    """The git-state seam used by the agent-config reader."""

    def state(self, project: Path, relative: str) -> tuple[GitState, str | None]: ...


@dataclass(frozen=True, slots=True)
class RepositoryContext:
    """Resolved checkout/main identity; no roots with no error means outside git."""

    root: Path | None = None
    main: Path | None = None
    error: str | None = None


@runtime_checkable
class ContextGitProbe(GitProbe, Protocol):
    def context(self, project: Path) -> RepositoryContext: ...


def repository_context(git: GitProbe, project: Path) -> RepositoryContext:
    """Old state-only probes remain usable, but cannot assert repository identity."""
    if isinstance(git, ContextGitProbe):
        return git.context(project)
    return RepositoryContext(error="git probe does not provide repository identity")


class SubprocessGit:
    """The real git probe; tracked paths take precedence over ignore rules."""

    def __init__(self, executable: str = "git") -> None:
        self._executable = executable

    def _run(self, project: Path, *args: str) -> GitOutcome:
        return probe_git(project, *args, executable=self._executable)

    def context(self, project: Path) -> RepositoryContext:
        top = self._run(project, "rev-parse", "--show-toplevel")
        if top.cause != "ok":
            return RepositoryContext(error=_CAUSES[top.cause])
        if top.returncode != 0:
            # Other failures (dubious ownership, corruption, localized errors) are
            # uncertain. Never infer "not a repository" from any nonzero status.
            if "not a git repository" in top.stderr.lower():
                return RepositoryContext()
            return RepositoryContext(error="git-error")
        listing = self._run(project, "worktree", "list", "--porcelain", "-z")
        if listing.cause != "ok":
            return RepositoryContext(error=_CAUSES[listing.cause])
        first = listing.stdout.split("\0", 1)[0]
        if listing.returncode != 0 or not first.startswith("worktree ") or not top.stdout.strip():
            return RepositoryContext(error="git worktree identity unavailable")
        try:
            root = Path(top.stdout.strip()).resolve()
            main = Path(first.removeprefix("worktree ")).resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            return RepositoryContext(error=f"git path could not be resolved: {exc}")
        return RepositoryContext(root, main)

    def state(self, project: Path, relative: str) -> tuple[GitState, str | None]:
        inside = self._run(project, "rev-parse", "--is-inside-work-tree")
        if inside.cause != "ok":
            return "unknown", _CAUSES[inside.cause]
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return "unknown", "not-a-repository"

        tracked = self._run(project, "ls-files", "--error-unmatch", "--", relative)
        if tracked.cause != "ok":
            return "unknown", _CAUSES[tracked.cause]
        if tracked.returncode == 0:
            return "committed", None
        if tracked.returncode != 1:
            return "unknown", "git-error"

        ignored = self._run(project, "check-ignore", "-q", "--", relative)
        if ignored.cause != "ok":
            return "unknown", _CAUSES[ignored.cause]
        if ignored.returncode == 0:
            return "ignored", None
        if ignored.returncode == 1:
            return "untracked", None
        return "unknown", "git-error"
