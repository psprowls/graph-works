"""Workspace init: plan, then apply.

**No `dry_run` flag.** Six shipped writers across two tiers already return a
plan instead — `okf_ext.tables`, `moves`, `sections`, `generators`, and
`work_tracker_okf.filing`, `archive`. This is the seventh and the first at band
3. Not calling `apply_init` *is* the dry run, and a workspace init is exactly
the shape a boolean cannot express: "scaffold and install, but leave the
manifest alone" is a plan you edit, not a flag you pass.

**The acts, in the order `apply_init` performs them:**

1. Directories — `root`, `_config/`, `_cache/`, `okf/`, `worktrees/`.
2. `<root>/.gitignore` — the gitignored members. Self-contained inside the
   workspace; the repo's own root `.gitignore` is never edited.
3. `<root>/workspace.yaml` — written when absent, never overwritten.
4. `<bundle>/_repositories.yaml` — seeded *from* the layout, so the two config
   surfaces cannot disagree at birth.
5. `okf_ext.bundle.plan_scaffold` — `index.md`, `log.md`, `_tags.yaml`, with
   declaration members routed to `config_dir`.
6. Each installer in `INSTALLERS`.

Acts 4 and 5-6 are swapped relative to the design spec's numbering, and the
swap is load-bearing. `code_wiki_okf` ships its own `_repositories.yaml`
template as a `SEED_ONLY` member — created when absent, skipped when present,
**never compared**. Seeding ours first is what makes that skip fire. Writing
ours afterwards would mean overwriting a file that package had just declared
the human's, and its template's `graph_dir` is wrong for this layout anyway.

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

from graph_works_core.workspace.discovery import find_repo_root
from graph_works_core.workspace.errors import InitError
from graph_works_core.workspace.layout import MANIFEST_FILENAME, WorkspaceLayout, layout_for
from graph_works_core.workspace.manifest import defaults, read, render_initial
from graph_works_core.workspace.pipeline import RELAY_TAIL_SEED

GITIGNORE_FILENAME = ".gitignore"
REPOSITORIES_FILENAME = "_repositories.yaml"

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

    The installer previews in `installs` are computed against the filesystem as
    it stands, so on a fresh root `code_wiki_okf`'s preview lists
    `_repositories.yaml` as a write that act 4 will have already satisfied by
    the time its installer runs. That over-reports one file on a first init and
    nothing at all on any later one, which is where idempotence is actually
    read.
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
    """`<root>/.gitignore` — the workspace's own, never the repo's.

    Created with every entry when absent; when present, only the *missing*
    entries are appended. That makes re-entrance a checked fact rather than a
    property of the file happening to already say the right thing.
    """
    entries = layout.gitignore_entries
    if not entries:
        return None
    path = layout.root / GITIGNORE_FILENAME
    body = "".join(f"{entry}\n" for entry in entries)
    if not path.exists():
        return PlannedWrite(label=GITIGNORE_FILENAME, path=path, content=_GITIGNORE_HEADER + body, mode="create")
    present = {line.strip() for line in path.read_text(encoding="utf-8").splitlines()}
    missing = "".join(f"{entry}\n" for entry in entries if entry not in present)
    if not missing:
        return None
    return PlannedWrite(label=GITIGNORE_FILENAME, path=path, content=missing, mode="append")


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
    repositories = layout.bundle_dir / REPOSITORIES_FILENAME
    if not repositories.exists():
        writes.append(
            PlannedWrite(
                label=f"{layout.bundle_dir.name}/{REPOSITORIES_FILENAME}",
                path=repositories,
                content=_repositories_text(layout),
                mode="create",
            )
        )

    return WorkspacePlan(
        layout=layout,
        today=today,
        directories=tuple(directory for directory in layout.directories if not directory.is_dir()),
        writes=tuple(writes),
        scaffold=plan_scaffold(layout.bundle_dir, today=today, declarations_dir=layout.config_dir),
        installs=tuple(
            installer(layout.bundle_dir, today=today, declarations_dir=layout.config_dir, dry_run=True)
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
            installer(plan.layout.bundle_dir, today=plan.today, declarations_dir=plan.layout.config_dir, dry_run=False)
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
