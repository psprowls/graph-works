"""Best-effort Orca naming and parent links for worktrees created by plain git.

The local event protocol keeps this vendor adapter independent of the
application that owns placement. Orca discovery and notification failures
are notes: the checkout has already been placed successfully.
"""

from __future__ import annotations

import shlex
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Protocol

from workflow_orca._cli import OrcaCliError, _subprocess_run
from workflow_orca.port import OrcaCliPort, OrcaWorktree


class _PlacementEventLike(Protocol):
    @property
    def repo_root(self) -> Path: ...

    @property
    def worktree(self) -> Path: ...

    @property
    def parent_worktree(self) -> Path: ...

    @property
    def slug(self) -> str: ...

    @property
    def worktrees_dir(self) -> Path: ...


class OrcaPlacementNotifier:
    """Tell Orca about placement without making Orca a placement dependency."""

    def __init__(
        self,
        port: OrcaCliPort | None = None,
        *,
        attempts: int = 5,
        interval: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._port = port if port is not None else OrcaCliPort(run=partial(_subprocess_run, timeout=5))
        self._attempts = attempts
        self._interval = interval
        self._sleep = sleep
        self._checked_repos: set[Path] = set()

    def placed(self, event: _PlacementEventLike) -> str | None:
        """Return Orca's outcome and any configuration note; never undo placement."""
        notes: list[str] = []
        try:
            base_note = self._base_note(event)
            if base_note is not None:
                notes.append(base_note)
                if base_note == "orca: unknown repository":
                    return base_note
            notes.append(self._link(event))
        except Exception as exc:
            # Notification must not fail an already completed placement, even
            # when a vendor response violates the port's expected shape.
            notes.append(f"orca: unavailable ({exc})")
        return "; ".join(notes)

    def _base_note(self, event: _PlacementEventLike) -> str | None:
        if event.repo_root in self._checked_repos:
            return None
        self._checked_repos.add(event.repo_root)
        try:
            base = self._port.repo_show(f"path:{event.repo_root}")
            if base is not None and Path(base).resolve() != event.worktrees_dir.resolve():
                return f"orca: worktreeBasePath {base} differs from layout worktrees_dir {event.worktrees_dir}"
        except OrcaCliError as exc:
            # selector_not_found on repo show identifies the repository itself.
            if exc.code in {"unknown_repository", "repo_not_found", "repository_not_found", "selector_not_found"}:
                return "orca: unknown repository"
        except TimeoutError:
            # Stop notification on a stalled diagnostic read too; placement
            # still succeeds with the unavailable note from placed().
            raise
        except Exception:
            # This optional configuration diagnostic is always best effort.
            pass
        return None

    def _show(self, selector: str) -> OrcaWorktree | None:
        try:
            return self._port.worktree_show(selector)
        except OrcaCliError as exc:
            if exc.code != "selector_not_found":
                raise
        return None

    def _link(self, event: _PlacementEventLike) -> str:
        child = f"path:{event.worktree}"
        parent = f"path:{event.parent_worktree}"
        for _ in range(self._attempts):
            if self._show(child) is not None:
                parent_row = self._show(parent)
                self._port.worktree_set(child, display_name=event.slug, parent=parent if parent_row else None)
                if parent_row is None:
                    return "orca: parent not found"
                return f"orca: linked under {parent_row['display_name'] or event.parent_worktree}"
            self._sleep(self._interval)
        command = shlex.join(
            ["orca", "worktree", "set", "--worktree", child, "--parent-worktree", parent, "--display-name", event.slug]
        )
        return f"orca: not found after {self._attempts * self._interval:g}s; run: {command}"
