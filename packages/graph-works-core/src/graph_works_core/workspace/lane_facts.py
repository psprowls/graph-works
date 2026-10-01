"""Git observations for repository lane pages, shared by wiki and work readers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from okf_io import Document, load
from repositories_okf.git import Git
from repositories_okf.lane import LANE_DIR, TYPES
from repositories_okf.lifecycle import MANAGED_TYPE, clone_path
from repositories_okf.repository import RepositoryFacts, gather

from graph_works_core.workspace.config import declared_checkouts, load_workspace_config
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.provenance import GitFailure, gate_git
from graph_works_core.workspace.repos import in_bundle_clone


def runner(layout: WorkspaceLayout, environ: Mapping[str, str] | None = None) -> Git | str:
    """Use the workspace's configured git executable, or return its failure detail."""
    env: Mapping[str, str] = os.environ if environ is None else environ
    resolved = gate_git(layout, environ=env)
    if isinstance(resolved, GitFailure):
        return resolved.detail
    return Git(executable=resolved.path, environ=dict(env))


def lane_documents(layout: WorkspaceLayout) -> dict[str, Document]:
    """Direct repository pages, including when a caller's bundle hides this lane."""
    lane = layout.bundle_dir / LANE_DIR
    pages: dict[str, Document] = {}
    for path in sorted(lane.glob("*.md")) if lane.is_dir() else ():
        if path.name == "index.md":
            continue
        document = load(path)
        if document.fm_data(dates="iso").get("type") in TYPES:
            pages[path.stem] = document
    return pages


def has_lane_pages(layout: WorkspaceLayout) -> bool:
    lane = layout.bundle_dir / LANE_DIR
    return lane.is_dir() and any(path.name != "index.md" for path in lane.glob("*.md"))


def _declared(layout: WorkspaceLayout) -> tuple[dict[str, str], dict[str, Path]]:
    """Map declared in-bundle clone paths to names and checkout paths."""
    try:
        repos = load_workspace_config(layout).repos
        checkouts = declared_checkouts(layout)
    except (OSError, WorkspaceError):
        return {}, {}
    return (
        {entry.path.resolve().as_posix(): entry.name for entry in repos if in_bundle_clone(layout, entry.path)},
        dict(checkouts),
    )


def gather_lane_facts(
    layout: WorkspaceLayout, git: Git, pages: Mapping[str, Document] | None = None
) -> tuple[RepositoryFacts, ...]:
    by_clone, checkouts = _declared(layout)
    facts: list[RepositoryFacts] = []
    for name, page in sorted((lane_documents(layout) if pages is None else pages).items()):
        manifest_name = by_clone.get((layout.bundle_dir / clone_path(name)).resolve().as_posix())
        checkout = checkouts.get(manifest_name) if manifest_name is not None else None
        facts.append(
            gather(
                git,
                bundle_dir=layout.bundle_dir,
                name=name,
                page=page,
                declared=manifest_name is not None,
                checkout=checkout,
            )
        )
    return tuple(facts)


def repository_notes(
    layout: WorkspaceLayout, repo_name: str | None, *, environ: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    """Advisory notes for managed repositories ahead of their recorded pin."""
    if repo_name is None or not has_lane_pages(layout):
        return ()
    git = runner(layout, environ)
    if isinstance(git, str):
        return ()
    by_clone, _ = _declared(layout)
    notes: list[str] = []
    for fact in gather_lane_facts(layout, git):
        declared_as = by_clone.get((layout.bundle_dir / clone_path(fact.name)).resolve().as_posix())
        if fact.type == MANAGED_TYPE and declared_as == repo_name and fact.ahead:
            notes.append(
                f"repository {fact.name}: {fact.track} is {fact.ahead} commit(s) ahead of pin {(fact.pin or '')[:7]}; "
                f"run `gw repo advance {fact.name}` to rescan"
            )
    return tuple(notes)


__all__ = ["gather_lane_facts", "has_lane_pages", "lane_documents", "repository_notes", "runner"]
