"""All-or-nothing planning and application for the complete code-wiki sync."""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest
from code_graph_io import open_reader
from code_graph_io.records import GraphEdge, GraphNode, GraphRecords
from code_graph_io.testing import open_store
from code_graph_io.update import run_workspace
from code_wiki_okf.config import Config, RepoConfig, StateGateConfig
from code_wiki_okf.entities.catalog import reconcile_catalogs
from code_wiki_okf.init import install_bundle
from code_wiki_okf.mirror.model import MirrorPlan, MirrorResult, MirrorTarget
from code_wiki_okf.placement import PlacementError
from code_wiki_okf.sync import plan_sync, sync_bundle
from code_wiki_okf.sync import run as run_module
from okf_ext.moves import MovePlan
from okf_io import load_bundle

_AT = "2026-01-01T00:00:00+00:00"
_TODAY = date(2026, 1, 1)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _bundle_bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _workspace(tmp_path: Path) -> tuple[Path, Path, Config]:
    repo_root = tmp_path / "demo"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text(
        '[project]\nname = "widgets"\nversion = "0.1.0"\ndependencies = []\n',
        encoding="utf-8",
    )
    (repo_root / "src" / "widgets.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo_root, "init", "-q", "-b", "main")
    _git(repo_root, "config", "user.email", "t@t")
    _git(repo_root, "config", "user.name", "t")
    _git(repo_root, "remote", "add", "origin", "https://github.com/acme/demo.git")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "init")

    graph_dir = tmp_path / "graph"
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = Config(
        graph_dir=graph_dir,
        declarations_dir=bundle_root,
        repos=(RepoConfig(name="demo", path=repo_root, ignore=()),),
        state_gate=StateGateConfig(enabled=False, branches=()),
    )
    return bundle_root, graph_dir, config


def test_preflight_failure_leaves_valid_entity_unwritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    monkeypatch.setattr(
        "code_wiki_okf.sync.run.tracked_files",
        lambda _config: {"demo": ("pyproject.toml", "src/widgets.py", "../secret.py")},
    )

    before = _bundle_bytes(bundle_root)
    with (
        open_reader(graph_dir=graph_dir) as reader,
        pytest.raises(PlacementError, match=r"file:acme/demo/\.\./secret\.py"),
    ):
        sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    assert _bundle_bytes(bundle_root) == before
    assert not (bundle_root / "repositories/demo/packages/widgets.md").exists()


def test_cross_lane_member_collision_leaves_bundle_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    before = _bundle_bytes(bundle_root)

    def colliding_plan(*args: object, **kwargs: object) -> MirrorPlan:
        return MirrorPlan(
            repo="demo",
            targets=(
                MirrorTarget(
                    resource="file:acme/demo/pyproject.toml",
                    source_path="pyproject.toml",
                    member="repositories/demo/packages/widgets.md",
                ),
            ),
            moves=MovePlan(root=bundle_root, moves=(), edits=(), refusals=(), unrebased=(), digests={}),
            creates={},
            updates={},
            deletions=(),
            declined_deletions=(),
        )

    monkeypatch.setattr("code_wiki_okf.sync.run.tracked_files", lambda _config: {"demo": ("pyproject.toml",)})
    monkeypatch.setattr("code_wiki_okf.sync.run.plan_mirror", colliding_plan)
    with (
        open_reader(graph_dir=graph_dir) as reader,
        pytest.raises(PlacementError, match=r"planned member.*also belongs"),
    ):
        sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    assert _bundle_bytes(bundle_root) == before


def test_cross_lane_filesystem_equivalent_collision_leaves_bundle_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    before = _bundle_bytes(bundle_root)

    def colliding_plan(*args: object, **kwargs: object) -> MirrorPlan:
        return MirrorPlan(
            repo="demo",
            targets=(
                MirrorTarget(
                    resource="file:acme/demo/pyproject.toml",
                    source_path="pyproject.toml",
                    member="repositories/demo/packages/Widgets.md",
                ),
            ),
            moves=MovePlan(root=bundle_root, moves=(), edits=(), refusals=(), unrebased=(), digests={}),
            creates={},
            updates={},
            deletions=(),
            declined_deletions=(),
        )

    monkeypatch.setattr("code_wiki_okf.sync.run.tracked_files", lambda _config: {"demo": ("pyproject.toml",)})
    monkeypatch.setattr("code_wiki_okf.sync.run.plan_mirror", colliding_plan)
    with open_reader(graph_dir=graph_dir) as reader, pytest.raises(PlacementError, match="filesystem-equivalent"):
        plan_sync(bundle_root, config=config, reader=reader, at=_AT)

    assert _bundle_bytes(bundle_root) == before


