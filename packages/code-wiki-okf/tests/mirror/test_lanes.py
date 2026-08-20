"""`sync_mirror`: the whole mirror lane as a library call.

The four branches that only exist at this level -- the dry-run split, the
skipped repo, the failed repo, the one-entry log -- have no other test, and
the package gates at 95% branch coverage.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from code_graph_io.handle import open_reader
from code_graph_io.update import run_workspace
from code_wiki_okf.config import Config, load_config
from code_wiki_okf.init import install_bundle
from code_wiki_okf.mirror.lanes import MirrorSummary, sync_mirror

_AT = datetime(2026, 1, 1, tzinfo=UTC)
_TODAY = date(2026, 1, 1)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_git_repo(repo_root: Path, filename: str, content: str) -> None:
    repo_root.mkdir(parents=True)
    (repo_root / filename).write_text(content, encoding="utf-8")
    _git(repo_root, "init", "-q", "-b", "main")
    _git(repo_root, "config", "user.email", "t@t")
    _git(repo_root, "config", "user.name", "t")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "init")


def _workspace(tmp_path: Path, *repo_names: str) -> tuple[Path, Config]:
    """A bundle plus one graphed, git-backed repo per name in *repo_names*,
    config already pointing at all of them.
    """
    graph_dir = tmp_path / "graph"
    repo_roots = []
    for name in repo_names:
        repo_root = tmp_path / name
        _init_git_repo(repo_root, "a.py", "VALUE = 1\n")
        repo_roots.append(repo_root)
    run_workspace(repo_roots, graph_dir=graph_dir, full=True)

    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)

    repos_yaml = "\n".join(f"  {name}:\n    path: {tmp_path / name}" for name in repo_names)
    (bundle_root / "_repositories.yaml").write_text(
        f"graph_dir: {graph_dir}\nrepositories:\n{repos_yaml}\n", encoding="utf-8"
    )
    config = load_config(bundle_root)
    return bundle_root, config


def test_dry_run_plans_and_writes_nothing(tmp_path: Path) -> None:
    """`plan_mirror` is genuinely read-only, so a dry run here is a real
    preview -- unlike `entities.lanes.sync(dry_run=True)`, which calls
    nothing. `code-wiki-okf sync --dry-run`'s per-repo plan output depends on
    this (D4).
    """
    bundle_root, config = _workspace(tmp_path, "acme")
    with open_reader(graph_dir=config.graph_dir) as reader:
        summary = sync_mirror(bundle_root, config, reader, today=_TODAY, at=_AT, dry_run=True)

    assert len(summary.plans) == 1
    assert summary.results == ()
    assert summary.plans[0].creates  # it saw work to do
    assert not (bundle_root / "repositories" / "acme" / "fs").exists()
    assert "mirror sync" not in (bundle_root / "log.md").read_text(encoding="utf-8")


def test_wet_run_applies_and_logs_once(tmp_path: Path) -> None:
    bundle_root, config = _workspace(tmp_path, "acme")
    with open_reader(graph_dir=config.graph_dir) as reader:
        summary = sync_mirror(bundle_root, config, reader, today=_TODAY, at=_AT, dry_run=False)

    assert summary.ok
    assert summary.created == 1
    assert (bundle_root / "repositories" / "acme" / "fs" / "a.py.md").exists()
    log_text = (bundle_root / "log.md").read_text(encoding="utf-8")
    assert log_text.count("mirror sync:") == 1


def test_a_second_run_writes_nothing_and_logs_nothing_new(tmp_path: Path) -> None:
    """`_render_matches_disk` short-circuits unchanged updates, but the run
    still *applied*, so it still logs -- one entry per applied run, matching
    `entities.lanes.sync`. Asserting the count goes 1 -> 2 pins that.
    """
    bundle_root, config = _workspace(tmp_path, "acme")
    with open_reader(graph_dir=config.graph_dir) as reader:
        sync_mirror(bundle_root, config, reader, today=_TODAY, at=_AT, dry_run=False)
        second = sync_mirror(bundle_root, config, reader, today=_TODAY, at=_AT, dry_run=False)

    assert second.created == 0
    assert (bundle_root / "log.md").read_text(encoding="utf-8").count("mirror sync:") == 2


def test_a_non_git_repo_is_skipped_not_raised(tmp_path: Path) -> None:
    """`git_state`'s never-raise contract, carried up one level."""
    graph_dir = tmp_path / "graph"
    run_workspace([], graph_dir=graph_dir, full=True)
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)

    non_git = tmp_path / "not-a-repo"
    non_git.mkdir()
    (bundle_root / "_repositories.yaml").write_text(
        f"graph_dir: {graph_dir}\nrepositories:\n  acme:\n    path: {non_git}\n", encoding="utf-8"
    )
    config = load_config(bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        summary = sync_mirror(bundle_root, config, reader, today=_TODAY, at=_AT, dry_run=False)

    assert summary.skipped_repos == ("acme",)
    assert summary.results == ()
    assert summary.ok


def test_one_repo_failing_does_not_abort_the_others(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A filesystem error in repo A must not cost repo B its turn, and must
    not vanish either.
    """
    bundle_root, config = _workspace(tmp_path, "acme", "beta")

    import code_wiki_okf.mirror.lanes as lanes_module

    real_apply = lanes_module.apply_mirror

    def _boom_on_acme(bundle, plan, repo, **kwargs):  # type: ignore[no-untyped-def]
        if repo.name == "acme":
            raise OSError("disk full")
        return real_apply(bundle, plan, repo, **kwargs)

    monkeypatch.setattr("code_wiki_okf.mirror.lanes.apply_mirror", _boom_on_acme)
    with open_reader(graph_dir=config.graph_dir) as reader:
        summary = sync_mirror(bundle_root, config, reader, today=_TODAY, at=_AT, dry_run=False)

    assert summary.failed_repos == (("acme", "disk full"),)
    assert not summary.ok
    assert [result.repo for result in summary.results] == ["beta"]


def test_missing_sections_directory_propagates(tmp_path: Path) -> None:
    """`load_sections`' `OSError` reaches the caller unwrapped, matching
    `entities.lanes.sync`. The CLI is what turns it into a clean exit-1
    message; a library does not decide that.
    """
    bundle_root, config = _workspace(tmp_path, "acme")
    shutil.rmtree(config.declarations_dir / "_sections")
    with open_reader(graph_dir=config.graph_dir) as reader, pytest.raises(OSError):
        sync_mirror(bundle_root, config, reader, today=_TODAY, at=_AT, dry_run=True)


def test_empty_summary_reports_zero_counts() -> None:
    summary = MirrorSummary()
    assert summary.ok
    assert (summary.created, summary.regenerated, summary.moved, summary.deleted, summary.declined) == (
        0,
        0,
        0,
        0,
        0,
    )
