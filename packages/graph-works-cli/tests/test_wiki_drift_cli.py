"""`gw wiki drift` — the claude_code brief and the full judge+propose pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from code_graph_io.testing import raw_conn
from code_wiki_okf.config import ConfigError
from graph_works_cli.cli import app
from graph_works_cli.wiki_cli import drift as drift_module
from graph_works_core.lint_drift.propagate_drift import Candidate, DriftBrief, PropagateResult, Target
from graph_works_core.workspace.errors import WorkspaceError
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def initialized_workspace(tmp_path: Path) -> Path:
    """A bootstrapped workspace with a real, empty `code.db`.

    `drift`'s command body opens the code graph unconditionally (before the
    `claude_code`/bedrock branch), the same as `gw graph`'s commands do — see
    `test_graph_cli.py`'s `seeded_workspace`. A bare `bootstrap` never creates
    `.gw/cache/code.db`, so `open_reader` would raise `GraphNotInitializedError`
    without this. The tests here monkeypatch `plan_drift_brief` /
    `run_propagate_drift` themselves, so the graph's *contents* never matter —
    only that a valid, empty store exists to open.
    """
    root = tmp_path / "works"
    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])
    assert result.exit_code == 0
    graph_dir = root / ".gw" / "cache"
    graph_dir.mkdir(parents=True, exist_ok=True)
    raw_conn(graph_dir / "code.db", create=True).close()
    return root


def test_drift_claude_code_backend_returns_a_brief_and_calls_no_judge(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    candidate = Candidate(
        concept_id="packages/foo",
        resource="package:foo",
        title="foo",
        narrative="It does a thing.",
        last_updated_commit="abc1234",
        anchor=None,
        changed_files=("a.py",),
    )
    target = Target(concept_id="concepts/bar", title="bar", body="…", kind="concept", candidates=(candidate,))
    brief = DriftBrief(targets=(target,))

    def fake_plan_drift_brief(layout, config, reader, *, repo_root):
        return brief

    def fail_run_propagate_drift(*args: object, **kwargs: object) -> object:
        raise AssertionError("run_propagate_drift must not run under the claude_code default")

    monkeypatch.setattr(drift_module, "plan_drift_brief", fake_plan_drift_brief)
    monkeypatch.setattr(drift_module, "run_propagate_drift", fail_run_propagate_drift)

    result = runner.invoke(
        app, ["wiki", "drift", "--backend", "claude_code", "--json", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["targets"][0]["concept_id"] == "concepts/bar"
    assert payload["targets"][0]["candidates"][0]["concept_id"] == "packages/foo"


def test_drift_backend_bedrock_runs_the_full_pipeline(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    result_obj = PropagateResult(entities_considered=1, pages_judged=1, dry_run=True)

    async def fake_run_propagate_drift(*args: object, **kwargs: object) -> PropagateResult:
        return result_obj

    monkeypatch.setattr(drift_module, "run_propagate_drift", fake_run_propagate_drift)

    result = runner.invoke(
        app, ["wiki", "drift", "--backend", "bedrock", "--json", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["pages_judged"] == 1


def test_drift_claude_code_text_output_lists_targets_and_candidate_counts(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """Non-JSON claude_code output names each target and its changed-entity count."""
    candidate = Candidate(
        concept_id="packages/foo",
        resource="package:foo",
        title="foo",
        narrative="It does a thing.",
        last_updated_commit="abc1234",
        anchor=None,
        changed_files=("a.py",),
    )
    one_candidate = Target(concept_id="concepts/bar", title="bar", body="…", kind="concept", candidates=(candidate,))
    two_candidates = Target(
        concept_id="concepts/baz", title="baz", body="…", kind="concept", candidates=(candidate, candidate)
    )
    brief = DriftBrief(targets=(one_candidate, two_candidates))

    monkeypatch.setattr(drift_module, "plan_drift_brief", lambda *args, **kwargs: brief)

    result = runner.invoke(
        app, ["wiki", "drift", "--backend", "claude_code", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 0, result.stdout
    assert result.stdout == "concepts/bar (1 changed entity)\nconcepts/baz (2 changed entities)\n"


def test_drift_bedrock_text_output_and_warnings(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    """Non-JSON bedrock output summarizes counts and surfaces run errors as stderr warnings."""
    result_obj = PropagateResult(entities_considered=2, pages_judged=1, pages_stale=1, errors=("boom",), dry_run=True)

    async def fake_run_propagate_drift(*args: object, **kwargs: object) -> PropagateResult:
        return result_obj

    monkeypatch.setattr(drift_module, "run_propagate_drift", fake_run_propagate_drift)

    result = runner.invoke(app, ["wiki", "drift", "--backend", "bedrock", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0, result.stdout
    assert result.stdout == "Considered 2, judged 1, stale 1\n"
    assert "Warning: boom" in result.stderr


def test_drift_reports_an_unknown_backend_via_role_spec(initialized_workspace: Path) -> None:
    """A bad `--backend` value must fail through `role_spec`'s `WorkspaceError`."""
    result = runner.invoke(app, ["wiki", "drift", "--backend", "bogus", "--workspace", str(initialized_workspace)])

    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "drift_propagator" in combined
    assert "bogus" in combined


