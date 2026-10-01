"""A workspace declaring one sibling checkout, `demo`, on `main` with linked worktrees: the shape of ../gw today."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gitrepo import Upstream, git, make_upstream
from graph_works_core.workspace.layout import WorkspaceLayout
from workspace_fixture import make_workspace

MANIFEST_TAIL = (
    "# code we build\nrepositories:\n  {name}:\n    path: {path}  # sibling clone\n"
    "    ignore:\n    - '**/fixtures/**'\n"
)


@dataclass(frozen=True)
class Adoptable:
    layout: WorkspaceLayout
    upstream: Upstream
    source: Path
    head: str
    linked: tuple[Path, ...]
    manifest_before: str


def adoptable(tmp_path: Path, *, name: str = "demo", linked: int = 2) -> Adoptable:
    layout = make_workspace(tmp_path)
    (tmp_path / "up").mkdir()
    upstream = make_upstream(tmp_path / "up")
    upstream.commit({"README.md": "# demo\n", "src/a.py": "a = 1\n"}, "c1")
    source = layout.root.parent / name
    git(tmp_path, "clone", "-q", upstream.url, str(source))
    worktrees = []
    for index in range(linked):
        target = layout.worktrees_dir / "legacy" / f"wt{index}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if index == 0:
            git(source, "worktree", "add", "-q", "-b", "feature-a", str(target))
        else:
            git(source, "worktree", "add", "-q", "--detach", str(target))
        worktrees.append(target)
    manifest = layout.manifest_path
    text = manifest.read_bytes().decode("utf-8")
    assert 'repositories:\n  "ws":\n    path: "."\n' in text
    text = text.replace('repositories:\n  "ws":\n    path: "."\n', "")
    relative = Path("..") / name  # the manifest sits in ws/, the checkout beside it
    manifest.write_text(text + MANIFEST_TAIL.format(name=name, path=relative.as_posix()), encoding="utf-8", newline="")
    git(layout.root, "add", "workspace.yaml")
    git(layout.root, "commit", "-qm", f"declare {name}")
    return Adoptable(
        layout=layout,
        upstream=upstream,
        source=source,
        head=git(source, "rev-parse", "HEAD"),
        linked=tuple(worktrees),
        manifest_before=manifest.read_bytes().decode("utf-8"),
    )


def snapshot(fixture: Adoptable) -> tuple[object, ...]:
    """Everything a refusal must leave unchanged."""
    return (
        fixture.layout.manifest_path.read_bytes(),
        git(fixture.layout.root, "rev-parse", "HEAD"),
        git(fixture.layout.root, "status", "--porcelain"),
        fixture.source.is_dir() and git(fixture.source, "rev-parse", "--abbrev-ref", "HEAD"),
        fixture.source.is_dir() and git(fixture.source, "worktree", "list", "--porcelain"),
        sorted(str(p.relative_to(fixture.layout.root)) for p in fixture.layout.bundle_dir.rglob("repositories/**/*")),
    )
