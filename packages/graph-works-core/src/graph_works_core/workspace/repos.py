"""Which code repository this workspace describes.

One question, two answers: `resolve_repo` for "which one repo" (strict on
ambiguity) and `resolve_repos` for "every declared repo". It sits at layer 0
rather than inside `orchestrate/` because two verticals now ask it —
`orchestrate` for dispatch, `work` for reconciliation evidence — and the
verticals-never-import-each-other contract in the root `pyproject.toml`
forbids the second reaching across for the first's copy.

It is not folded into `discovery.py`, which answers "where is the workspace".
This answers "what code does that workspace catalog": a different question,
with a different failure mode (refusal on ambiguity, not a walk-up).

A third question sits above both: not "which repo(s) does this workspace
catalog" but "which repo does *this item* live in". `resolve_item_repo`
answers it, walking the item's `repo:` chain (names only, resolved via
`declared_repo` in `work_tracker_okf.hierarchy`) before falling back to a
caller-supplied name, a caller fallback, or `resolve_repo`'s own strict rule.

An in-bundle clone (`okf/repositories/<name>/references/git`) answers every
question here with its declared `checkout` rather than its `path` (D-001 /
epic decision 016). Scan and the lane lifecycle want the pinned tree and ask
for it directly (`config.repos`, `declared_clone`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from code_wiki_okf.config import RepoConfig
from repositories_okf.lane import LANE_DIR
from work_tracker_okf.hierarchy import declared_repo
from work_tracker_okf.items import WorkItem

from graph_works_core.workspace.config import declared_checkouts, load_workspace_config
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout


def in_bundle_clone(layout: WorkspaceLayout, path: Path) -> bool:
    """Whether *path* is exactly an in-bundle clone: `<bundle_dir>/repositories/<name>/references/git`."""
    try:
        parts = path.resolve().relative_to(layout.bundle_dir.resolve()).parts
    except ValueError:
        return False
    return len(parts) == 4 and parts[0] == LANE_DIR and parts[2:] == ("references", "git")


def working_checkout(layout: WorkspaceLayout, entry: RepoConfig, checkouts: Mapping[str, Path]) -> Path:
    """Where gw works on *entry* (D-001 / epic decision 016).

    An in-bundle clone is scanned, never worked in: its declared `checkout` is
    the answer, and its absence is a refusal rather than a silent fall-back to
    the pinned tree. Any other entry is its own checkout.
    """
    if not in_bundle_clone(layout, entry.path):
        return entry.path
    checkout = checkouts.get(entry.name)
    if checkout is None:
        raise WorkspaceError(
            f"{layout.manifest_path}: repositories.{entry.name} is an in-bundle clone and declares no checkout; "
            f"add checkout: (e.g. .gw/worktrees/{entry.name}/<track>)"
        )
    return checkout


def _working(layout: WorkspaceLayout, entries: Sequence[RepoConfig]) -> dict[str, Path]:
    checkouts = declared_checkouts(layout) if any(in_bundle_clone(layout, entry.path) for entry in entries) else {}
    return {entry.name: working_checkout(layout, entry, checkouts) for entry in entries}


def declared_clone(layout: WorkspaceLayout, name: str) -> Path | None:
    """The declared `path` for *name*: for an in-bundle repository, the pinned clone.

    Only the lane lifecycle and lint ask for it; scan reads `config.repos` directly.
    """
    try:
        config = load_workspace_config(layout)
    except OSError:
        return None
    return next((entry.path for entry in config.repos if entry.name == name), None)


def resolve_repo(layout: WorkspaceLayout, *, repo_name: str | None = None) -> tuple[Path | None, str | None]:
    """The code repo this workspace describes, and why there is none.

    `workspace.yaml`'s `repositories` block is authoritative **always**, not only when the
    workspace and the code repo are separate repositories. The layout's
    `repo_root` is a `.git` walk-up from the workspace root: in the split
    topology it resolves to the *vault*, and every git call made against it
    then degrades to `None` without a word. The declarations file is the
    surface that actually names the code.

    Six outcomes, closed:

    - Exactly one declared, no *repo_name* -> that repo.
    - *repo_name* names a declared repo -> that repo.
    - *repo_name* names nothing declared -> `WorkspaceError`, naming the set.
    - Several declared, no *repo_name* -> `WorkspaceError`. Ambiguity refuses
      rather than guessing; a wrong repo is worse than a refusal.
    - Zero declared, or the file missing -> `(None, note)`. Not an error: a
      workspace that catalogs no code is a shape.
    - Malformed -> `WorkspaceError`. Config raises and content never does, the
      line `code_wiki_okf.config` draws for itself and `_routing_rules` draws
      for a hand-edited manifest.
    """
    path = layout.manifest_path
    try:
        config = load_workspace_config(layout)
    except OSError:
        return None, f"{path}: absent, so this workspace declares no code repository"

    by_name = _working(layout, config.repos)
    if repo_name is not None:
        match = next((entry for entry in config.repos if entry.name == repo_name), None)
        if match is None:
            raise WorkspaceError(
                f"{path}: repo_name {repo_name!r} names no declared repository; declared: {_declared_listing(by_name)}"
            )
        return by_name[repo_name], None
    if not config.repos:
        return None, f"{path}: declares no repositories, so no code repo was resolved"
    if len(config.repos) > 1:
        raise WorkspaceError(
            f"{path}: {len(config.repos)} repositories declared ({_declared_listing(by_name)}); "
            "pass repo_name= to choose one"
        )
    return by_name[config.repos[0].name], None


def resolve_repos(layout: WorkspaceLayout) -> tuple[Path, ...]:
    """Every code repo this workspace declares, in declaration order.

    The multi-repository sibling of `resolve_repo`, for callers whose question
    is about *any* declared repo rather than about one: postcondition
    validation accepts a path that resolves under any of them, so it needs
    them all and never has to choose. Resolution is `resolve_repo`'s, root for
    root. Zero declared, or the file missing, is `()` -- the same degrade, with
    no note because an empty tuple already says it. Malformed raises
    `WorkspaceError` exactly as `resolve_repo` does.
    """
    try:
        config = load_workspace_config(layout)
    except OSError:
        return ()
    return tuple(_working(layout, config.repos).values())


RepoSource = Literal["frontmatter", "flag", "cwd", "sole", "fallback", "workspace"]


@dataclass(frozen=True, slots=True)
class ItemRepo:
    """One item's code repository, and why it was chosen.

    `source` says which rung answered: the item chain's `repo:`
    (`frontmatter`), the caller's `repo_name` (`flag`), a caller fallback
    (`cwd` for advance, `fallback` for an orchestration descendant inheriting
    its root's), or strict resolution (`sole` -- one declared, or none).
    `workspace` names the workspace's own repository (`_workspace`).
    `name` and `path` are both `None` only when nothing resolved; `note`
    then says why.
    """

    name: str | None
    path: Path | None
    source: RepoSource
    note: str | None = None


RepoFallback = Callable[[], ItemRepo]


def declared_repositories(layout: WorkspaceLayout) -> dict[str, Path]:
    """`name -> path` for every declared repository, in declaration order.

    `{}` when `workspace.yaml` is absent; malformed raises `WorkspaceError`,
    exactly as `resolve_repo` does.
    """
    try:
        config = load_workspace_config(layout)
    except OSError:
        return {}
    return _working(layout, config.repos)


def _join(*notes: str | None) -> str | None:
    kept = [note for note in notes if note]
    return "; ".join(kept) if kept else None


def _declared_listing(repositories: Mapping[str, Path]) -> str:
    """Render declared repositories as name and path pairs, sorted by name."""
    return ", ".join(f"{name} → {repositories[name]}" for name in sorted(repositories)) or "none"


def _malformed_note(item: WorkItem, items: Mapping[str, WorkItem], setter: str | None) -> str | None:
    for path in (item.path, *reversed(item.ancestor_paths)):
        if path == setter:
            return None
        holder = item if path == item.path else items.get(path)
        if holder is not None and "repo" in holder.invalid_optional_fields:
            return f"{path}: malformed repo: ignored (expected a declared repository name)"
    return None


def resolve_item_repo(
    layout: WorkspaceLayout,
    item: WorkItem | None,
    items: Mapping[str, WorkItem],
    *,
    repo_name: str | None = None,
    fallback: RepoFallback | None = None,
) -> ItemRepo:
    """The code repository *item* lives in.

    Precedence, first answer wins:

    1. The nearest `repo:` over *item* and its physical ancestors
       (`declared_repo`). It must name a declared repository; a *repo_name*
       that disagrees refuses, naming both and the item that set `repo:`.
    2. *repo_name* -- `resolve_repo(layout, repo_name=...)`'s rules.
    3. *fallback*, when the caller supplies one (advance's cwd matcher, an
       orchestration descendant inheriting its root's repository).
    4. Strict: one declared -> it; none -> `(None, None)` with a note;
       several -> `WorkspaceError` suggesting `repo:`.

    Every refusal is a `WorkspaceError` naming the item. A malformed `repo:`
    is walked past as absent and reported in `note`.
    """
    label = item.path if item is not None else "<unknown item>"
    declared_name, setter = declared_repo(item, items) if item is not None else (None, None)
    note = _malformed_note(item, items, setter) if item is not None else None
    if declared_name is not None:
        repositories = declared_repositories(layout)
        if declared_name not in repositories:
            raise WorkspaceError(
                f"{label}: repo {declared_name!r} (set by {setter}) names no declared repository "
                f"in {layout.manifest_path}; declared: {_declared_listing(repositories)}"
            )
        if repo_name is not None and repo_name != declared_name:
            raise WorkspaceError(
                f"{label}: --repo-name {repo_name!r} conflicts with repo {declared_name!r} set by {setter}"
            )
        return ItemRepo(declared_name, repositories[declared_name], "frontmatter", note)
    if repo_name is not None:
        try:
            path, _ = resolve_repo(layout, repo_name=repo_name)
        except WorkspaceError as exc:
            raise WorkspaceError(f"{label}: {exc}") from exc
        return ItemRepo(repo_name, path, "flag", note)
    if fallback is not None:
        chosen = fallback()
        return replace(chosen, note=_join(note, chosen.note))
    repositories = declared_repositories(layout)
    if len(repositories) > 1:
        raise WorkspaceError(
            _join(
                f"{label}: {layout.manifest_path} declares {len(repositories)} repositories "
                f"({_declared_listing(repositories)}) and {label} sets no repo:; add `repo: <name>` to it or an "
                "ancestor, or pass repo_name= (--repo-name) to choose one",
                note,
            )
        )
    path, strict_note = resolve_repo(layout)
    return ItemRepo(next(iter(repositories), None), path, "sole", _join(note, strict_note))


__all__ = [
    "ItemRepo",
    "RepoFallback",
    "RepoSource",
    "declared_clone",
    "declared_repositories",
    "in_bundle_clone",
    "resolve_item_repo",
    "resolve_repo",
    "resolve_repos",
    "working_checkout",
]