def test_drift_reports_unreadable_configuration_before_opening_the_graph(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A config fault must surface as a clean diagnostic, not a lint/graph error."""

    def fail(*args: object, **kwargs: object) -> object:
        raise ConfigError("config.yaml is malformed")

    monkeypatch.setattr(drift_module, "load_config", fail)

    result = runner.invoke(app, ["wiki", "drift", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "config.yaml is malformed" in result.stderr


def test_drift_reports_an_uninitialized_graph_without_a_traceback(tmp_path: Path) -> None:
    """A workspace that has never been scanned must fail with one clean diagnostic."""
    root = tmp_path / "works"
    assert runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)]).exit_code == 0

    result = runner.invoke(app, ["wiki", "drift", "--workspace", str(root)])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "cannot open the code graph" in result.stderr
    assert "Traceback" not in result.stderr


def test_drift_reports_an_ambiguous_repo_resolution_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A multi-repo workspace with no chosen repo must fail with one clean diagnostic.

    `resolve_repo` was previously called bare -- an ambiguous workspace or a
    malformed manifest raised `WorkspaceError` straight through the command as
    a traceback instead of the usual `Error: ...` exit.
    """

    def fail(*args: object, **kwargs: object) -> object:
        raise WorkspaceError("3 repositories declared; pass repo_name= to choose one")

    monkeypatch.setattr(drift_module, "resolve_repo", fail)

    result = runner.invoke(app, ["wiki", "drift", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "3 repositories declared" in result.stderr
    assert "Traceback" not in result.stderr


def test_drift_reports_brief_planning_failures_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A `plan_drift_brief` failure under the default `claude_code` backend must be
    one clean diagnostic, matching the bedrock/vercel branch's guard around
    `run_propagate_drift`.
    """

    def fail(*args: object, **kwargs: object) -> object:
        raise OSError("could not read anchors")

    monkeypatch.setattr(drift_module, "plan_drift_brief", fail)

    result = runner.invoke(app, ["wiki", "drift", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Error: could not read anchors\n"
    assert "Traceback" not in result.stderr


def test_drift_only_is_rejected_under_the_claude_code_default_backend(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """`--only` has no effect under `claude_code` -- `plan_drift_brief` takes no
    such argument, so silently returning the unfiltered brief would be worse
    than refusing. The default backend must error clearly instead.
    """

    def fail_plan_drift_brief(*args: object, **kwargs: object) -> object:
        raise AssertionError("plan_drift_brief must not be called when --only is rejected")

    monkeypatch.setattr(drift_module, "plan_drift_brief", fail_plan_drift_brief)

    result = runner.invoke(
        app, ["wiki", "drift", "--only", "concepts/foo", "--json", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "--only" in result.stderr
    assert "claude_code" in result.stderr


def test_drift_reports_pipeline_runtime_errors_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """An operational drift-propagation failure must not expose a traceback as command output."""

    async def fail_run_propagate_drift(*args: object, **kwargs: object) -> PropagateResult:
        raise ValueError("propagation backend unavailable")

    monkeypatch.setattr(drift_module, "run_propagate_drift", fail_run_propagate_drift)

    result = runner.invoke(app, ["wiki", "drift", "--backend", "bedrock", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "propagation backend unavailable" in result.stderr
    assert "Traceback" not in result.stderr
