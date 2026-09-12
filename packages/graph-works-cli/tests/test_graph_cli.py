"""`gw graph` — the four verbs, their shared helpers, and the D-026 error prefix.

Two strategies, deliberately: the per-verb tests mock `graph_cli.main`'s bound
`core_*` names and assert *routing* (which core function, which kwargs, which
stream, which exit code) — routing is this module's whole job. The end-to-end
tests at the bottom run the same verbs against a real seeded graph, proving the
wrapper composes with the landed core rather than only with a mock's signature.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from code_graph_io import upsert
from code_graph_io.records import GraphEdge, GraphNode, GraphRecords
from code_graph_io.testing import raw_conn
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_cli.graph_cli import main as graph_main
from graph_works_core.graph.commands import GraphResult, GraphTarget
from graph_works_core.workspace.errors import WorkspaceConfigError
from typer.testing import CliRunner

runner = CliRunner()

TARGET = GraphTarget(graph_dir=Path("/fake/graph"))


@pytest.fixture
def spy(monkeypatch):
    """Replace one bound `core_*` name with a recorder returning `result`."""
    calls: list[dict[str, object]] = []

    def _install(verb: str, result: GraphResult) -> list[dict[str, object]]:
        def _fake(target, **kwargs):
            calls.append({"target": target, **kwargs})
            return result

        monkeypatch.setattr(graph_main, f"core_{verb}", _fake)
        monkeypatch.setattr(graph_main, "_resolve_target", lambda workspace: TARGET)
        return calls

    return _install


# ------------------------------------------------------------------ _emit / _run


def test_emit_routes_output_to_stdout_and_error_to_stderr(capsys) -> None:
    graph_main._emit(GraphResult(exit_codes.GENERIC, "the answer", "error: the problem"))

    captured = capsys.readouterr()
    assert captured.out == "the answer\n"
    assert captured.err == "Error: the problem\n"


def test_emit_prints_nothing_for_empty_strings(capsys) -> None:
    graph_main._emit(GraphResult(exit_codes.SUCCESS))

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_normalize_error_restyles_cores_lowercase_prefix() -> None:
    assert graph_main._normalize_error("error: no graph") == "Error: no graph"


def test_normalize_error_leaves_an_unprefixed_message_alone() -> None:
    assert graph_main._normalize_error("no graph") == "no graph"


def test_normalize_error_does_not_touch_a_later_occurrence() -> None:
    assert graph_main._normalize_error("error: parse error: line 3") == "Error: parse error: line 3"


def test_run_emits_then_exits_with_the_results_code(capsys) -> None:
    with pytest.raises(typer.Exit) as excinfo:
        graph_main._run(GraphResult(exit_codes.SCHEMA_MISMATCH, "", "error: stale"))

    assert excinfo.value.exit_code == exit_codes.SCHEMA_MISMATCH
    assert capsys.readouterr().err == "Error: stale\n"


# --------------------------------------------------------------- _resolve_target


def test_resolve_target_passes_through_the_graph_target(monkeypatch) -> None:
    monkeypatch.setattr(graph_main, "resolve_workspace", lambda workspace: "LAYOUT")
    monkeypatch.setattr(graph_main, "graph_target", lambda layout: TARGET)

    assert graph_main._resolve_target("") is TARGET


def test_resolve_target_maps_config_error_to_generic(monkeypatch, capsys) -> None:
    monkeypatch.setattr(graph_main, "resolve_workspace", lambda workspace: "LAYOUT")

    def _boom(layout):
        raise WorkspaceConfigError("workspace.yaml: not valid YAML")

    monkeypatch.setattr(graph_main, "graph_target", _boom)

    with pytest.raises(typer.Exit) as excinfo:
        graph_main._resolve_target("")

    assert excinfo.value.exit_code == exit_codes.GENERIC
    assert capsys.readouterr().err == "Error: workspace.yaml: not valid YAML\n"


def test_resolve_target_lets_workspace_not_found_exit_not_initialized(monkeypatch, capsys) -> None:
    def _exit(workspace):
        typer.echo("Error: no workspace here", err=True)
        raise typer.Exit(code=exit_codes.NOT_INITIALIZED)

    monkeypatch.setattr(graph_main, "resolve_workspace", _exit)

    with pytest.raises(typer.Exit) as excinfo:
        graph_main._resolve_target("")

    assert excinfo.value.exit_code == exit_codes.NOT_INITIALIZED
    assert capsys.readouterr().err == "Error: no workspace here\n"


# ------------------------------------------------------------------------ build


def test_build_routes_flags_and_exits_success(spy) -> None:
    calls = spy("build", GraphResult(exit_codes.SUCCESS))

    result = runner.invoke(app, ["graph", "build", "--full", "--only", "widgets"])

    assert result.exit_code == exit_codes.SUCCESS
    assert calls == [{"target": TARGET, "full": True, "only": "widgets"}]
    assert result.stdout == ""


def test_build_maps_an_omitted_only_to_none(spy) -> None:
    calls = spy("build", GraphResult(exit_codes.SUCCESS))

    runner.invoke(app, ["graph", "build"])

    assert calls == [{"target": TARGET, "full": False, "only": None}]


def test_build_carries_a_core_failure_to_stderr_and_the_exit_code(spy) -> None:
    spy("build", GraphResult(exit_codes.UPDATE_IN_PROGRESS, "", "error: a build is already running"))

    result = runner.invoke(app, ["graph", "build"])

    assert result.exit_code == exit_codes.UPDATE_IN_PROGRESS
    assert result.stderr == "Error: a build is already running\n"
    assert result.stdout == ""


# --------------------------------------------------------------------- describe


def test_describe_routes_every_flag(spy) -> None:
    calls = spy("describe", GraphResult(exit_codes.SUCCESS, "package widgets"))

    result = runner.invoke(
        app,
        ["graph", "describe", "widgets", "--kind", "package", "--in-package", "core", "--depth", "2", "--json"],
    )

    assert result.exit_code == exit_codes.SUCCESS
    assert calls == [
        {
            "target": TARGET,
            "kind": "package",
            "identifier": "widgets",
            "depth": 2,
            "in_package": "core",
            "fmt": "json",
        }
    ]
    assert result.stdout == "package widgets\n"


def test_describe_leaves_kind_and_selector_none_for_inference(spy) -> None:
    calls = spy("describe", GraphResult(exit_codes.SUCCESS, "repository demo"))

    runner.invoke(app, ["graph", "describe"])

    assert calls == [
        {"target": TARGET, "kind": None, "identifier": None, "depth": None, "in_package": None, "fmt": "human"}
    ]


def test_describe_carries_a_core_failure_to_stderr(spy) -> None:
    spy("describe", GraphResult(exit_codes.AMBIGUOUS, "", "error: entry point not found: shared"))

    result = runner.invoke(app, ["graph", "describe", "shared"])

    assert result.exit_code == exit_codes.AMBIGUOUS
    assert result.stderr == "Error: entry point not found: shared\n"


# ------------------------------------------------------------------------- find


def test_find_refuses_an_all_none_filter_before_resolving_a_workspace(monkeypatch) -> None:
    def _never(workspace):
        raise AssertionError("the guard must run before workspace resolution")

    monkeypatch.setattr(graph_main, "_resolve_target", _never)

    result = runner.invoke(app, ["graph", "find"])

    assert result.exit_code == 2
    assert result.stderr == "Error: at least one of --name, --kind, --in-package required\n"


def test_find_routes_every_filter(spy) -> None:
    calls = spy("find", GraphResult(exit_codes.SUCCESS, "widgets"))

    result = runner.invoke(app, ["graph", "find", "--name", "alpha", "--kind", "function", "--in-package", "widgets"])

    assert result.exit_code == exit_codes.SUCCESS
    assert calls == [{"target": TARGET, "name": "alpha", "kind": "function", "in_package": "widgets", "fmt": "human"}]


def test_find_json_flag_selects_the_json_formatter(spy) -> None:
    calls = spy("find", GraphResult(exit_codes.SUCCESS, "[]"))

    runner.invoke(app, ["graph", "find", "--kind", "package", "--json"])

    assert calls == [{"target": TARGET, "name": None, "kind": "package", "in_package": None, "fmt": "json"}]


def test_find_carries_a_core_failure_to_stderr(spy) -> None:
    spy("find", GraphResult(exit_codes.GENERIC, "", "error: unknown kind: widget"))

    result = runner.invoke(app, ["graph", "find", "--kind", "widget"])

    assert result.exit_code == exit_codes.GENERIC
    assert result.stderr == "Error: unknown kind: widget\n"


# ----------------------------------------------------------------------- export


@pytest.mark.parametrize("args", ([], ["--out", "-"]))
def test_export_maps_stdout_conventions_to_none(spy, args) -> None:
    calls = spy("export", GraphResult(exit_codes.SUCCESS, "<graphml/>"))

    result = runner.invoke(app, ["graph", "export", *args])

    assert result.exit_code == exit_codes.SUCCESS
    assert calls == [{"target": TARGET, "out": None}]
    assert result.stdout == "<graphml/>\n"


def test_export_passes_a_real_path_through(spy, tmp_path) -> None:
    calls = spy("export", GraphResult(exit_codes.SUCCESS, "wrote 3 nodes, 2 edges → out.graphml"))
    destination = tmp_path / "nested" / "out.graphml"

    runner.invoke(app, ["graph", "export", "--out", str(destination)])

    assert calls == [{"target": TARGET, "out": destination}]


def test_export_carries_a_core_failure_to_stderr(spy) -> None:
    spy("export", GraphResult(exit_codes.NOT_INITIALIZED, "", "error: no graph at /fake/graph"))

    result = runner.invoke(app, ["graph", "export"])

    assert result.exit_code == exit_codes.NOT_INITIALIZED
    assert result.stderr == "Error: no graph at /fake/graph\n"


# -------------------------------------------------------------------- end-to-end
#
# No mock below this line: a real bootstrapped workspace with a real seeded
# `code.db`, driven through the real `graph.commands`. Spec §5 requires this —
# the sibling `graph-works-core` child (782e50ac) landed `fmt`, `kind=None`
# inference and symbol describe, so the composed behavior is exercisable here.


@pytest.fixture
def seeded_workspace(tmp_path: Path) -> Path:
    """A bootstrapped workspace whose `.gw/cache/code.db` holds a tiny real graph.

    `graph_target()` calls `load_workspace_config(layout)`, so the
    graph the CLI opens is always `layout.cache_dir` (`<root>/.gw/cache` by
    default) — supplied by the caller, not read from the document.
    """
    root = tmp_path / "works"
    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])
    assert result.exit_code == exit_codes.SUCCESS

    graph_dir = root / ".gw" / "cache"
    graph_dir.mkdir(parents=True, exist_ok=True)
    conn = raw_conn(graph_dir / "code.db", create=True)
    try:
        upsert.upsert_records(
            conn,
            GraphRecords(
                nodes=[
                    GraphNode(
                        kind="package",
                        name="widgets",
                        path="packages/widgets/pyproject.toml",
                        line=None,
                        attrs={"language": "python"},
                    ),
                    GraphNode(
                        kind="file",
                        name="a.py",
                        path="packages/widgets/src/a.py",
                        line=None,
                        attrs={"language": "python"},
                    ),
                    GraphNode(kind="function", name="alpha", path="packages/widgets/src/a.py", line=10, attrs={}),
                    GraphNode(kind="function", name="beta", path="packages/widgets/src/a.py", line=20, attrs={}),
                    GraphNode(kind="class", name="beta", path="packages/widgets/src/a.py", line=40, attrs={}),
                ],
                edges=[
                    GraphEdge(
                        src=("package", "widgets", "packages/widgets/pyproject.toml"),
                        dst=("file", "a.py", "packages/widgets/src/a.py"),
                        kind="contains",
                        attrs={},
                    ),
                ],
            ),
        )
    finally:
        conn.close()
    return root


def test_end_to_end_describe_infers_the_kind_of_a_unique_selector(seeded_workspace: Path) -> None:
    result = runner.invoke(app, ["graph", "describe", "alpha", "--workspace", str(seeded_workspace)])

    assert result.exit_code == exit_codes.SUCCESS
    assert result.stdout.startswith("function alpha\n")
    assert "packages/widgets/src/a.py:10" in result.stdout


def test_end_to_end_describe_json_emits_a_parseable_object(seeded_workspace: Path) -> None:
    result = runner.invoke(app, ["graph", "describe", "alpha", "--json", "--workspace", str(seeded_workspace)])

    assert result.exit_code == exit_codes.SUCCESS
    payload = json.loads(result.stdout)
    assert payload["kind"] == "function"
    assert payload["name"] == "alpha"


def test_end_to_end_describe_a_code_symbol_by_explicit_kind(seeded_workspace: Path) -> None:
    result = runner.invoke(app, ["graph", "describe", "beta", "--kind", "class", "--workspace", str(seeded_workspace)])

    assert result.exit_code == exit_codes.SUCCESS
    assert result.stdout.startswith("class beta\n")


def test_end_to_end_an_ambiguous_selector_renders_a_menu_and_succeeds(seeded_workspace: Path) -> None:
    """More than one match is a question with several answers, not a failure."""
    result = runner.invoke(app, ["graph", "describe", "beta", "--workspace", str(seeded_workspace)])

    assert result.exit_code == exit_codes.SUCCESS
    assert result.stderr == ""
    assert "gw graph describe beta --kind function --in-package widgets" in result.stdout
    assert "gw graph describe beta --kind class --in-package widgets" in result.stdout


def test_end_to_end_find_json_lists_matching_nodes(seeded_workspace: Path) -> None:
    result = runner.invoke(app, ["graph", "find", "--kind", "function", "--json", "--workspace", str(seeded_workspace)])

    assert result.exit_code == exit_codes.SUCCESS
    assert {row["name"] for row in json.loads(result.stdout)} == {"alpha", "beta"}


def test_end_to_end_an_unmatched_selector_falls_back_to_path_and_normalizes_the_prefix(
    seeded_workspace: Path,
) -> None:
    """D-026 end-to-end: core wrote `error: …`, the user sees `Error: …`."""
    result = runner.invoke(app, ["graph", "describe", "nothing-matches-this", "--workspace", str(seeded_workspace)])

    assert result.exit_code == exit_codes.GENERIC
    assert result.stderr == "Error: path not found in graph: nothing-matches-this\n"


def test_end_to_end_export_writes_graphml_to_a_file(seeded_workspace: Path, tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "graph.graphml"

    result = runner.invoke(app, ["graph", "export", "--out", str(destination), "--workspace", str(seeded_workspace)])

    assert result.exit_code == exit_codes.SUCCESS
    assert destination.read_text(encoding="utf-8").startswith("<?xml")


@pytest.fixture
def path_less_workspace(tmp_path: Path) -> Path:
    """A workspace whose graph holds three path-less kinds under one selector.

    `dependency`, `test_suite` and `builtin` are the kinds whose menu commands
    `build_menu` used to synthesize against a CLI shape that no longer exists
    (`--ecosystem`, `--kind suite`, a bare builtin name).
    """
    root = tmp_path / "works"
    assert runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)]).exit_code == 0

    graph_dir = root / ".gw" / "cache"
    graph_dir.mkdir(parents=True, exist_ok=True)
    conn = raw_conn(graph_dir / "code.db", create=True)
    try:
        upsert.upsert_records(
            conn,
            GraphRecords(
                nodes=[
                    GraphNode(
                        kind="dependency",
                        name="shared",
                        path="dependency:pypi/shared",
                        line=None,
                        attrs={"ecosystem": "pypi", "versions_in_use": ["1.0.0"]},
                    ),
                    GraphNode(
                        kind="test_suite",
                        name="shared",
                        path="tests",
                        line=None,
                        attrs={"suite_kind": "unit", "path": "tests", "owner_kind": "repository"},
                    ),
                    GraphNode(
                        kind="builtin",
                        name="shared",
                        path="python",
                        line=None,
                        attrs={"language": "python", "module_name": "shared"},
                    ),
                ],
                edges=[],
            ),
        )
    finally:
        conn.close()
    return root


def test_end_to_end_every_synthesized_menu_command_round_trips(path_less_workspace: Path) -> None:
    """A disambiguation menu that hands the user a command `describe` rejects is
    a defect. Re-run each synthesized command and require SUCCESS from all of them."""
    menu = runner.invoke(app, ["graph", "describe", "shared", "--workspace", str(path_less_workspace)])
    assert menu.exit_code == exit_codes.SUCCESS

    commands = [line.split("→", 1)[1].strip() for line in menu.stdout.splitlines() if "→" in line]
    assert len(commands) == 3

    for command in commands:
        argv = command.removeprefix("gw ").split()
        replay = runner.invoke(app, [*argv, "--workspace", str(path_less_workspace)])
        assert replay.exit_code == exit_codes.SUCCESS, f"{command!r} -> {replay.stderr!r}"
