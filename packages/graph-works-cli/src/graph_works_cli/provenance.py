"""Provenance guard — warn when `gw`'s routing logic comes from another checkout.

`uv tool install` records an *editable* install, materialized as `.pth` files holding the absolute
path of the checkout it was installed from. Bare `gw` therefore means "the CLI as it exists in that
checkout right now" — normally `main`. Run from a worktree whose branch changed
`work-tracker-okf` / `graph-works-core` routing, the imports all succeed, from the wrong tree, and the
stage routing is computed from stale logic. Not an error: a plausible wrong answer.

Running bare `gw` from a worktree is *intended*, so "different checkout" alone cannot be the trigger;
it would warn on nearly every command and get filtered out as noise. Two conditions are required:

1. The command is routing-sensitive — enforced by the call sites, not here. Only `next`, `advance`,
   and `orchestrate` (both the top-level aliases and the `gw work` forms) call this. Those call sites
   land in C3 — this module ships unwired until then.
2. The invocation checkout's routing sources actually differ from the running ones, by git content
   comparison.

Every failure to resolve either side degrades to silence. A guard that guesses is worse than no
guard.

Kill switch: ``GRAPH_WORKS_PROVENANCE_GUARD=0``. stderr only — `gw` guarantees clean stdout so
`--json | jq` works, the same contract `-v` already honors.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import typer

_KILL_SWITCH = "GRAPH_WORKS_PROVENANCE_GUARD"

# Walk-up marker identifying a workspace checkout root. graph-works-core's own pyproject is used
# because it is the package whose staleness this guard is about.
_MARKER = Path("packages") / "graph-works-core" / "pyproject.toml"

# The only trees whose staleness changes a routing answer. Repo-relative and forward-slashed: these
# are git pathspecs, not filesystem paths.
_ROUTING_PATHS = ("packages/work-tracker-okf/src", "packages/graph-works-core/src")

_GIT_TIMEOUT_SECONDS = 5


def _checkout_root(start: Path) -> Path | None:
    """First ancestor of `start` (inclusive) holding the workspace marker."""
    candidate = start
    while True:
        if (candidate / _MARKER).is_file():
            return candidate
        if candidate == candidate.parent:
            return None
        candidate = candidate.parent


def _source_checkout_root() -> Path | None:
    """Checkout the *running* routing code was loaded from, or None.

    None means `graph_works_core` imports from somewhere with no workspace above it — a genuine
    non-editable wheel install. Nothing to compare, so nothing to say.
    """
    import graph_works_core

    return _checkout_root(Path(graph_works_core.__file__).resolve().parent)


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str] | None:
    """Run git, capturing all output; None on any failure to run it at all."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _stale_source() -> tuple[Path, str] | None:
    """(source_checkout, source_head_sha) when the running code is stale, else None."""
    if os.environ.get(_KILL_SWITCH) == "0":
        return None

    source_root = _source_checkout_root()
    if source_root is None:
        return None

    cwd_root = _checkout_root(Path.cwd().resolve())
    if cwd_root is None or cwd_root == source_root:
        return None

    head = _git(["rev-parse", "HEAD"], cwd=source_root)
    if head is None or head.returncode != 0 or not head.stdout.strip():
        return None
    sha = head.stdout.strip()

    # Compare each working tree to the same source commit, then compare those
    # patches. This includes tracked staged/unstaged edits in the editable
    # source checkout instead of treating source HEAD as the running content.
    # A non-zero result means the base commit did not resolve or git could not
    # produce a trustworthy comparison, so the guard stays fail-quiet.
    diff_args = [
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--binary",
        "--full-index",
        sha,
        "--",
        *_ROUTING_PATHS,
    ]
    source_diff = _git(diff_args, cwd=source_root)
    cwd_diff = _git(diff_args, cwd=cwd_root)
    if source_diff is None or cwd_diff is None or source_diff.returncode != 0 or cwd_diff.returncode != 0:
        return None
    if source_diff.stdout == cwd_diff.stdout:
        return None

    return source_root, sha


def _format_warning(source_root: Path, sha: str) -> str:
    """The warning text — names the fix, which under current policy is merge-and-pull."""
    return (
        f"warning: gw is running routing code from {source_root} (HEAD {sha[:7]})\n"
        "         which differs from this checkout's packages/work-tracker-okf + graph-works-core.\n"
        "         Stage routing may be computed from stale logic.\n"
        "         Fix: merge this branch to main and pull the main checkout\n"
        "              (editable install — no reinstall needed).\n"
        "         Override for one call: uv run --package graph-works-cli gw <args>\n"
        f"         Silence: {_KILL_SWITCH}=0"
    )


def warn_if_stale_routing() -> None:
    """Emit the staleness warning on stderr, or do nothing. Never raises."""
    stale = _stale_source()
    if stale is None:
        return
    typer.echo(_format_warning(*stale), err=True)
