"""`gw work reconcile-context` — the read the `reconciling-spec` skill runs first.

One command, not a sub-app: assembling the context is a single read, and every
mutating call it feeds (`gw work decision add`, `gw work advance`) already
exists. Read-only throughout -- it never edits the spec, answers a decision,
or decides whether reconciliation is complete.

Incomplete evidence is a success. A missing repo, ledger, or usable anchor
degrades to partial groups plus `warnings` and exits `SUCCESS`; only an
unresolved path and a `--repo` that is not a Git repository are failures.
"""

from __future__ import annotations

from pathlib import Path

import typer
from graph_works_core.work.reconcile import run_reconcile_context
from graph_works_core.workspace.errors import WorkspaceError

from graph_works_cli import exit_codes
from graph_works_cli.work_cli import rendering
from graph_works_cli.workspace_resolution import resolve_workspace


def _repo_override(repo: str) -> Path | None:
    """`--repo` as a validated Path, or `None`.

    Core degrades silently when a repo yields no git evidence, which is right
    for a repo it *resolved*; it is wrong for one the caller *named*. An
    explicit path that is not a repository is a caller error and gets its own
    exit code (spec 8).
    """
    if not repo:
        return None
    path = Path(repo)
    if not (path / ".git").exists():
        rendering.fail(f"{path}: not a git repository", code=exit_codes.NOT_IN_GIT_REPO)
    return path


def reconcile_context(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    repo: str = typer.Option("", "--repo", help="Code repository path; overrides the declared resolution."),
    repo_name: str = typer.Option("", "--repo-name", help="Select among several declared repositories."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = typer.Option(False, "--json", help="Emit the context as JSON."),
) -> None:
    """Assemble everything `reconciling-spec` needs for one work item. Read-only."""
    layout = resolve_workspace(workspace)
    override = _repo_override(repo)
    try:
        context = run_reconcile_context(layout, path, repo=override, repo_name=repo_name or None)
    except WorkspaceError as exc:
        rendering.fail(str(exc), code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except ValueError as exc:
        rendering.fail(str(exc), code=exit_codes.AMBIGUOUS, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), cause=exc)

    payload = rendering.reconcile_payload(context)
    if json_output:
        rendering.emit(payload)
        for warning in payload["warnings"]:
            rendering.warn(warning)
    else:
        rendering.render_reconcile(payload)