def test_composite_preflight_refuses_late_mirror_ancestor_conflict_before_entities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_sync(bundle_root, config=config, reader=reader, at=_AT)

    occupied = bundle_root / "repositories" / "demo" / "FILES"
    occupied.parent.mkdir(parents=True, exist_ok=True)
    occupied.write_bytes(b"pre-existing ancestor file\n")
    before = _bundle_bytes(bundle_root)
    monkeypatch.setattr("code_wiki_okf.sync.run.plan_sync", lambda *_args, **_kwargs: plan)

    with open_reader(graph_dir=graph_dir) as reader, pytest.raises(PlacementError, match="filesystem-equivalent"):
        sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    assert _bundle_bytes(bundle_root) == before
    assert all(not (bundle_root / write.member).exists() for write in plan.entities.writes)


def test_composite_preflight_refuses_late_mirror_equivalent_member_before_entities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_sync(bundle_root, config=config, reader=reader, at=_AT)

    target = next(
        target for mirror in plan.mirrors for target in mirror.targets if target.source_path == "pyproject.toml"
    )
    occupied = bundle_root / "repositories" / "demo" / "FILES" / "PYPROJECT.TOML.md"
    occupied.parent.mkdir(parents=True, exist_ok=True)
    occupied.write_bytes(b"pre-existing equivalent member\n")
    before = _bundle_bytes(bundle_root)
    monkeypatch.setattr("code_wiki_okf.sync.run.plan_sync", lambda *_args, **_kwargs: plan)

    with open_reader(graph_dir=graph_dir) as reader, pytest.raises(PlacementError, match="filesystem-equivalent"):
        sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    assert _bundle_bytes(bundle_root) == before
    assert target.member not in _bundle_bytes(bundle_root)
    assert all(not (bundle_root / write.member).exists() for write in plan.entities.writes)


def test_composite_preflight_refuses_late_exact_mirror_create_occupant_before_entities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_sync(bundle_root, config=config, reader=reader, at=_AT)

    target = next(
        target for mirror in plan.mirrors for target in mirror.targets if target.source_path == "pyproject.toml"
    )
    assert target.source_path in plan.mirrors[0].creates
    occupied = bundle_root / target.member
    occupied.parent.mkdir(parents=True, exist_ok=True)
    occupied.write_bytes(b"pre-existing planned destination\n")
    before = _bundle_bytes(bundle_root)
    monkeypatch.setattr("code_wiki_okf.sync.run.plan_sync", lambda *_args, **_kwargs: plan)

    with (
        open_reader(graph_dir=graph_dir) as reader,
        pytest.raises(PlacementError, match=r"planned destination.*occupied"),
    ):
        sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    assert _bundle_bytes(bundle_root) == before
    assert all(not (bundle_root / write.member).exists() for write in plan.entities.writes)


def test_composite_preflight_refuses_directory_at_derived_mirror_index_before_entities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_sync(bundle_root, config=config, reader=reader, at=_AT)

    occupied = bundle_root / "repositories" / "demo" / "files" / "index.md"
    occupied.mkdir(parents=True)
    before = _bundle_bytes(bundle_root)
    monkeypatch.setattr("code_wiki_okf.sync.run.plan_sync", lambda *_args, **_kwargs: plan)

    with open_reader(graph_dir=graph_dir) as reader, pytest.raises(PlacementError, match="path type conflict"):
        sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    assert _bundle_bytes(bundle_root) == before
    assert occupied.is_dir()
    assert all(not (bundle_root / write.member).exists() for write in plan.entities.writes)


