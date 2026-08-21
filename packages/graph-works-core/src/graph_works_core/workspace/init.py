"""Workspace init: plan, then apply.

**No `dry_run` flag.** Six shipped writers across two tiers already return a
plan instead — `okf_ext.tables`, `moves`, `sections`, `generators`, and
`work_tracker_okf.filing`, `archive`. This is the seventh and the first at band
3. Not calling `apply_init` *is* the dry run, and a workspace init is exactly
the shape a boolean cannot express: "scaffold and install, but leave the
manifest alone" is a plan you edit, not a flag you pass.

**The acts, in the order `apply_init` performs them:**

1. Directories — `root`, `_gw/_config/`, `_gw/_cache/`, `okf/`, `_gw/worktrees/`.
2. `<root>/_gw/.gitignore` — the gitignored members. Self-contained inside the
   workspace; the repo's own root `.gitignore` is never edited.
3. `<root>/workspace.yaml` — written when absent, never overwritten.
4. `<root>/_gw/_repositories.yaml` — seeded *from* the layout, so the two
   config surfaces cannot disagree at birth.
5. `<layout.repo_root or layout.root>/{CLAUDE.md,AGENTS.md}` — identical
   bodies, seeding the project context `graph_works_core.prompts
   .project_context` reads. First render fills the template; a re-render
   splices a fresh auto block into the installer-roster region and leaves
   hand-edited prose alone.
6. `okf_ext.bundle.plan_scaffold` — `index.md`, `log.md`, `_tags.yaml`, with
   declaration members routed to `config_dir`.
7. Each installer in `INSTALLERS`, called with `seed_repositories=False` --
   `_repositories.yaml` now lives outside every installer's own bundle-root
   write surface, so none of them plans, seeds, or refuses it. `code_wiki_okf`
   used to ship its own `_repositories.yaml` template as a `SEED_ONLY` member
   there (created when absent, skipped when present, never compared), and act
   4 ran first specifically so that skip would fire before act 7 could collide
   with it. `seed_repositories=False` removes the collision itself, so acts 4
   and 6-7 no longer need to be ordered around it -- they are left in this
   order because nothing requires reverting it, not because it is load-bearing
   anymore.

Idempotence surfaces as an **empty plan**, the vocabulary four okf-ext
capabilities already use. Refusals stay `okf_ext.bundle.WriteFailure` data
rather than a new exception type. `today` is injected — core never reads the
clock.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, Protocol

import code_wiki_okf.init
import doc_wiki_okf.init
import work_tracker_okf.init
from okf_ext.bundle import ApplyResult, ScaffoldPlan, apply, plan_scaffold

from graph_works_core.workspace.context_seed import render_context_file
from graph_works_core.workspace.discovery import find_repo_root
from graph_works_core.workspace.errors import InitError
from graph_works_core.workspace.layout import GW_DIRNAME, MANIFEST_FILENAME, WorkspaceLayout, layout_for
from graph_works_core.workspace.manifest import defaults, read, render_initial
from graph_works_core.workspace.pipeline import RELAY_TAIL_SEED

GITIGNORE_FILENAME = ".gitignore"
REPOSITORIES_FILENAME = "_repositories.yaml"
CONTEXT_FILENAMES = ("CLAUDE.md", "AGENTS.md")

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
        seed_repositories: bool = True,
        dry_run: bool = True,
    ) -> InstallResult: ...


#: The installers a workspace is born with. A module-level tuple *and* a
#: keyword argument, so a later vertical adds its own package's installer
#: without editing core.
#:
#: `doc_wiki_okf` and `work_tracker_okf` both shipped an asset at
#: `_sections/_fragments.yaml` with genuinely different content (`see_also`
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
    """One file init will write, and how. `label` is workspace-relative posix,
    for reporting; `path` is where it actually lands."""

    label: str
    path: Path
    content: str
    mode: Literal["create", "append"]


@dataclass(frozen=True, slots=True)
class WorkspacePlan:
    """A preview of initializing one workspace. Writes nothing.

    Every installer in `installs` is called with `seed_repositories=False`, so
    none of their previews mention `_repositories.yaml` -- act 4's own write is
    the only place it appears. Before that keyword existed, a fresh root's plan
    double-reported the file (once from act 4, once from `code_wiki_okf`'s own
    preview of a file it would find already present); that asymmetry is gone.
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

        `_repositories.yaml` itself no longer over-reports (see the class
        docstring) since every installer runs with `seed_repositories=False`.
        `index.md`, `log.md` and `_tags.yaml` still do: every installer previews
        the bundle scaffold against the filesystem as it stands, so a first init
        lists each of those once per installer — four times each, as of the
        three shipped installers plus act 5's own scaffold plan. This is
        staleness, counted honestly: each of those previews is an act the plan
        really does hold, and a renderer that hid it would disagree with the
        plan it renders.
        """
        lines = [f"+ {path}/" for path in self.directories]
        lines += [f"+ {write.label}" for write in self.writes]
        lines += [f"+ {planned.member}" for planned in self.scaffold.writes]
        lines += [f"= {item.path}" for item in self.scaffold.skipped]
        lines += [f"! {refusal.path}: {refusal.error}" for refusal in self.scaffold.refusals]
        lines += [install.diff() for install in self.installs if install.changed]
        return "\n".join(line for line in lines if line)


@dataclass(frozen=True, slots=True)
class WorkspaceInit:
    """What `apply_init` did. Shares the `changed` / `diff()` vocabulary
    `IndexUpdate`, `LogAppend`, `Migration` and both `BundleInstall`s use:
    `diff()` renders on demand and writes nothing."""

    layout: WorkspaceLayout
    created: tuple[Path, ...]
    written: tuple[str, ...]
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
            self.created or self.written or self.scaffold.written or any(install.changed for install in self.installs)
        )

    def diff(self) -> str:
        lines = [f"+ {path}/" for path in self.created]
        lines += [f"+ {label}" for label in self.written]
        lines += [f"+ {member}" for member in self.scaffold.written]
        lines += [f"= {item.path}" for item in self.scaffold.skipped]
        lines += [f"! {failure.path}: {failure.error}" for failure in self.scaffold.failed]
        lines += [install.diff() for install in self.installs if install.changed]
        return "\n".join(line for line in lines if line)


def _gitignore_write(layout: WorkspaceLayout) -> PlannedWrite | None:
    """`<root>/_gw/.gitignore` — the workspace's own, never the repo's.

    Created with every entry when absent; when present, only the *missing*
    entries are appended. That makes re-entrance a checked fact rather than a
    property of the file happening to already say the right thing.
    """
    entries = layout.gitignore_entries
    if not entries:
        return None
    path = layout.root / GW_DIRNAME / GITIGNORE_FILENAME
    label = f"{GW_DIRNAME}/{GITIGNORE_FILENAME}"
    body = "".join(f"{entry}\n" for entry in entries)
    if not path.exists():
        return PlannedWrite(label=label, path=path, content=_GITIGNORE_HEADER + body, mode="create")
    present = {line.strip() for line in path.read_text(encoding="utf-8").splitlines()}
    missing = "".join(f"{entry}\n" for entry in entries if entry not in present)
    if not missing:
        return None
    return PlannedWrite(label=label, path=path, content=missing, mode="append")


def _bundle_relative(target: Path, bundle_dir: Path) -> str:
    """*target* as `_repositories.yaml` should carry it.

    Relative to the bundle root, which is what that file resolves relative
    paths against — and relative rather than absolute because the file is
    committed, so an absolute path would pin the workspace to one machine.
    """
    return Path(os.path.relpath(target, start=bundle_dir)).as_posix()


def _repositories_text(layout: WorkspaceLayout) -> str:
    """`_repositories.yaml`, written *from* the layout, so the two config
    surfaces cannot disagree at birth.

    They can still drift afterwards: a human editing `graph_dir` here moves the
    cache out from under the layout, and nothing catches it. That check belongs
    to the lint work item, not to this writer.
    """
    lines = [
        "# Written by graph-works-core at workspace init, from the resolved",
        "# workspace layout. `graph_dir` and `declarations_dir` mirror the",
        "# layout's cache and config directories; editing them here moves",
        "# those directories out from under the workspace.",
        f"graph_dir: {json.dumps(_bundle_relative(layout.cache_dir, layout.bundle_dir))}",
        f"declarations_dir: {json.dumps(_bundle_relative(layout.config_dir, layout.bundle_dir))}",
        "",
    ]
    if layout.repo_root is None:
        lines.append("repositories: {}")
    else:
        lines.append("repositories:")
        lines.append(f"  {layout.repo_root.name}:")
        lines.append(f"    path: {json.dumps(_bundle_relative(layout.repo_root, layout.bundle_dir))}")
    lines.append("")
    excludes = layout.scanner_excludes
    if excludes:
        lines.append("ignore:")
        lines.extend(f"  - {json.dumps(pattern)}" for pattern in excludes)
    else:
        lines.append("ignore: []")
    return "\n".join(lines) + "\n"


def _context_writes(layout: WorkspaceLayout, installers: Sequence[Installer], today: date) -> list[PlannedWrite]:
    """`CLAUDE.md` and `AGENTS.md`, identical bodies -- read by
    `graph_works_core.prompts.project_context` from `layout.repo_root or
    layout.root`, so that is where they are written.

    Each is a `render_context_file` call over that file's own existing text
    (`None` when absent), so a hand-edited `CLAUDE.md` and an untouched
    `AGENTS.md` refresh independently. `mode="create"` always carries the
    fully computed text -- first-render, block-refreshed, or block-appended --
    so nothing here needs `PlannedWrite` to grow a third mode. Skipped
    entirely when the computed text does not change, the same idempotence the
    gitignore write uses.
    """
    context_dir = layout.repo_root or layout.root
    writes: list[PlannedWrite] = []
    for filename in CONTEXT_FILENAMES:
        path = context_dir / filename
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        content = render_context_file(existing, workspace=layout.root, installers=installers, today=today)
        if content != existing:
            writes.append(PlannedWrite(label=filename, path=path, content=content, mode="create"))
    return writes


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

    Raises `InitError` only for a *root* that exists and is not a directory —
    the same line both shipped `install_bundle`s draw.
    """
    root = Path(root).expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise InitError(f"{root}: exists and is not a directory")

    manifest_path = root / MANIFEST_FILENAME
    manifest = read(manifest_path) if manifest_path.exists() else defaults()
    layout = layout_for(
        root,
        bundle_dir=manifest.bundle_dir,
        config_dir=manifest.config_dir,
        cache_dir=manifest.cache_dir,
        worktrees_dir=manifest.worktrees_dir,
        repositories_path=manifest.repositories_path,
        repo_root=repo_root if repo_root is not None else find_repo_root(root),
    )

    writes: list[PlannedWrite] = []
    gitignore = _gitignore_write(layout)
    if gitignore is not None:
        writes.append(gitignore)
    if not layout.manifest_path.exists():
        writes.append(
            PlannedWrite(
                label=MANIFEST_FILENAME,
                path=layout.manifest_path,
                content=render_initial(today=today, topic=topic, relay_tail=RELAY_TAIL_SEED),
                mode="create",
            )
        )
    repositories = layout.repositories_path
    if not repositories.exists():
        writes.append(
            PlannedWrite(
                label=_bundle_relative(repositories, layout.root),
                path=repositories,
                content=_repositories_text(layout),
                mode="create",
            )
        )
    writes.extend(_context_writes(layout, installers, today))

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
                seed_repositories=False,
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
    for write in plan.writes:
        write.path.parent.mkdir(parents=True, exist_ok=True)
        if write.mode == "create":
            write.path.write_text(write.content, encoding="utf-8")
        else:
            existing = write.path.read_text(encoding="utf-8")
            separator = "" if existing.endswith("\n") or not existing else "\n"
            write.path.write_text(existing + separator + write.content, encoding="utf-8")
        written.append(write.label)

    return WorkspaceInit(
        layout=plan.layout,
        created=plan.directories,
        written=tuple(written),
        scaffold=apply(plan.scaffold),
        installs=tuple(
            installer(
                plan.layout.bundle_dir,
                today=plan.today,
                declarations_dir=plan.layout.config_dir,
                seed_repositories=False,
                dry_run=False,
            )
            for installer in plan.installers
        ),
    )


__all__ = [
    "GITIGNORE_FILENAME",
    "INSTALLERS",
    "REPOSITORIES_FILENAME",
    "InstallResult",
    "Installer",
    "PlannedWrite",
    "WorkspaceInit",
    "WorkspacePlan",
    "apply_init",
    "plan_init",
]
