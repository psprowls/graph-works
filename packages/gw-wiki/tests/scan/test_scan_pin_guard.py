"""Scan refuses a lane clone whose HEAD disagrees with its pin (D-205); only the advance rescan bypasses it (D-206)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from code_wiki_okf.config import Config
from gw_kernel import apply_init, plan_init
from gw_kernel.graph import commands as graph
from gw_kernel.workspace.config import load_workspace_config
from gw_kernel.workspace.errors import ScanError
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_wiki.scan import run_repo_scan
from gw_wiki.scan.commands import build_scan_worklist, run_scan
from gw_wiki.scan.pin_guard import pin_drift
from ruamel.yaml import YAML
from scan_helpers import TODAY, commit_change, copy_repo, git

AT = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Drifted:
    layout: WorkspaceLayout
    config: Config
    old: str
    new: str


def _page(layout: WorkspaceLayout, name: str, pin: str | None) -> None:
    page = layout.bundle_dir / "repositories" / f"{name}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    pin_block = f"pin:\n  commit: {pin}\n" if pin else ""
    page.write_text(f"---\ntype: ManagedRepository\ntitle: {name}\n{pin_block}---\n\n# {name}\n", encoding="utf-8")


def _declare(layout: WorkspaceLayout, paths: dict[str, Path]) -> Config:
    yaml = YAML()
    with layout.manifest_path.open(encoding="utf-8") as handle:
        data = yaml.load(handle)
    data["repositories"] = {name: {"path": str(path)} for name, path in paths.items()}
    data["state_gate"] = {"enabled": False}
    with layout.manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
        yaml.dump(data, handle)
    return load_workspace_config(layout)


def _clone(layout: WorkspaceLayout, name: str, repo_seed: Path, scratch: Path) -> tuple[Path, str, str]:
    """An in-bundle clone with two commits, detached at the older one; returns (path, old, new)."""
    clone = layout.bundle_dir / "repositories" / name / "references" / "git"
    clone.parent.mkdir(parents=True)
    copy_repo(repo_seed, scratch / name).rename(clone)
    old = git(clone, "rev-parse", "HEAD")
    new = commit_change(clone, "README.md", "# newer\n")
    git(clone, "checkout", "--detach", old)
    return clone, old, new


@pytest.fixture
def drifted(tmp_path: Path, repo_seed: Path) -> Drifted:
    layout = apply_init(plan_init(tmp_path / ".works", today=TODAY, topic="Scan")).layout
    clone, old, new = _clone(layout, "lane", repo_seed, tmp_path)
    _page(layout, "lane", new)
    return Drifted(layout, _declare(layout, {"lane": clone}), old, new)


def _snapshot(layout: WorkspaceLayout) -> dict[str, bytes]:
    root = layout.bundle_dir
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "references/git" not in p.as_posix()
    }


def test_pin_drift_names_the_repository_head_and_pin(drifted: Drifted) -> None:
    (drift,) = pin_drift(drifted.layout, drifted.config, None)
    assert (drift.name, drift.head, drift.pin) == ("lane", drifted.old, drifted.new)


def test_run_repo_scan_refuses_drift_and_writes_nothing(drifted: Drifted) -> None:
    before = _snapshot(drifted.layout)
    for dry_run in (True, False):
        run = run_repo_scan(drifted.layout, drifted.config, repo="lane", at=AT, today=AT.date(), dry_run=dry_run)
        assert run.refusal == "pin-drift" and run.applied is False
        assert run.detail is not None and "gw repo restore lane" in run.detail
        assert drifted.old[:7] in run.detail and drifted.new[:7] in run.detail
    assert _snapshot(drifted.layout) == before


def test_build_scan_worklist_raises_scan_error_naming_restore(drifted: Drifted) -> None:
    with pytest.raises(ScanError, match="gw repo restore lane"):
        asyncio.run(build_scan_worklist(drifted.layout, drifted.config, today=TODAY, at=AT))


def test_run_scan_refuses_drift(drifted: Drifted) -> None:
    with pytest.raises(ScanError, match="gw repo restore lane"):
        asyncio.run(run_scan(drifted.layout, drifted.config, today=TODAY, at=AT, narrate=False))


def test_clone_at_the_pin_is_not_drift(drifted: Drifted) -> None:
    git(drifted.config.repos[0].path, "checkout", "--detach", drifted.new)
    assert pin_drift(drifted.layout, drifted.config, None) == ()


def _failing_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the graph build the first thing past the guard to refuse, so a pass-through is observable."""
    monkeypatch.setattr(graph, "build", lambda *_a, **_k: graph.GraphResult(1, "", "error: boom"))


def test_check_pins_false_bypasses_the_guard(drifted: Drifted, monkeypatch: pytest.MonkeyPatch) -> None:
    _failing_build(monkeypatch)
    run = run_repo_scan(drifted.layout, drifted.config, repo="lane", at=AT, today=AT.date(), check_pins=False)
    assert run.refusal == "graph-build-failed"
    with pytest.raises(ScanError, match="boom"):
        asyncio.run(build_scan_worklist(drifted.layout, drifted.config, today=TODAY, at=AT, check_pins=False))


def test_drift_in_another_repository_is_ignored_when_scoped(
    drifted: Drifted, tmp_path: Path, repo_seed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _failing_build(monkeypatch)
    clean, _, new = _clone(drifted.layout, "clean", repo_seed, tmp_path / "other")
    git(clean, "checkout", "--detach", new)
    _page(drifted.layout, "clean", new)
    config = _declare(drifted.layout, {"lane": drifted.config.repos[0].path, "clean": clean})
    assert pin_drift(drifted.layout, config, frozenset({"clean"})) == ()
    assert [d.name for d in pin_drift(drifted.layout, config, None)] == ["lane"]
    run = run_repo_scan(drifted.layout, config, repo="clean", at=AT, today=AT.date())
    assert run.refusal == "graph-build-failed"


def test_missing_page_missing_pin_and_non_git_clone_are_not_claimed(
    tmp_path: Path, repo_seed: Path, drifted: Drifted
) -> None:
    layout = drifted.layout
    (layout.bundle_dir / "repositories" / "lane.md").unlink()
    assert pin_drift(layout, drifted.config, None) == ()
    _page(layout, "lane", None)
    assert pin_drift(layout, drifted.config, None) == ()
    _page(layout, "lane", drifted.new)
    plain = layout.bundle_dir / "repositories" / "plain" / "references" / "git"
    plain.mkdir(parents=True)
    _page(layout, "plain", drifted.new)
    config = _declare(layout, {"plain": plain})
    assert pin_drift(layout, config, None) == ()


def test_entry_outside_the_bundle_has_no_pin(tmp_path: Path, repo_seed: Path, drifted: Drifted) -> None:
    outside = copy_repo(repo_seed, tmp_path / "outside")
    config = _declare(drifted.layout, {"outside": outside})
    assert pin_drift(drifted.layout, config, None) == ()