def test_dry_run_reports_the_complete_plan_and_writes_nothing(tmp_path: Path) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    before = _bundle_bytes(bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        result = sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY, dry_run=True)

    assert result.dry_run is True
    assert "repositories/demo/packages/widgets" in result.entities.created
    assert result.entities.updated == ()
    assert result.entities.written == result.entities.created
    assert "repositories/demo/packages/index.md" in result.entities.catalog_created
    assert "repositories/demo/packages/index.md" not in result.entities.catalog_updated
    assert set(result.entities.catalog_created).isdisjoint(result.entities.catalog_updated)
    assert "index.md" in result.entities.catalog_updated
    assert result.mirror.plans[0].creates
    assert result.mirror.results == ()
    assert _bundle_bytes(bundle_root) == before


def test_mirror_result_failures_make_composite_summary_non_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)

    def incomplete_apply(_root: Path, plan: MirrorPlan, **_kwargs: object) -> MirrorResult:
        return MirrorResult(
            repo=plan.repo,
            moved=(),
            created=(),
            regenerated=(),
            deleted=(),
            declined_deletions=(),
            index_updates=(),
            failed=("repositories/demo/files/src/widgets.py.md: commit-error: disk full",),
        )

    monkeypatch.setattr("code_wiki_okf.sync.run.apply_mirror", incomplete_apply)
    with open_reader(graph_dir=graph_dir) as reader:
        result = sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    assert result.mirror.failed_repos == (
        ("demo", "repositories/demo/files/src/widgets.py.md: commit-error: disk full"),
    )
    assert not result.ok


def test_dry_catalog_categories_match_post_mirror_catalog_application(tmp_path: Path) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    before = _bundle_bytes(bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        preview = sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY, dry_run=True)
        assert _bundle_bytes(bundle_root) == before
        applied = sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    mirror_indexes = {
        "repositories/demo/index.md",
        "repositories/demo/files/index.md",
        "repositories/demo/files/src/index.md",
    }
    assert mirror_indexes.isdisjoint(preview.entities.catalog_created)
    assert mirror_indexes <= set(preview.entities.catalog_updated)
    assert preview.entities.catalog_created == applied.entities.catalog_created
    assert preview.entities.catalog_updated == applied.entities.catalog_updated


def test_dry_run_after_identical_sync_reports_no_entity_or_catalog_work(tmp_path: Path) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    with open_reader(graph_dir=graph_dir) as reader:
        sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)
        before = _bundle_bytes(bundle_root)
        preview = sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY, dry_run=True)

    assert preview.entities.created == ()
    assert preview.entities.updated == ()
    assert preview.entities.written == ()
    assert preview.entities.deleted == ()
    assert preview.entities.declined == ()
    assert preview.entities.catalog_created == ()
    assert preview.entities.catalog_updated == ()
    assert preview.entities.catalog == ()
    assert all(plan.is_empty for plan in preview.mirror.plans)
    assert _bundle_bytes(bundle_root) == before


def test_dry_run_reports_entity_updates_without_relabeling_them_as_creates(tmp_path: Path) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    with open_reader(graph_dir=graph_dir) as reader:
        sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    repo_root = config.repos[0].path
    manifest = repo_root / "pyproject.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace('version = "0.1.0"', 'version = "0.2.0"'))
    _git(repo_root, "add", "pyproject.toml")
    _git(repo_root, "commit", "-q", "-m", "bump version")
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    before = _bundle_bytes(bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        preview = sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY, dry_run=True)

    assert "repositories/demo/packages/widgets" in preview.entities.updated
    assert "repositories/demo/packages/widgets" not in preview.entities.created
    assert "repositories/demo/packages/widgets" in preview.entities.written
    assert _bundle_bytes(bundle_root) == before


