"""Effective repository checkouts used by lint.

General repository resolution anchors declarations at the selected manifest.
A linked workspace worktree can lack an ignored operational checkout for an
in-bundle clone. Lint alone may borrow that checkout from the Git-proven
primary workspace, when both shared declarations agree. Scan, dispatch and
mutation postconditions retain their existing repository resolution.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from code_wiki_okf.config import Config
from okf_io import Rule, RuleContext
from okf_io.validate import Finding
from work_tracker_okf.compose import rule_set
from work_tracker_okf.items import item_index, load_items
from work_tracker_okf.pipeline import PipelineDefinition
from work_tracker_okf.rules import lane_rules

from graph_works_core.workspace.config import declared_checkouts, load_workspace_config
from graph_works_core.workspace.discovery import resolve as resolve_workspace
from graph_works_core.workspace.dispatch_config import load_dispatch_config
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.manifest import workspace_store
from graph_works_core.workspace.provenance import probe_git
from graph_works_core.workspace.repos import ItemRepo, in_bundle_clone, resolve_item_repo

PrimaryProbe = Callable[[WorkspaceLayout], WorkspaceLayout | None]
ROOTED_CODES: frozenset[str] = frozenset({"targets.affects-missing", "plan.action-target-missing"})


@dataclass(frozen=True, slots=True)
class LintRepositories:
    """Repository names and effective roots in declaration order."""

    roots: Mapping[str, Path]

    def __post_init__(self) -> None:
        object.__setattr__(self, "roots", MappingProxyType(dict(self.roots)))

    @property
    def union(self) -> tuple[Path, ...]:
        return tuple(self.roots.values())


def _without(rule: Rule) -> Rule:
    def wrapped(ctx: RuleContext) -> Iterable[Finding]:
        return (finding for finding in rule(ctx) if finding.code not in ROOTED_CODES)

    return wrapped


def _rooted(layout: WorkspaceLayout, repositories: LintRepositories, definition: PipelineDefinition) -> Rule:
    def rule(ctx: RuleContext) -> Iterable[Finding]:
        items = load_items(ctx.bundle)
        by_path = item_index(items)
        groups: dict[tuple[Path, ...], set[str]] = {}
        for item in items:
            if item.archived or (ctx.scope is not None and item.page_path not in ctx.scope):
                continue
            chosen = resolve_item_repo(
                layout,
                item,
                by_path,
                repositories=repositories.roots,
                fallback=lambda: ItemRepo(None, None, "fallback"),
            )
            roots = (chosen.path,) if chosen.path is not None else repositories.union
            groups.setdefault(roots, set()).add(item.page_path)
        for roots, pages in groups.items():
            for inner in lane_rules(repo_roots=roots, vault_root=layout.bundle_dir, definition=definition):
                for finding in inner(ctx):
                    if finding.code in ROOTED_CODES and finding.path in pages:
                        yield finding

    return rule


def work_lane_rules(layout: WorkspaceLayout, config: Config, repositories: LintRepositories) -> tuple[Rule, ...]:
    """Check root-dependent work findings against each item's effective repository."""
    definition = load_dispatch_config(layout).definition
    base = rule_set(
        layout.bundle_dir,
        vault_root=layout.bundle_dir,
        declarations_dir=config.declarations_dir,
        definition=definition,
    )
    return (*map(_without, base), _rooted(layout, repositories, definition))


def _entry(content: Mapping[str, object], name: str) -> Mapping[str, object]:
    repositories = content.get("repositories")
    entry = repositories.get(name) if isinstance(repositories, Mapping) else None
    return dict(entry) if isinstance(entry, Mapping) else {}


def _shared_entry(layout: WorkspaceLayout, name: str) -> Mapping[str, object]:
    return _entry(workspace_store(layout).read_base_explicit(), name)


def _missing(layout: WorkspaceLayout, name: str, path: Path, why: str) -> WorkspaceError:
    return WorkspaceError(
        f"{layout.manifest_path}: repositories.{name} resolves to {path}, "
        f"which does not exist as a directory ({why}); lint cannot check affects paths without it"
    )


def lint_repositories(layout: WorkspaceLayout, *, probe: PrimaryProbe | None = None) -> LintRepositories:
    """Resolve all declared working roots, refusing missing effective roots.

    Only a missing relative checkout of an in-bundle clone can be projected.
    Absolute checkouts and selected-workspace local overrides are authoritative.
    The primary probe runs at most once, only when projection is needed.
    """
    try:
        try:
            layout.manifest_path.stat()
        except FileNotFoundError:
            return LintRepositories({})
        config = load_workspace_config(layout)
    except OSError as exc:
        raise WorkspaceError(f"{layout.manifest_path}: cannot read workspace repository configuration: {exc}") from exc
    checkouts = declared_checkouts(layout) if any(in_bundle_clone(layout, e.path) for e in config.repos) else {}
    resolved: dict[str, Path] = {}
    primary: list[WorkspaceLayout | None] = []
    for entry in config.repos:
        if not in_bundle_clone(layout, entry.path):
            if not entry.path.is_dir():
                raise _missing(layout, entry.name, entry.path, "declared path")
            resolved[entry.name] = entry.path
            continue
        checkout = checkouts.get(entry.name)
        if checkout is None:
            raise WorkspaceError(
                f"{layout.manifest_path}: repositories.{entry.name} is an in-bundle clone and declares no checkout; "
                f"add checkout: (e.g. .gw/worktrees/{entry.name}/<track>)"
            )
        if checkout.is_dir():
            resolved[entry.name] = checkout
            continue
        overlay = _entry(workspace_store(layout).read_overlay_explicit(), entry.name)
        if isinstance(overlay.get("checkout"), str):
            raise _missing(layout, entry.name, checkout, f"set in {layout.local_manifest_path.name}")
        raw_checkout = _shared_entry(layout, entry.name).get("checkout")
        if isinstance(raw_checkout, str) and Path(raw_checkout).expanduser().is_absolute():
            raise _missing(layout, entry.name, checkout, "declared absolute checkout")
        resolved[entry.name] = _projected(layout, entry.name, checkout, probe, primary)
    return LintRepositories(resolved)


