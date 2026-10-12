"""Repository-lane observations belong to kernel, independent of wiki commands."""

from __future__ import annotations

import os
from pathlib import Path

from gw_kernel.testing.transactions import git, init_git, workspace
from gw_kernel.workspace import lane_facts
from gw_kernel.workspace.layout import layout_for
from repositories_okf.git import Git


def _page(layout, name: str, kind: str, pin: str | None = None) -> Path:
    page = layout.bundle_dir / "repositories" / f"{name}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    pin_text = "" if pin is None else f"pin: {{commit: {pin}, fetched_at: '2026-10-11T00:00:00Z'}}\n"
    page.write_text(f"---\ntype: {kind}\ntrack: main\n{pin_text}---\n", encoding="utf-8", newline="")
    return page


def test_lane_pages_are_shallow_and_only_repository_types_are_gathered(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    assert lane_facts.lane_documents(layout) == {}
    assert not lane_facts.has_lane_pages(layout)
    index = _page(layout, "index", "ManagedRepository")
    assert not lane_facts.has_lane_pages(layout)
    _page(layout, "other", "Concept")
    _page(layout, "reference", "ReferenceRepository")
    _page(layout, "managed", "ManagedRepository")
    nested = index.parent / "nested/ignored.md"
    nested.parent.mkdir()
    nested.write_bytes(b"---\ntype: ManagedRepository\n---\n")
    assert set(lane_facts.lane_documents(layout)) == {"managed", "reference"}
    assert lane_facts.has_lane_pages(layout)
    runner = Git("git", dict(os.environ))
    facts = lane_facts.gather_lane_facts(layout, runner)
    assert [(fact.name, fact.declared, fact.clone_present, fact.checkout) for fact in facts] == [
        ("managed", False, False, None),
        ("reference", False, False, None),
    ]
    supplied = {"reference": lane_facts.lane_documents(layout)["reference"]}
    assert [fact.name for fact in lane_facts.gather_lane_facts(layout, runner, supplied)] == ["reference"]


def test_managed_lane_notes_use_declared_alias_checkout_and_real_pin_lag(tmp_path: Path) -> None:
    layout = workspace(tmp_path)
    clone = layout.bundle_dir / "repositories/demo/references/git"
    init_git(clone)
    pin = git(clone, "rev-parse", "HEAD").strip()
    git(clone, "checkout", "--detach", "-q")
    checkout = layout.root / "checkout"
    git(clone, "worktree", "add", "-q", str(checkout), "main")
    layout.manifest_path.write_text(
        "version: 1\nrepositories:\n  engine:\n"
        "    path: okf/repositories/demo/references/git\n    checkout: checkout\n",
        encoding="utf-8",
        newline="",
    )
    _page(layout, "demo", "ManagedRepository", pin)
    runner = lane_facts.runner(layout)
    assert isinstance(runner, Git)
    facts = lane_facts.gather_lane_facts(layout, runner)
    assert len(facts) == 1
    assert facts[0].declared and facts[0].checkout_linked
    assert facts[0].checkout == checkout.as_posix()
    assert facts[0].ahead == 0
    assert lane_facts.repository_notes(layout, "engine") == ()
    git(checkout, "commit", "-q", "--allow-empty", "-m", "after pin")
    assert lane_facts.repository_notes(layout, "engine") == (
        f"repository demo: main is 1 commit(s) ahead of pin {pin[:7]}; run `gw repo advance demo` to rescan",
    )
    assert lane_facts.repository_notes(layout, "other") == ()
    assert lane_facts.repository_notes(layout, None) == ()


def test_missing_git_and_invalid_manifest_degrade_to_no_notes(tmp_path: Path) -> None:
    layout = workspace(tmp_path)
    assert lane_facts.repository_notes(layout, "demo") == ()
    _page(layout, "demo", "ManagedRepository")
    env = {"GW_GIT": str(tmp_path / "missing-git"), "PATH": ""}
    assert isinstance(lane_facts.runner(layout, env), str)
    assert lane_facts.repository_notes(layout, "demo", environ=env) == ()
    layout.manifest_path.write_text("version: [\n", encoding="utf-8", newline="")
    facts = lane_facts.gather_lane_facts(layout, Git("git", dict(os.environ)))
    assert len(facts) == 1
    assert not facts[0].declared and facts[0].checkout is None