def test_dry_run_reports_guarded_stale_delete_and_catalog_updates_without_writes(tmp_path: Path) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    with open_reader(graph_dir=graph_dir) as reader:
        sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    stale = bundle_root / "repositories/demo/packages/stale.md"
    stale.write_text(
        '---\ntype: Package\ntitle: "stale"\nresource: "pkg:acme/demo/stale"\n'
        "generated:\n  by: code-wiki-okf/0.4.0\n  at: '2026-01-01T00:00:00+00:00'\n---\n\n"
        "## Purpose\n\n> TODO: what this package does, who uses it, and why it exists, in one paragraph.\n\n"
        "## Public API\n\n> TODO: the main exports and when to use them. "
        "Link code with backticked `path:line` references.\n\n"
        "## Files\n\n_(none)_\n",
        encoding="utf-8",
    )
    reconcile_catalogs(load_bundle(bundle_root), today=_TODAY)
    before = _bundle_bytes(bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        preview = sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY, dry_run=True)

    assert preview.entities.deleted == ("repositories/demo/packages/stale",)
    assert preview.entities.declined == ()
    assert {
        "index.md",
        "repositories/demo/index.md",
        "repositories/demo/packages/index.md",
        "repositories/demo/repository.md",
    } <= set(preview.entities.catalog_updated)
    assert preview.entities.catalog_created == ()
    assert _bundle_bytes(bundle_root) == before


def test_dry_run_reports_guarded_stale_decline_without_writes(tmp_path: Path) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    with open_reader(graph_dir=graph_dir) as reader:
        sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    stale = bundle_root / "repositories/demo/packages/retained.md"
    stale.write_text(
        '---\ntype: Package\ntitle: "retained"\nresource: "pkg:acme/demo/retained"\n'
        "generated:\n  by: code-wiki-okf/0.4.0\n  at: '2026-01-01T00:00:00+00:00'\n---\n\n"
        "## Purpose\n\nA human explanation that must be retained.\n\n"
        "## Public API\n\n> TODO: the main exports and when to use them. "
        "Link code with backticked `path:line` references.\n\n"
        "## Files\n\n_(none)_\n",
        encoding="utf-8",
    )
    before = _bundle_bytes(bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        preview = sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY, dry_run=True)

    assert preview.entities.deleted == ()
    assert preview.entities.declined == (("repositories/demo/packages/retained", "prose-edited"),)
    assert _bundle_bytes(bundle_root) == before


def test_second_composite_sync_is_idempotent_and_keeps_file_resources_current(tmp_path: Path) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    with open_reader(graph_dir=graph_dir) as reader:
        first = sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)
        after_first = _bundle_bytes(bundle_root)
        second = sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    assert first.dry_run is False
    assert first.ok is True
    assert first.entities.ok is True
    assert first.mirror.ok is True
    assert first.mirror.created == 2
    assert first.mirror.regenerated == 0
    assert first.mirror.moved == 0
    assert first.mirror.deleted == 0
    assert first.mirror.declined == 0
    assert first.mirror.stranded == 0
    assert second.entities.written == ()
    assert all(plan.is_empty for plan in second.mirror.plans)
    assert _bundle_bytes(bundle_root) == after_first
    assert (bundle_root / "repositories/demo/files/src/widgets.py.md").is_file()
    assert (bundle_root / "repositories/demo/packages/index.md").is_file()
    assert "/repositories/demo/packages/widgets.md" in (bundle_root / "repositories/demo/packages/index.md").read_text(
        encoding="utf-8"
    )


