"""Stable exit-code contract (ADR-0013 rule 1) — re-exported from `code_graph_io.exit_codes`.

Every sub-app maps its own typed result onto the closest-fitting code here rather than inventing a
second parallel numbering: missing workspace -> `NOT_INITIALIZED`; unresolved slug/entity match ->
`AMBIGUOUS`; stale routing checkout -> `STALE`; uncaught error -> `GENERIC`. Re-exported by explicit
name (not `from x import *`) so every sub-app imports from inside this package, keeping the
dependency direction visible — `test_exit_codes.py` pins the identity so the two cannot drift apart.
"""

from __future__ import annotations

from code_graph_io.exit_codes import (
    AMBIGUOUS,
    GENERIC,
    NOT_IN_GIT_REPO,
    NOT_INITIALIZED,
    SCHEMA_MISMATCH,
    STALE,
    SUCCESS,
    UPDATE_IN_PROGRESS,
)

__all__ = [
    "AMBIGUOUS",
    "GENERIC",
    "NOT_INITIALIZED",
    "NOT_IN_GIT_REPO",
    "SCHEMA_MISMATCH",
    "STALE",
    "SUCCESS",
    "UPDATE_IN_PROGRESS",
]
