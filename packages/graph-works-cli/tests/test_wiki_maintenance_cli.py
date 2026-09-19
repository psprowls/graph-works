"""`gw wiki` maintenance commands — stats, index, and archive."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_cli.wiki_cli import maintenance
from graph_works_core.wiki_stats.commands import HubEntry, WikiStats
from helpers import initialized_workspace as _initialized_workspace
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def initialized_workspace(tmp_path: Path) -> Path:
    """Create the smallest real initialized workspace for CLI boundary tests."""
    return _initialized_workspace(tmp_path / "works")


def test_stats_uses_the_default_top_and_the_seven_key_json_contract(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A wrong top default or leaked result fields would break stats consumers."""
    bundle = object()
    calls: list[object] = []
    stats = WikiStats(
        2, 1, 1, (HubEntry("concepts/a", 1),), (HubEntry("concepts/b", 1),), ("concepts/a",), ("concepts/b",)
    )

    monkeypatch.setattr(maintenance, "load_bundle", lambda root: bundle)

    def fake_compute_stats(actual_bundle: object, *, top: int) -> WikiStats:
        calls.append((actual_bundle, top))
        return stats

    monkeypatch.setattr(maintenance, "compute_stats", fake_compute_stats)

    result = runner.invoke(app, ["wiki", "stats", "--json", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert calls == [(bundle, 10)]
    assert json.loads(result.stdout) == {
        "total_pages": 2,
        "total_edges": 1,
        "component_count": 1,
        "top_outbound_hubs": [{"page": "concepts/a", "degree": 1}],
        "top_inbound_hubs": [{"page": "concepts/b", "degree": 1}],
        "orphans": ["concepts/a"],
        "sinks": ["concepts/b"],
    }


def test_index_reconciles_every_directory_and_reports_changed_indexes(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """Narrow indexing would leave a bundle with stale or missing indexes."""
    bundle = object()
    calls: list[tuple[object, object, bool, bool]] = []

    monkeypatch.setattr(maintenance, "load_bundle", lambda root: bundle)

    def fake_update_index(
        actual_bundle: object, *, directories: object, create_missing: bool, dry_run: bool
    ) -> tuple[SimpleNamespace, ...]:
        calls.append((actual_bundle, directories, create_missing, dry_run))
        return (
            SimpleNamespace(path="concepts/index.md", changed=True),
            SimpleNamespace(path="sources/index.md", changed=False),
        )

    monkeypatch.setattr(maintenance, "update_index", fake_update_index)

    result = runner.invoke(app, ["wiki", "index", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert calls == [(bundle, None, True, False)]
    assert result.stdout == "concepts/index.md\n"


def test_index_reports_a_noop_without_an_empty_human_response(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A successful no-op needs an explicit human-readable result."""
    monkeypatch.setattr(maintenance, "load_bundle", lambda root: object())
    monkeypatch.setattr(maintenance, "update_index", lambda *_args, **_kwargs: ())

    result = runner.invoke(app, ["wiki", "index", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert result.stdout == "nothing to do\n"


def _run(
    *,
    ok: bool = True,
    applied: bool = True,
    archived: tuple[str, ...] = (),
    refusals: tuple[object, ...] = (),
    failed: tuple[object, ...] = (),
    diff: str = "archive plan",
) -> SimpleNamespace:
    plan = SimpleNamespace(ok=True, path_mapping={}, warnings=(), refusals=(), move_plan=None, moves=(), writes=())
    wiki_plan = SimpleNamespace(
        ok=ok,
        tokens=archived,
        skipped=(),
        diff=lambda: diff,
        moves=SimpleNamespace(moves=(), refusals=refusals, stranded=()),
    )
    result = SimpleNamespace(ok=True, written=(), warnings=(), rolled_back=False, failures=()) if applied else None
    wiki = (
        SimpleNamespace(
            ok=not failed and not refusals,
            archived=archived,
            refusals=(),
            move=SimpleNamespace(failed=failed),
            indexes=(),
        )
        if applied
        else None
    )
    return SimpleNamespace(
        plan=plan,
        wiki_plan=wiki_plan,
        conflict=(),
        pointer_cleared=False,
        logged=None,
        result=result,
        wiki=wiki,
        ok=ok,
    )


@pytest.fixture
def archive_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, object, object, date, bool]]:
    calls: list[tuple[object, object, object, date, bool]] = []

    def fake_run_archive(
        layout: object, paths: object, tokens: object, *, today: date, dry_run: bool
    ) -> SimpleNamespace:
        calls.append((layout, paths, tokens, today, dry_run))
        return _run()

    monkeypatch.setattr(maintenance, "run_archive", fake_run_archive)
    return calls


@pytest.mark.parametrize(("target", "expected_tokens"), (("sources/one", ["sources/one"]), (None, None)))
def test_archive_routes_through_run_archive_with_no_work_selection(
    archive_calls: list[tuple[object, object, object, date, bool]],
    initialized_workspace: Path,
    target: str | None,
    expected_tokens: list[str] | None,
) -> None:
    args = ["wiki", "archive", "--workspace", str(initialized_workspace)]
    if target is not None:
        args.insert(2, target)

    result = runner.invoke(app, args)

    assert result.exit_code == 0
    layout, paths, tokens, today, dry_run = archive_calls[0]
    assert layout == maintenance.resolve_workspace(str(initialized_workspace))
    assert paths == () and tokens == expected_tokens and isinstance(today, date) and dry_run is False


def test_archive_dry_run_never_applies(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(
        maintenance,
        "run_archive",
        lambda *_args, **kwargs: calls.append(kwargs["dry_run"]) or _run(applied=False, diff="preview"),
    )

    result = runner.invoke(
        app, ["wiki", "archive", "sources/one", "--dry-run", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 0
    assert result.stdout == "preview\n"
    assert calls == [True]


def test_archive_dry_run_prints_a_refused_plan_without_applying(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    monkeypatch.setattr(
        maintenance,
        "run_archive",
        lambda *_args, **_kwargs: _run(ok=False, applied=False, diff="refused preview"),
    )

    result = runner.invoke(
        app, ["wiki", "archive", "sources/one", "--dry-run", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == "refused preview\n"
    assert "archive plan was refused" in result.stderr


def test_archive_refusals_and_incomplete_moves_fail(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    monkeypatch.setattr(maintenance, "run_archive", lambda *_args, **_kwargs: _run(ok=False, applied=False))
    refused = runner.invoke(app, ["wiki", "archive", "sources/one", "--workspace", str(initialized_workspace)])
    monkeypatch.setattr(
        maintenance,
        "run_archive",
        lambda *_args, **_kwargs: _run(failed=(SimpleNamespace(path="a", kind="move", error="disk"),)),
    )
    incomplete = runner.invoke(app, ["wiki", "archive", "sources/one", "--workspace", str(initialized_workspace)])

    assert refused.exit_code == incomplete.exit_code == exit_codes.GENERIC
    assert "archive plan was refused" in refused.stderr
    assert "archive was incomplete" in incomplete.stderr


def test_stats_renders_a_human_summary_when_json_is_not_requested(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """The human view is the default, so a broken renderer hides every stat behind --json."""
    stats = WikiStats(
        2, 1, 1, (HubEntry("concepts/a", 3),), (HubEntry("concepts/b", 2),), ("concepts/c",), ("concepts/d",)
    )
    monkeypatch.setattr(maintenance, "load_bundle", lambda root: object())
    monkeypatch.setattr(maintenance, "compute_stats", lambda _bundle, *, top: stats)

    result = runner.invoke(app, ["wiki", "stats", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert result.stdout == (
        "Pages: 2\n"
        "Edges: 1\n"
        "Components: 1\n"
        "Outbound hubs: concepts/a (3)\n"
        "Inbound hubs: concepts/b (2)\n"
        "Orphans: concepts/c\n"
        "Sinks: concepts/d\n"
    )


def test_stats_names_every_empty_section_rather_than_trailing_a_bare_label(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A healthy wiki has no orphans; empty labels would read as a truncated report."""
    monkeypatch.setattr(maintenance, "load_bundle", lambda root: object())
    monkeypatch.setattr(maintenance, "compute_stats", lambda _bundle, *, top: WikiStats(1, 0, 1, (), (), (), ()))

    result = runner.invoke(app, ["wiki", "stats", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert result.stdout == (
        "Pages: 1\nEdges: 0\nComponents: 1\nOutbound hubs: none\nInbound hubs: none\nOrphans: none\nSinks: none\n"
    )


@pytest.mark.parametrize("failure", (OSError("bundle is unreadable"), ValueError("malformed link graph")))
def test_stats_reports_an_unreadable_bundle_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path, failure: Exception
) -> None:
    """A raw traceback out of a read-only query gives the operator nothing to act on."""

    def fail(*_args: object, **_kwargs: object) -> object:
        raise failure

    monkeypatch.setattr(maintenance, "load_bundle", fail)

    result = runner.invoke(app, ["wiki", "stats", "--workspace", str(initialized_workspace)])

    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == ""
    assert f"Error: {failure}" in result.stderr


def test_index_reports_a_failed_reconcile_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """`index` writes to the bundle; a swallowed failure would look like a clean reconcile."""

    def fail(*_args: object, **_kwargs: object) -> object:
        raise OSError("index.md is read-only")

    monkeypatch.setattr(maintenance, "load_bundle", lambda root: object())
    monkeypatch.setattr(maintenance, "update_index", fail)

    result = runner.invoke(app, ["wiki", "index", "--workspace", str(initialized_workspace)])

    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == ""
    assert "Error: index.md is read-only" in result.stderr


def test_archive_reports_an_unplannable_bundle_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """Planning reads the whole bundle; its failure must not surface as a crash."""

    def fail(*_args: object, **_kwargs: object) -> object:
        raise ValueError("sources/one is not archivable")

    monkeypatch.setattr(maintenance, "run_archive", fail)

    result = runner.invoke(app, ["wiki", "archive", "sources/one", "--workspace", str(initialized_workspace)])

    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == ""
    assert "Error: sources/one is not archivable" in result.stderr


def test_archive_reports_a_failed_move_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A move that raises mid-flight leaves the bundle split; the operator must hear about it."""

    def fail(*_args: object, **_kwargs: object) -> object:
        raise OSError("destination is not writable")

    monkeypatch.setattr(maintenance, "run_archive", fail)

    result = runner.invoke(app, ["wiki", "archive", "sources/one", "--workspace", str(initialized_workspace)])

    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == ""
    assert "Error: destination is not writable" in result.stderr


def test_archive_echoes_every_archived_token(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    """A silent archive gives no record of what moved."""
    monkeypatch.setattr(
        maintenance,
        "run_archive",
        lambda *_args, **_kwargs: _run(archived=("sources/one", "sources/two")),
    )

    result = runner.invoke(app, ["wiki", "archive", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert result.stdout == "sources/one\nsources/two\n"


def _workspace_with_wikilink_into(tmp_path: Path, token: str) -> Path:
    """A real, bootstrapped workspace with *token*'s page and a curated page
    under `references/` whose body cites `token` as a `[[wikilink]]`."""
    root = tmp_path / "works"
    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])
    assert result.exit_code == 0

    layout = maintenance.resolve_workspace(str(root))
    lane, slug = token.split("/", 1)
    target_dir = layout.bundle_dir / lane
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / f"{slug}.md").write_text("---\ntitle: Foo\ndescription: d\n---\n\n## Summary\nd\n", encoding="utf-8")

    references_dir = layout.bundle_dir / "references"
    references_dir.mkdir(parents=True, exist_ok=True)
    (references_dir / "citing.md").write_text(
        f"---\ntitle: Citing\ndescription: d\n---\n\n## Summary\nSee [[{token}]] for the rest.\n",
        encoding="utf-8",
    )
    return root


def test_wiki_archive_reports_stranded_wikilinks_in_both_modes(tmp_path: Path) -> None:
    """The lane the spec singles out: without `--dry-run` this command never
    prints the plan, so a plan-render-only fix would leave it silent."""
    workspace = _workspace_with_wikilink_into(tmp_path, "tutorials/foo")

    preview = runner.invoke(app, ["wiki", "archive", "tutorials/foo", "--dry-run", "--workspace", str(workspace)])
    assert preview.exit_code == 0
    assert "wiki pages: ! 1 inbound [[wikilink]]" in preview.stderr

    applied = runner.invoke(app, ["wiki", "archive", "tutorials/foo", "--workspace", str(workspace)])
    assert applied.exit_code == 0  # ADR-0004: broken links are warn, never error
    assert "wiki pages: ! 1 inbound [[wikilink]]" in applied.stderr


def _workspace_with_linked_source(tmp_path: Path) -> Path:
    root = tmp_path / "works"
    assert runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)]).exit_code == 0
    layout = maintenance.resolve_workspace(str(root))
    sources = layout.bundle_dir / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    sources.joinpath("one.md").write_text("---\ntitle: One\ndescription: d\n---\n\n## Summary\nd\n", encoding="utf-8")
    (layout.bundle_dir / "log.md").write_text("# Log\n\nSee [One](sources/one.md).\n", encoding="utf-8")
    return root


def test_wiki_archive_appends_its_log_entry(tmp_path: Path) -> None:
    workspace = _workspace_with_linked_source(tmp_path)

    result = runner.invoke(app, ["wiki", "archive", "sources/one", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "sources/one"
    log = (maintenance.resolve_workspace(str(workspace)).bundle_dir / "log.md").read_text(encoding="utf-8")
    assert "archived wiki sources/one" in log and "sources/_archive/one.md" in log


@pytest.mark.parametrize("dry_run", (True, False))
def test_wiki_archive_json_emits_the_combined_projection(tmp_path: Path, dry_run: bool) -> None:
    workspace = _workspace_with_linked_source(tmp_path)
    args = [
        "wiki",
        "archive",
        "sources/one",
        "--json",
        "--workspace",
        str(workspace),
        *(["--dry-run"] if dry_run else []),
    ]

    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["path_mapping"] == {} and doc["wiki"]["tokens"] == ["sources/one"]
    assert doc["wiki"]["archived"] == ([] if dry_run else ["sources/one"])
    assert doc["pointer_cleared"] is False