def test_second_wet_sync_ignores_derived_index_conflict_for_an_empty_mirror_plan(tmp_path: Path) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    with open_reader(graph_dir=graph_dir) as reader:
        sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    unrelated_index = bundle_root / "repositories" / "demo" / "files" / "index.md"
    unrelated_index.unlink()
    unrelated_index.mkdir()
    before = _bundle_bytes(bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        second = sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    assert second.mirror.ok is True
    # The skip is safe ONLY because the catalog lane still reports the
    # corrupted index and fails the run. Pin that, or a later change could
    # silence it and this test would still pass.
    assert second.ok is False
    assert ("repositories/demo/files/index.md", "stale") in second.entities.catalog_declined
    assert second.entities.written == ()
    assert all(plan.is_empty for plan in second.mirror.plans)
    assert all(
        result.moved == () and result.created == () and result.regenerated == () and result.deleted == ()
        for result in second.mirror.results
    )
    assert _bundle_bytes(bundle_root) == before
    assert unrelated_index.is_dir()


def test_composite_sync_refuses_a_timestamp_without_timezone_before_writes(tmp_path: Path) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    before = _bundle_bytes(bundle_root)

    with open_reader(graph_dir=graph_dir) as reader, pytest.raises(ValueError, match="timezone offset"):
        plan_sync(bundle_root, config=config, reader=reader, at="2026-01-01T00:00:00")

    assert _bundle_bytes(bundle_root) == before


def test_plan_is_frozen_and_carries_file_resources_into_prune_guard(tmp_path: Path) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_sync(bundle_root, config=config, reader=reader, at=_AT)

    assert "file:acme/demo/src/widgets.py" in plan.entities.current_resources
    with pytest.raises(AttributeError):
        plan.warnings = ("changed",)  # type: ignore[misc]


def test_non_git_skip_preserves_existing_file_pages(tmp_path: Path) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    with open_reader(graph_dir=graph_dir) as reader:
        sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    repo_root = config.repos[0].path
    (repo_root / ".git").rename(repo_root / ".git-disabled")
    with open_reader(graph_dir=graph_dir) as reader:
        result = sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    assert result.mirror.skipped_repos == ("demo",)
    assert (bundle_root / "repositories/demo/files/src/widgets.py.md").is_file()


def test_entity_named_index_is_refused_before_any_write(tmp_path: Path) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    store = open_store(graph_dir / "code.db", create=True)
    try:
        store.set_current_repo("repo:acme/demo")
        with store.transaction() as transaction:
            transaction.upsert_records(
                GraphRecords(
                    nodes=(
                        GraphNode(
                            kind="package",
                            name="index",
                            path="index/pyproject.toml",
                            line=None,
                            attrs={
                                "uri": "pkg:acme/demo/index",
                                "language": "python",
                                "version": "1.0.0",
                            },
                        ),
                    ),
                    edges=(),
                )
            )
        store.set_current_repo(None)
    finally:
        store.close()

    before = _bundle_bytes(bundle_root)
    with (
        open_reader(graph_dir=graph_dir) as reader,
        pytest.raises(PlacementError, match=r"reserved directory index\.md"),
    ):
        sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    assert _bundle_bytes(bundle_root) == before


def test_multi_implementation_dependency_warns_without_selecting_one(tmp_path: Path) -> None:
    bundle_root, graph_dir, config = _workspace(tmp_path)
    store = open_store(graph_dir / "code.db", create=True)
    try:
        store.set_current_repo("repo:acme/demo")
        with store.transaction() as transaction:
            transaction.upsert_records(
                GraphRecords(
                    nodes=(
                        GraphNode(
                            kind="package",
                            name="alternate",
                            path="alternate/pyproject.toml",
                            line=None,
                            attrs={
                                "uri": "pkg:acme/demo/alternate",
                                "language": "python",
                                "version": "1.0.0",
                            },
                        ),
                    ),
                    edges=(),
                )
            )
        store.set_current_repo(None)
        with store.transaction() as transaction:
            transaction.upsert_records(
                GraphRecords(
                    nodes=(
                        GraphNode(
                            kind="dependency",
                            name="shared",
                            path=None,
                            line=None,
                            attrs={
                                "uri": "dependency:pypi/shared",
                                "ecosystem": "pypi",
                                "versions_in_use": ["1.0"],
                            },
                        ),
                    ),
                    edges=(
                        GraphEdge(
                            src=("dependency", "shared", None),
                            dst=("package", "widgets", ""),
                            kind="implemented_by",
                            attrs={},
                        ),
                        GraphEdge(
                            src=("dependency", "shared", None),
                            dst=("package", "alternate", "alternate/pyproject.toml"),
                            kind="implemented_by",
                            attrs={},
                        ),
                    ),
                )
            )
    finally:
        store.close()

    with open_reader(graph_dir=graph_dir) as reader:
        result = sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    assert result.ok
    assert result.warnings == (
        "dependency:pypi/shared has multiple implementations: pkg:acme/demo/alternate, pkg:acme/demo/widgets",
    )
    dependency = load_bundle(bundle_root).concept("dependencies/pypi/shared")
    assert dependency is None
    index_path = bundle_root / "dependencies/pypi/index.md"
    if index_path.exists():
        assert "/dependencies/pypi/shared.md" not in index_path.read_text(encoding="utf-8")


def test_plan_time_preflight_refuses_an_entity_target_blocked_by_a_directory(tmp_path: Path) -> None:
    """`_preflight_existing` (`entities/sync.py`) must refuse at plan time,
    before `apply_entities` writes anything, so a blocked entity target
    already occupied on disk can never leave the bundle half-written.
    """
    bundle_root, graph_dir, config = _workspace(tmp_path)
    blocked = bundle_root / "repositories" / "demo" / "packages" / "widgets.md"
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.mkdir()
    before = _bundle_bytes(bundle_root)

    with (
        open_reader(graph_dir=graph_dir) as reader,
        pytest.raises(PlacementError, match="delete the old pre-release page"),
    ):
        sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    assert _bundle_bytes(bundle_root) == before


def test_plan_time_preflight_refuses_a_case_equivalent_entity_occupant(tmp_path: Path) -> None:
    """Two members differing only by case or Unicode form are one file on a
    macOS or Windows checkout, so the plan-time refusal is a portability
    guarantee, not a nicety.
    """
    bundle_root, graph_dir, config = _workspace(tmp_path)
    occupant = bundle_root / "repositories" / "demo" / "packages" / "WIDGETS.md"
    occupant.parent.mkdir(parents=True, exist_ok=True)
    occupant.write_text("---\ntype: Package\n---\n", encoding="utf-8")
    before = _bundle_bytes(bundle_root)

    with (
        open_reader(graph_dir=graph_dir) as reader,
        pytest.raises(PlacementError, match="delete the old pre-release page"),
    ):
        sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    assert _bundle_bytes(bundle_root) == before


def test_wet_sync_refuses_entity_target_drift_between_plan_and_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entity live preflight guards the window between planning and
    applying: a target that was free when the plan was computed can be
    occupied by another writer before the first entity page lands. Plan-time
    preflight cannot see that, which is why this second check exists.
    """
    bundle_root, graph_dir, config = _workspace(tmp_path)
    real_plan_sync = run_module.plan_sync
    captured: dict[str, dict[str, bytes]] = {}

    def plan_then_drift(bundle_root_arg: Path, *, config: Config, reader: object, at: str) -> run_module.SyncPlan:
        plan = real_plan_sync(bundle_root_arg, config=config, reader=reader, at=at)  # type: ignore[arg-type]
        blocked = bundle_root / "repositories" / "demo" / "packages" / "widgets.md"
        blocked.parent.mkdir(parents=True, exist_ok=True)
        blocked.mkdir()
        captured["after_drift"] = _bundle_bytes(bundle_root)
        return plan

    monkeypatch.setattr(run_module, "plan_sync", plan_then_drift)

    with (
        open_reader(graph_dir=graph_dir) as reader,
        pytest.raises(PlacementError, match="re-plan"),
    ):
        sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    assert _bundle_bytes(bundle_root) == captured["after_drift"]


def test_wet_sync_refuses_a_case_equivalent_entity_target_drift_between_plan_and_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Companion to the directory-drift case above, exercising the OTHER
    live-preflight branch: a case/Unicode-equivalent occupant that appears
    (rather than a path-type conflict) between planning and applying.
    """
    bundle_root, graph_dir, config = _workspace(tmp_path)
    real_plan_sync = run_module.plan_sync
    captured: dict[str, dict[str, bytes]] = {}

    def plan_then_drift(bundle_root_arg: Path, *, config: Config, reader: object, at: str) -> run_module.SyncPlan:
        plan = real_plan_sync(bundle_root_arg, config=config, reader=reader, at=at)  # type: ignore[arg-type]
        occupant = bundle_root / "repositories" / "demo" / "packages" / "WIDGETS.md"
        occupant.parent.mkdir(parents=True, exist_ok=True)
        occupant.write_text("---\ntype: Package\n---\n", encoding="utf-8")
        captured["after_drift"] = _bundle_bytes(bundle_root)
        return plan

    monkeypatch.setattr(run_module, "plan_sync", plan_then_drift)

    with (
        open_reader(graph_dir=graph_dir) as reader,
        pytest.raises(PlacementError, match="re-plan"),
    ):
        sync_bundle(bundle_root, config=config, reader=reader, at=_AT, today=_TODAY)

    assert _bundle_bytes(bundle_root) == captured["after_drift"]
