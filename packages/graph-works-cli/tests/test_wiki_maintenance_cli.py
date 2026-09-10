"""`gw wiki` maintenance commands — stats, index, and archive."""

from __future__ import annotations

import json
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


@pytest.mark.parametrize(("target", "expected_tokens"), (("sources/one", ["sources/one"]), (None, None)))
def test_archive_uses_the_wide_bundle_lens_and_targeted_or_sweep_plan(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path, target: str | None, expected_tokens: list[str] | None
) -> None:
    """The narrow lens strands source reference companions during an archive."""
    bundle = object()
    loaded: list[tuple[object, object]] = []
    planned: list[tuple[object, object]] = []

    def fake_load_bundle(root: object, *, ignore: object) -> object:
        loaded.append((root, ignore))
        return bundle

    def fake_plan_archive(actual_bundle: object, tokens: object = None) -> SimpleNamespace:
        planned.append((actual_bundle, tokens))
        return SimpleNamespace(ok=True, diff=lambda: "archive plan", moves=SimpleNamespace(stranded=()))

    monkeypatch.setattr(maintenance, "load_bundle", fake_load_bundle)
    monkeypatch.setattr(maintenance, "plan_archive", fake_plan_archive)
    monkeypatch.setattr(maintenance, "apply_archive", lambda *_args: SimpleNamespace(ok=True, archived=()))

    args = ["wiki", "archive", "--workspace", str(initialized_workspace)]
    if target is not None:
        args.insert(2, target)
    result = runner.invoke(app, args)

    layout = maintenance.resolve_workspace(str(initialized_workspace))
    assert result.exit_code == 0
    assert loaded == [(layout.bundle_dir, maintenance.ARCHIVE_IGNORE)]
    assert planned == [(bundle, expected_tokens)]


def test_archive_dry_run_never_applies(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    """A preview that moves files would violate the sole preview-mode guarantee."""
    applied: list[object] = []
    monkeypatch.setattr(maintenance, "load_bundle", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        maintenance,
        "plan_archive",
        lambda *_args, **_kwargs: SimpleNamespace(ok=True, diff=lambda: "preview", moves=SimpleNamespace(stranded=())),
    )
    monkeypatch.setattr(maintenance, "apply_archive", applied.append)

    result = runner.invoke(
        app, ["wiki", "archive", "sources/one", "--dry-run", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 0
    assert result.stdout == "preview\n"
    assert applied == []


def test_archive_dry_run_prints_a_refused_plan_without_applying(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A refused preview must expose its plan while still preserving the bundle."""
    applied: list[object] = []
    monkeypatch.setattr(maintenance, "load_bundle", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        maintenance,
        "plan_archive",
        lambda *_args, **_kwargs: SimpleNamespace(
            ok=False, diff=lambda: "refused preview", moves=SimpleNamespace(stranded=())
        ),
    )
    monkeypatch.setattr(maintenance, "apply_archive", applied.append)

    result = runner.invoke(
        app, ["wiki", "archive", "sources/one", "--dry-run", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == "refused preview\n"
    assert "archive plan was refused" in result.stderr
    assert applied == []


@pytest.mark.parametrize(("plan_ok", "result_ok"), ((False, True), (True, False)))
def test_archive_refusals_and_incomplete_moves_fail(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path, plan_ok: bool, result_ok: bool
) -> None:
    """Refused plans and partial moves must never report a successful archive."""
    applied: list[object] = []
    plan = SimpleNamespace(ok=plan_ok, diff=lambda: "refused", moves=SimpleNamespace(stranded=()))
    monkeypatch.setattr(maintenance, "load_bundle", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(maintenance, "plan_archive", lambda *_args, **_kwargs: plan)

    def fake_apply_archive(*args: object) -> SimpleNamespace:
        applied.append(args)
        return SimpleNamespace(ok=result_ok)

    monkeypatch.setattr(maintenance, "apply_archive", fake_apply_archive)

    result = runner.invoke(app, ["wiki", "archive", "sources/one", "--workspace", str(initialized_workspace)])

    assert result.exit_code == exit_codes.GENERIC
    assert len(applied) == (0 if not plan_ok else 1)


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

    monkeypatch.setattr(maintenance, "load_bundle", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(maintenance, "plan_archive", fail)

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

    monkeypatch.setattr(maintenance, "load_bundle", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        maintenance,
        "plan_archive",
        lambda *_args, **_kwargs: SimpleNamespace(ok=True, moves=SimpleNamespace(stranded=())),
    )
    monkeypatch.setattr(maintenance, "apply_archive", fail)

    result = runner.invoke(app, ["wiki", "archive", "sources/one", "--workspace", str(initialized_workspace)])

    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == ""
    assert "Error: destination is not writable" in result.stderr


def test_archive_echoes_every_archived_token(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    """A silent archive gives no record of what moved."""
    monkeypatch.setattr(maintenance, "load_bundle", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        maintenance,
        "plan_archive",
        lambda *_args, **_kwargs: SimpleNamespace(ok=True, moves=SimpleNamespace(stranded=())),
    )
    monkeypatch.setattr(
        maintenance,
        "apply_archive",
        lambda *_args: SimpleNamespace(ok=True, archived=("sources/one", "sources/two")),
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
    assert "inbound [[wikilink]]" in preview.stderr

    applied = runner.invoke(app, ["wiki", "archive", "tutorials/foo", "--workspace", str(workspace)])
    assert applied.exit_code == 0  # ADR-0004: broken links are warn, never error
    assert "inbound [[wikilink]]" in applied.stderr
