"""Contract-test inputs for `graph_works_wire.repo`: every `None` / non-`None` branch."""

from __future__ import annotations

from collections.abc import Callable

from graph_works_core.repositories.adopt import RepoAdoptResult
from graph_works_core.repositories.commands import (
    RepoAddResult,
    RepoAdvanceResult,
    RepoRefusal,
    RepoRestoreResult,
    RestoreOutcome,
)
from graph_works_core.workspace.commits import CommitOutcome
from graph_works_wire import repo
from repositories_okf.flagging import FlaggedLink, FlaggedPage
from repositories_okf.git import FileChange, RangeFacts

A, B = "a" * 40, "b" * 40
_COMMIT = CommitOutcome(
    "committed", "c" * 40, "workspace: add reference repository demo", ("okf/repositories/demo.md",), None
)
_RANGE = RangeFacts(
    A, B, None, True, 3, (FileChange("M", "src/a.py"), FileChange("R", "src/b.py", "src/c.py")), ("Merge #1",), ("v1",)
)
_FLAGGED = (FlaggedPage("concepts/x.md", "X", (FlaggedLink("src/", "src/a.py", "M", None),)),)

REPO: dict[str, tuple[Callable[[], object], ...]] = {
    "repo.repo_adopt_payload": (
        lambda: repo.repo_adopt_payload(
            RepoAdoptResult(
                "demo",
                "../demo",
                "okf/repositories/demo/references/git",
                ".gw/worktrees/demo/main",
                "main",
                "0" * 40,
                True,
                ("/ws/.gw/worktrees/legacy/wt0",),
                ("log.md", "repositories/demo.md", "repositories/index.md", "workspace.yaml"),
                False,
                commit_outcome=CommitOutcome(
                    "committed", "1" * 40, "workspace: adopt managed repository demo", ("log.md",), None
                ),
            )
        ),
        lambda: repo.repo_adopt_payload(
            RepoAdoptResult(
                "nope",
                None,
                None,
                None,
                None,
                None,
                False,
                (),
                (),
                True,
                RepoRefusal("unknown-repository", "d"),
                warnings=("w",),
            )
        ),
    ),
    "repo.repo_add_payload": (
        lambda: repo.repo_add_payload(
            RepoAddResult(
                "demo",
                "https://e/demo.git",
                "main",
                "main",
                A,
                "v1",
                ("repositories/demo.md",),
                False,
                None,
                _COMMIT,
                ("w",),
            )
        ),
        lambda: repo.repo_add_payload(
            RepoAddResult(
                "Bad", "https://e/demo.git", None, None, None, None, (), True, RepoRefusal("invalid-name", "d")
            )
        ),
        lambda: repo.repo_add_payload(
            RepoAddResult(
                "demo",
                "https://e/demo.git",
                "main",
                None,
                A,
                None,
                (),
                False,
                managed=True,
                checkout=".gw/worktrees/demo/main",
            )
        ),
    ),
    "repo.repo_restore_payload": (
        lambda: repo.repo_restore_payload(
            RepoRestoreResult(
                (
                    RestoreOutcome("demo", "cloned", A),
                    RestoreOutcome("x", "refused", None, RepoRefusal("not-found", "d")),
                ),
                False,
            )
        ),
        lambda: repo.repo_restore_payload(RepoRestoreResult((), True, RepoRefusal("git-unavailable", "d"))),
        lambda: repo.repo_restore_payload(
            RepoRestoreResult((RestoreOutcome("demo", "cloned", A, checkout="checkout-created"),), False)
        ),
    ),
    "repo.repo_advance_payload": (
        lambda: repo.repo_advance_payload(
            RepoAdvanceResult(
                "demo",
                "advanced",
                A,
                B,
                "v1-3-gbbbbbbb",
                _RANGE,
                _FLAGGED,
                ("log.md",),
                ("proposals/x.md",),
                ("y.md: k — d",),
                False,
                None,
                _COMMIT,
                (),
            )
        ),
        lambda: repo.repo_advance_payload(
            RepoAdvanceResult(
                "demo", "refused", None, None, None, None, (), (), (), (), True, RepoRefusal("no-pin", "d")
            )
        ),
        lambda: repo.repo_advance_payload(
            RepoAdvanceResult(
                "demo",
                "advanced",
                A,
                B,
                None,
                None,
                (),
                ("code-graph/demo.md",),
                (),
                (),
                False,
                managed=True,
                commits=2,
            )
        ),
    ),
}