def _projected(
    layout: WorkspaceLayout,
    name: str,
    checkout: Path,
    probe: PrimaryProbe | None,
    memo: list[WorkspaceLayout | None],
) -> Path:
    if not memo:
        memo.append((probe if probe is not None else primary_workspace)(layout))
    primary = memo[0]
    if primary is None:
        raise _missing(layout, name, checkout, "declared checkout; this workspace is not a linked worktree")
    if _shared_entry(layout, name) != _shared_entry(primary, name):
        raise WorkspaceError(
            f"{layout.manifest_path}: repositories.{name} differs from the primary workspace "
            f"{primary.manifest_path}; lint will not borrow a checkout for a different declaration"
        )
    projected = declared_checkouts(primary).get(name)
    if projected is None or not projected.is_dir():
        raise _missing(primary, name, projected or checkout, "declared checkout in the primary workspace")
    return projected


def _git_paths(root: Path) -> tuple[Path, Path, Path] | None:
    out = probe_git(root, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-dir", "--git-common-dir")
    if out.returncode != 0:
        if any((parent / ".git").is_file() for parent in (root, *root.parents)):
            raise WorkspaceError(
                f"{root}: a linked Git worktree whose repository cannot be read: {out.stderr.strip() or out.cause}"
            )
        return None
    lines = out.stdout.splitlines()
    if len(lines) != 3 or any(not Path(line).is_absolute() for line in lines):
        raise WorkspaceError(f"{root}: unexpected git rev-parse output {out.stdout!r}")
    top, git_dir, common = (Path(line).resolve() for line in lines)
    if not root.resolve().is_relative_to(top):
        raise WorkspaceError(f"{root}: outside Git toplevel {top}; cannot prove the primary workspace")
    return top, git_dir, common


def _worktrees(root: Path) -> tuple[tuple[Path, bool], ...]:
    out = probe_git(root, "worktree", "list", "--porcelain")
    if out.returncode != 0:
        raise WorkspaceError(f"{root}: git worktree list failed: {out.stderr.strip() or out.cause}")
    records: list[tuple[Path, bool]] = []
    for record in out.stdout.strip("\n").split("\n\n"):
        if not record:
            continue
        lines = record.splitlines()
        raw_path = lines[0].removeprefix("worktree ")
        if (
            not lines[0].startswith("worktree ")
            or not Path(raw_path).is_absolute()
            or any(line.startswith("worktree ") for line in lines[1:])
        ):
            raise WorkspaceError(f"{root}: malformed git worktree list record {record!r}")
        if any(line == "prunable" or line.startswith("prunable ") for line in lines[1:]):
            if not records:
                raise WorkspaceError(f"{root}: the primary worktree is prunable; cannot prove the primary workspace")
            continue
        records.append((Path(raw_path).resolve(), "bare" in lines[1:]))
    return tuple(records)


def primary_workspace(layout: WorkspaceLayout) -> WorkspaceLayout | None:
    """Prove the primary workspace of a registered linked Git worktree.

    Return None for a non-Git workspace or the primary worktree itself. Refuse
    linked worktrees whose primary cannot be proven. Never infer a primary
    from directory spelling, branch names, or the common directory's parent.
    """
    paths = _git_paths(layout.root)
    if paths is None:
        return None
    top, git_dir, common = paths
    if git_dir == common:
        return None
    records = _worktrees(layout.root)
    if not records:
        raise WorkspaceError(f"{layout.root}: git lists no worktrees; cannot prove the primary workspace")
    main, bare = records[0]
    if bare:
        raise WorkspaceError(f"{layout.root}: primary workspace is a bare repository; cannot project checkouts")
    if top not in {path for path, _ in records}:
        raise WorkspaceError(f"{top}: not a registered worktree of {common}; cannot prove the primary workspace")
    main_common = probe_git(main, "rev-parse", "--path-format=absolute", "--git-common-dir")
    common_lines = main_common.stdout.splitlines()
    if (
        main_common.returncode != 0
        or len(common_lines) != 1
        or not Path(common_lines[0]).is_absolute()
        or Path(common_lines[0]).resolve() != common
    ):
        raise WorkspaceError(f"{main}: its git common directory is not {common}; cannot prove the primary workspace")
    primary_root = main / layout.root.resolve().relative_to(top)
    if not (primary_root / "workspace.yaml").is_file():
        raise WorkspaceError(f"{primary_root}: no workspace.yaml in the primary worktree")
    return resolve_workspace(workspace=primary_root, environ={})


__all__ = ["ROOTED_CODES", "LintRepositories", "lint_repositories", "primary_workspace", "work_lane_rules"]
