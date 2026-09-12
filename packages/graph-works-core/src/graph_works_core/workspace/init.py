"""Workspace init: plan, then apply.

**No `dry_run` flag.** Six shipped writers across two tiers already return a
plan instead — `okf_ext.tables`, `moves`, `sections`, `generators`, and
`work_tracker_okf.filing`, `archive`. This is the seventh and the first at band
3. Not calling `apply_init` *is* the dry run, and a workspace init is exactly
the shape a boolean cannot express: "scaffold and install, but leave the
manifest alone" is a plan you edit, not a flag you pass.

**The acts, in the order `apply_init` performs them:**

1. Directories — `root`, `.gw/` (the config dir), `.gw/cache/`, `okf/`,
   `.gw/worktrees/`.
2. `<config_dir>/.gitignore` — the gitignored members — and `<root>/.gitignore`,
   whose one line is `workspace.local.yaml`, the per-machine overlay that must
   never be committed. Both are self-contained inside the workspace; the repo's
   own root `.gitignore` is never edited.
3. `<root>/workspace.yaml` — written when absent, never overwritten. Carries
   the `repositories:`/`ignore:` blocks alongside the four layout overrides
   and provenance -- the workspace's one configuration surface.
4. `<root>/AGENTS.md` via `render_context_file` over the file's existing
   text -- the gw region above `## Local Conventions` regenerated whole from
   the installed template, the tail beneath it carried verbatim -- and
   `<root>/CLAUDE.md` as the one-line `@AGENTS.md` pointer. Both at
   `layout.root`, never the repo root. Then `<bundle_dir>/AGENTS.md` and
   `<bundle_dir>/CLAUDE.md` are deleted when present (a previewed `- ` act).
   A pair left at a *repo* root by the pre-D-001 placement rule is not
   touched -- gw cannot tell it from adopted prose -- and is a manual cleanup.
5. `okf_ext.bundle.plan_scaffold` — `index.md`, `log.md`, `tags.yaml`, with
   declaration members routed to `config_dir`.
6. Each installer in `INSTALLERS`.
7. `<cache_dir>/config.json` — the manifest catalog's projection, written
   from the manifest `apply_init` just confirmed on disk. Closes the silent
   dormancy a gitignored, absent projection would otherwise leave every
   fail-open hook and routing check in until someone runs `gw` by hand.

Idempotence surfaces as an **empty plan**, the vocabulary four okf-ext
capabilities already use. Refusals stay `okf_ext.bundle.WriteFailure` data
rather than a new exception type. `today` is injected — core never reads the
clock.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, Protocol

import code_wiki_okf.init
import doc_wiki_okf.init
import work_tracker_okf.init
from config_io import PROJECTION_FILENAME, write_projection
from okf_ext.bundle import ApplyResult, ScaffoldPlan, apply, plan_scaffold

from graph_works_core.workspace import anchors
from graph_works_core.workspace.context_seed import CLAUDE_POINTER, render_context_file
from graph_works_core.workspace.discovery import find_repo_root
from graph_works_core.workspace.errors import InitError
from graph_works_core.workspace.layout import (
    LOCAL_MANIFEST_FILENAME,
    MANIFEST_FILENAME,
    WorkspaceLayout,
    layout_for,
)
from graph_works_core.workspace.manifest import defaults, read, render_initial, workspace_store
from graph_works_core.workspace.pipeline import RELAY_TAIL_SEED

GITIGNORE_FILENAME = ".gitignore"
AGENTS_FILENAME = "AGENTS.md"
CLAUDE_FILENAME = "CLAUDE.md"

_GITIGNORE_HEADER = "# Written by graph-works-core at workspace init.\n"


class InstallResult(Protocol):
    """What core reads back from an installer.

    Structural on purpose: `code_wiki_okf.init.BundleInstall` and
    `work_tracker_okf.init.BundleInstall` are two classes with one shape, and
    core has no business preferring either or importing one to annotate the
    other.
    """

    @property
    def ok(self) -> bool: ...

    @property
    def changed(self) -> bool: ...

    def diff(self) -> str: ...


class Installer(Protocol):
    """One tier-3 package's `install_bundle`, as core calls it.

    The first parameter is positional-only and named `bundle_root` here even
    though every shipped `install_bundle` names it `root` — that package's own
    bundle root, not `WorkspaceLayout.root` (the workspace root one level up).
    Every call site below passes `layout.bundle_dir`. Positional-only so the
    name mismatch against the real functions is not a `mypy --strict`
    structural-typing error.
    """

    def __call__(
        self,
        bundle_root: str | Path,
        /,
        *,
        today: date,
        declarations_dir: str | Path | None = None,
        dry_run: bool = True,
    ) -> InstallResult: ...


#: The installers a workspace is born with. A module-level tuple *and* a
#: keyword argument, so a later vertical adds its own package's installer
#: without editing core.
#:
#: `doc_wiki_okf` and `work_tracker_okf` both shipped an asset at
#: `sections/_fragments.yaml` with genuinely different content (`see_also`
#: vs. `plan_table` fragments); `okf_ext.bundle.plan_install` compares owned
#: templates by whole-file bytes, so whichever installer ran second refused
#: the other's file as foreign content. Resolved by renaming each package's
#: fragment file to a unique bundle-relative path
#: (`_fragments.doc_wiki.yaml` / `_fragments.work_tracker.yaml`) — that alone
#: makes `plan_install`'s byte-ownership model and
#: `okf_ext.shape.loader.load_sections`'s key-merge model agree, with no code
#: change to either.
INSTALLERS: tuple[Installer, ...] = (
    code_wiki_okf.init.install_bundle,
    work_tracker_okf.init.install_bundle,
    doc_wiki_okf.init.install_bundle,
)


@dataclass(frozen=True, slots=True)
class PlannedWrite:
    """One file init will write -- or, for `mode="delete"`, remove. `label` is
    workspace-relative posix, for reporting; `path` is where it actually
    lands. A delete carries an empty `content` and is only ever planned for
    a path that exists as a file at planning time."""

    label: str
    path: Path
    content: str
    mode: Literal["create", "append", "delete"]


@dataclass(frozen=True, slots=True)
class WorkspacePlan:
    """A preview of initializing one workspace. Writes nothing.

    No installer plans, seeds, or refuses anything under `repositories:`/
    `ignore:` -- those live in `workspace.yaml`, act 3's own write, and
    nothing in `installs` mentions them.
    """

    layout: WorkspaceLayout
    today: date
    directories: tuple[Path, ...]
    writes: tuple[PlannedWrite, ...]
    scaffold: ScaffoldPlan
    installs: tuple[InstallResult, ...]
    installers: tuple[Installer, ...]

    @property
    def ok(self) -> bool:
        return self.scaffold.ok and all(install.ok for install in self.installs)

    @property
    def is_empty(self) -> bool:
        return not (
            self.directories or self.writes or self.scaffold.writes or any(install.changed for install in self.installs)
        )

    def diff(self) -> str:
        """The plan's acts, in `WorkspaceInit.diff()`'s line vocabulary.

        On the plan rather than in the CLI: ADR-0022 requires a plan to be a
        complete artifact the caller can inspect before any write, and a renderer
        living in one caller is not that.

        Refusals come from `scaffold.refusals` — `failed` is the *applied*
        result's field, and a plan has not applied anything yet.

        `index.md`, `log.md` and `tags.yaml` still over-report: every installer previews
        the bundle scaffold against the filesystem as it stands, so a first init
        lists each of those once per installer — four times each, as of the
        three shipped installers plus act 5's own scaffold plan. This is
        staleness, counted honestly: each of those previews is an act the plan
        really does hold, and a renderer that hid it would disagree with the
        plan it renders.
        """
        lines = [f"+ {path}/" for path in self.directories]
        lines += [f"- {write.label}" if write.mode == "delete" else f"+ {write.label}" for write in self.writes]
        lines += [f"+ {planned.member}" for planned in self.scaffold.writes]
        lines += [f"= {item.path}" for item in self.scaffold.skipped]
        lines += [f"! {refusal.path}: {refusal.error}" for refusal in self.scaffold.refusals]
        lines += [install.diff() for install in self.installs if install.changed]
        return "\n".join(line for line in lines if line)


@dataclass(frozen=True, slots=True)
class WorkspaceInit:
    """What `apply_init` did. Shares the `changed` / `diff()` vocabulary
    `IndexUpdate`, `LogAppend`, `Migration` and both `BundleInstall`s use:
    `diff()` renders on demand and writes nothing. `deleted` is its own
    field, not a flavour of `written`, so the diff can say `- ` for it."""

    layout: WorkspaceLayout
    created: tuple[Path, ...]
    written: tuple[str, ...]
    deleted: tuple[str, ...]
    scaffold: ApplyResult
    installs: tuple[InstallResult, ...]

    @property
    def ok(self) -> bool:
        return self.scaffold.ok and all(install.ok for install in self.installs)

    @property
    def changed(self) -> bool:
        """False on a re-run, which is the point: an idempotent init that
        reports "changed" every time tells a human nothing."""
        return bool(
            self.created
            or self.written
            or self.deleted
            or self.scaffold.written
            or any(install.changed for install in self.installs)
        )

    def diff(self) -> str:
        lines = [f"+ {path}/" for path in self.created]
        lines += [f"+ {label}" for label in self.written]
        lines += [f"- {label}" for label in self.deleted]
        lines += [f"+ {member}" for member in self.scaffold.written]
        lines += [f"= {item.path}" for item in self.scaffold.skipped]
        lines += [f"! {failure.path}: {failure.error}" for failure in self.scaffold.failed]
        lines += [install.diff() for install in self.installs if install.changed]
        return "\n".join(line for line in lines if line)


def _gitignore_write(layout: WorkspaceLayout) -> PlannedWrite | None:
    """`<config_dir>/.gitignore` — the workspace's own, never the repo's.

    Anchored at `layout.config_dir`, wherever it resolves — not a hardcoded
    `GW_DIRNAME`-relative path — so a workspace that overrides `config_dir`
    still gets its `.gitignore` in the right place.

    Created with every entry when absent; when present, only the *missing*
    entries are appended. That makes re-entrance a checked fact rather than a
    property of the file happening to already say the right thing.
    """
    entries = layout.gitignore_entries
    if not entries:
        return None
    path = layout.config_dir / GITIGNORE_FILENAME
    label = f"{layout.config_dir.name}/{GITIGNORE_FILENAME}"
    body = "".join(f"{entry}\n" for entry in entries)
    if not path.exists():
        return PlannedWrite(label=label, path=path, content=_GITIGNORE_HEADER + body, mode="create")
    present = {line.strip() for line in path.read_text(encoding="utf-8").splitlines()}
    missing = "".join(f"{entry}\n" for entry in entries if entry not in present)
    if not missing:
        return None
    return PlannedWrite(label=label, path=path, content=missing, mode="append")


def _root_gitignore_write(layout: WorkspaceLayout) -> PlannedWrite | None:
    """`<root>/.gitignore` — the one line that keeps `workspace.local.yaml`
    out of git.

    A second gitignore, not an entry in the first: the local manifest is a
    sibling of `workspace.yaml` at the root, and `<config_dir>/.gitignore`
    cannot ignore what is not under it. In the in-repo `.works` shape
    `layout.root` is `<repo>/.works`, so this is `<repo>/.works/.gitignore`
    and the rule that the repo's own root `.gitignore` is never edited still
    holds.

    Same idempotence as `_gitignore_write`: created with the header and the
    line when absent, the line appended when the file exists without it,
    nothing written when it is already there.
    """
    path = layout.root / GITIGNORE_FILENAME
    if not path.exists():
        return PlannedWrite(
            label=GITIGNORE_FILENAME,
            path=path,
            content=f"{_GITIGNORE_HEADER}{LOCAL_MANIFEST_FILENAME}\n",
            mode="create",
        )
    present = {line.strip() for line in path.read_text(encoding="utf-8").splitlines()}
    if LOCAL_MANIFEST_FILENAME in present:
        return None
    return PlannedWrite(
        label=GITIGNORE_FILENAME,
        path=path,
        content=f"{LOCAL_MANIFEST_FILENAME}\n",
        mode="append",
    )


def _workspace_relative(target: Path, root: Path) -> str:
    """*target* as `workspace.yaml`'s `repositories.<name>.path` should carry it.

    Relative to *root* -- the directory `workspace.yaml` itself lives in
    (`layout.manifest_path == layout.root / "workspace.yaml"`), matching
    every reader (`workspace.config.load_workspace_config` resolves a
    declared repo's relative `path`, via `config_from_mapping`, against the
    directory the manifest was read from). Relative rather than absolute
    because the file is committed, so an absolute path would pin the
    workspace to one machine.
    """
    return Path(os.path.relpath(target, start=root)).as_posix()


def _context_writes(layout: WorkspaceLayout, *, topic: str | None, initialized_at: str) -> list[PlannedWrite]:
    """`<root>/AGENTS.md` and `<root>/CLAUDE.md` -- at `layout.root`, never
    the repo root: gw regenerates the whole gw region of `AGENTS.md`, and that
    is only safe on a file gw owns (D-001 of the unified-context-files item).

    `AGENTS.md` is one `render_context_file` call over its own existing text
    (`None` when absent): the gw region above `## Local Conventions` is
    regenerated, the tail beneath it carried verbatim. `CLAUDE.md` is the
    literal `CLAUDE_POINTER`. Both are `mode="create"` with the full text and
    both are skipped when the text on disk already matches -- the same
    idempotence the gitignore write uses, which is what makes a second plan
    empty.
    """
    writes: list[PlannedWrite] = []
    agents_path = layout.root / AGENTS_FILENAME
    # `read_bytes().decode(...)`, not `read_text(...)`: `Path.read_text` has no
    # `newline=` parameter, and its default universal-newline translation would
    # silently turn a CRLF-authored `## Local Conventions` tail into LF the
    # moment the gw body above it actually changes and this write executes.
    existing = agents_path.read_bytes().decode("utf-8") if agents_path.is_file() else None
    content = render_context_file(
        existing,
        topic=topic,
        initialized_at=initialized_at,
        bundle_dir=layout.bundle_dir.name,
        config_dir=layout.config_dir.name,
    )
    if content != existing:
        writes.append(PlannedWrite(label=AGENTS_FILENAME, path=agents_path, content=content, mode="create"))
    claude_path = layout.root / CLAUDE_FILENAME
    existing_claude = claude_path.read_bytes().decode("utf-8") if claude_path.is_file() else None
    if existing_claude != CLAUDE_POINTER:
        writes.append(PlannedWrite(label=CLAUDE_FILENAME, path=claude_path, content=CLAUDE_POINTER, mode="create"))
    return writes


def _stale_bundle_context_deletes(layout: WorkspaceLayout) -> list[PlannedWrite]:
    """`<bundle_dir>/AGENTS.md` and `<bundle_dir>/CLAUDE.md` -- the hand-carried
    bundle-level pair the merged root `AGENTS.md` absorbed. Planned as
    `mode="delete"` only for a path that is a file right now: a directory is
    left alone, and an absent file plans nothing, so a second plan is empty.
    Unconditional on content (D-003): nothing else writes these files, and
    the plan is the preview.

    A pathological manifest can configure `bundle_dir` to resolve to
    `layout.root` itself (e.g. `layout.bundle_dir: .`). In that shape this
    function's target and act 4's `_context_writes` target are the same
    path, and planning a delete here would have `apply_init` write and then
    immediately unlink the file it just wrote, every run. Skip a path that
    coincides with `layout.root / name` -- that file is act 4's, not a stale
    bundle-level pair."""
    deletes: list[PlannedWrite] = []
    for name in (AGENTS_FILENAME, CLAUDE_FILENAME):
        path = layout.bundle_dir / name
        if path == layout.root / name:
            continue
        if path.is_file():
            label = f"{layout.bundle_dir.name}/{name}"
            deletes.append(PlannedWrite(label=label, path=path, content="", mode="delete"))
    return deletes


def plan_init(
    root: str | Path,
    *,
    today: date,
    topic: str | None = None,
    repo_root: str | Path | None = None,
    installers: Sequence[Installer] = INSTALLERS,
) -> WorkspacePlan:
    """Preview an init over *root*, writing nothing.

    An existing `workspace.yaml`'s layout overrides are honored, so a re-plan
    over a customized workspace previews *that* workspace rather than the
    default one. *repo_root* defaults to a `.git` walk-up from *root*; pass it
    explicitly to pin a repo the walk-up would not find.

    Raises `InitError` for a *root* that exists and is not a directory, for a
    system that does not have Windows long-path support enabled, or for a root
    whose filesystem does not support hard links -- the same two requirements
    `_WindowsAnchor` enforces at mutation time, checked here (in the same
    order `_WindowsAnchor.__init__` checks them) so a bootstrapped-but-unusable
    workspace is never created in the first place.
    """
    root = Path(root).expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise InitError(f"{root}: exists and is not a directory")
    if not anchors.long_paths_enabled():
        raise InitError(anchors._long_path_refusal_message())
    if not anchors.hard_links_supported(root):
        raise InitError(anchors._hard_link_refusal_message(root))

    manifest_path = root / MANIFEST_FILENAME
    manifest = read(manifest_path) if manifest_path.exists() else defaults()
    layout = layout_for(
        root,
        bundle_dir=manifest.bundle_dir,
        config_dir=manifest.config_dir,
        cache_dir=manifest.cache_dir,
        worktrees_dir=manifest.worktrees_dir,
        repo_root=repo_root if repo_root is not None else find_repo_root(root),
    )

    writes: list[PlannedWrite] = []
    gitignore = _gitignore_write(layout)
    if gitignore is not None:
        writes.append(gitignore)
    root_gitignore = _root_gitignore_write(layout)
    if root_gitignore is not None:
        writes.append(root_gitignore)
    if not layout.manifest_path.exists():
        repositories: dict[str, str] = {}
        if layout.repo_root is not None:
            repositories[layout.repo_root.name] = _workspace_relative(layout.repo_root, layout.root)
        writes.append(
            PlannedWrite(
                label=MANIFEST_FILENAME,
                path=layout.manifest_path,
                content=render_initial(
                    today=today,
                    topic=topic,
                    relay_tail=RELAY_TAIL_SEED,
                    repositories=repositories,
                    ignore=layout.scanner_excludes,
                ),
                mode="create",
            )
        )
    writes.extend(
        _context_writes(
            layout,
            topic=manifest.topic if layout.manifest_path.exists() else topic,
            initialized_at=manifest.initialized_at if layout.manifest_path.exists() else today.isoformat(),
        )
    )
    writes.extend(_stale_bundle_context_deletes(layout))

    return WorkspacePlan(
        layout=layout,
        today=today,
        directories=tuple(directory for directory in layout.directories if not directory.is_dir()),
        writes=tuple(writes),
        scaffold=plan_scaffold(layout.bundle_dir, today=today, declarations_dir=layout.config_dir),
        installs=tuple(
            installer(
                layout.bundle_dir,
                today=today,
                declarations_dir=layout.config_dir,
                dry_run=True,
            )
            for installer in installers
        ),
        installers=tuple(installers),
    )


def apply_init(plan: WorkspacePlan) -> WorkspaceInit:
    """Perform *plan*, in the order the acts have to happen in."""
    for directory in plan.directories:
        directory.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    deleted: list[str] = []
    for write in plan.writes:
        if write.mode == "delete":
            write.path.unlink(missing_ok=True)
            deleted.append(write.label)
            continue
        write.path.parent.mkdir(parents=True, exist_ok=True)
        if write.mode == "create":
            write.path.write_text(write.content, encoding="utf-8", newline="")
        else:
            existing = write.path.read_text(encoding="utf-8")
            separator = "" if existing.endswith("\n") or not existing else "\n"
            write.path.write_text(existing + separator + write.content, encoding="utf-8", newline="")
        written.append(write.label)

    projection_path = plan.layout.cache_dir / PROJECTION_FILENAME
    before = projection_path.read_bytes() if projection_path.exists() else None
    write_projection(workspace_store(plan.layout), projection_path)
    if projection_path.read_bytes() != before:
        written.append(f"{plan.layout.cache_dir.name}/{PROJECTION_FILENAME}")

    return WorkspaceInit(
        layout=plan.layout,
        created=plan.directories,
        written=tuple(written),
        deleted=tuple(deleted),
        scaffold=apply(plan.scaffold),
        installs=tuple(
            installer(
                plan.layout.bundle_dir,
                today=plan.today,
                declarations_dir=plan.layout.config_dir,
                dry_run=False,
            )
            for installer in plan.installers
        ),
    )


__all__ = [
    "GITIGNORE_FILENAME",
    "INSTALLERS",
    "InstallResult",
    "Installer",
    "PlannedWrite",
    "WorkspaceInit",
    "WorkspacePlan",
    "apply_init",
    "plan_init",
]
