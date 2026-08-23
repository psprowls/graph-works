"""The command layer: resolution, the exit-code contract, and the three
behaviors this surface deliberately changed from the module it ports.

Fixture graphs come from `conftest.py`, built through the real store and read
through the real reader — the spec's §7 residual risk is `graph_target`
reading a `workspace.yaml` no test wrote, so the resolution tests below
write real ones.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from code_graph_io import exit_codes, paths
from code_wiki_okf import ConfigError
from graph_works_core import GraphResult, GraphTarget, graph_target, layout_for
from graph_works_core.graph import commands as graph_cmd


def _layout_with_config(root: Path, body: str):
    """A layout whose `workspace.yaml` carries *body*'s `repositories`/etc. blocks."""
    layout = layout_for(root, repo_root=root)
    layout.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    layout.manifest_path.write_text("version: 1\n" + textwrap.dedent(body), encoding="utf-8")
    return layout


def test_graph_result_ok_tracks_the_exit_code():
    assert GraphResult(exit_codes.SUCCESS).ok
    assert not GraphResult(exit_codes.GENERIC, "", "error: no").ok
    assert GraphResult(exit_codes.SUCCESS).output == ""
    assert GraphResult(exit_codes.SUCCESS).error == ""


def test_graph_target_reads_the_bundle_config(tmp_path):
    (tmp_path / "repo-a").mkdir()
    (tmp_path / "repo-b").mkdir()
    layout = _layout_with_config(
        tmp_path,
        """
        repositories:
          alpha:
            path: ../repo-a
          beta:
            path: ../repo-b
        """,
    )
    target = graph_target(layout)
    assert target.graph_dir == layout.cache_dir
    assert target.member_names == ("alpha", "beta")
    assert target.members == ((tmp_path / "repo-a").resolve(), (tmp_path / "repo-b").resolve())


def test_graph_target_falls_back_when_there_is_no_config(tmp_path):
    """The bootstrap path `update.run` documents: the graph DB is created
    before any manifest may exist, so resolution must not require one."""
    layout = layout_for(tmp_path, repo_root=tmp_path)
    target = graph_target(layout)
    assert target.graph_dir == paths.graph_dir(layout.root)
    assert target.members == (layout.repo_root,)
    assert target.member_names == (layout.root.name,)


def test_graph_target_outside_any_repository_has_no_members(tmp_path):
    layout = layout_for(tmp_path)
    target = graph_target(layout)
    assert target.graph_dir == paths.graph_dir(layout.root)
    assert target.members == ()
    assert target.member_names == ()


def test_a_malformed_config_raises_rather_than_returning_a_result(tmp_path):
    """Configuration raises; content never does. Resolution failures are the
    caller's to handle — only graph state comes back as an exit code."""
    layout = _layout_with_config(tmp_path, "repositories: [not, a, mapping]\n")
    with pytest.raises(ConfigError):
        graph_target(layout)


@pytest.fixture
def calls(monkeypatch):
    """Capture `update.run_workspace` instead of running a real scan.

    A build walks git and parses source; nothing about the seam under test
    needs that, and the scan is what would make this suite slow and
    machine-dependent."""
    recorded: list[dict] = []

    def _fake(members, *, graph_dir, full=False, lock_timeout_ms=None, member_ignore=None):
        recorded.append(
            {"members": list(members), "graph_dir": graph_dir, "full": full, "member_ignore": member_ignore}
        )

    monkeypatch.setattr(graph_cmd.update, "run_workspace", _fake)
    return recorded


def _two_member_target(tmp_path):
    return GraphTarget(
        graph_dir=tmp_path / "graph",
        members=(tmp_path / "repo-a", tmp_path / "repo-b"),
        member_names=("alpha", "beta"),
    )


def test_build_drives_run_workspace_with_every_member(tmp_path, calls):
    target = _two_member_target(tmp_path)
    result = graph_cmd.build(target)
    assert result.ok
    assert result.output == ""
    assert calls == [
        {
            "members": list(target.members),
            "graph_dir": target.graph_dir,
            "full": False,
            "member_ignore": [(), ()],
        }
    ]


def test_build_threads_member_ignore_through(tmp_path, calls):
    target = GraphTarget(
        graph_dir=tmp_path / "graph",
        members=(tmp_path / "repo-a", tmp_path / "repo-b"),
        member_names=("alpha", "beta"),
        member_ignore=(("**/fixtures/**",), ("*.generated.py",)),
    )
    graph_cmd.build(target)
    assert calls[0]["member_ignore"] == [("**/fixtures/**",), ("*.generated.py",)]


def test_only_scopes_member_ignore_to_the_selected_member(tmp_path, calls):
    target = GraphTarget(
        graph_dir=tmp_path / "graph",
        members=(tmp_path / "repo-a", tmp_path / "repo-b"),
        member_names=("alpha", "beta"),
        member_ignore=(("alpha-ignore/**",), ("beta-ignore/**",)),
    )
    graph_cmd.build(target, only="beta")
    assert calls[0]["member_ignore"] == [("beta-ignore/**",)]


def test_graph_target_reads_repo_ignore_into_member_ignore(tmp_path):
    (tmp_path / "repo-a").mkdir()
    (tmp_path / "repo-b").mkdir()
    layout = _layout_with_config(
        tmp_path,
        """
        graph_dir: ../.gw/cache/graph
        ignore:
          - "**/global-skip/**"
        repositories:
          alpha:
            path: ../repo-a
            ignore:
              - "**/alpha-only/**"
          beta:
            path: ../repo-b
        """,
    )
    target = graph_target(layout)
    assert target.member_ignore == (("**/global-skip/**", "**/alpha-only/**"), ("**/global-skip/**",))


def test_graph_target_bootstrap_path_has_an_empty_pattern_tuple(tmp_path):
    layout = layout_for(tmp_path, repo_root=tmp_path)
    target = graph_target(layout)
    assert target.member_ignore == ((),)


def test_ignore_pattern_reaches_the_built_graph(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.1.1"\n')
    (repo / "src").mkdir()
    (repo / "src" / "keep.py").write_text("def keep_me():\n    return 1\n")
    (repo / "generated").mkdir()
    (repo / "generated" / "auto.py").write_text("def skip_me():\n    return 2\n")

    def _git(args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    _git(["init", "-q"])
    _git(["add", "-A"])
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"])

    layout = _layout_with_config(
        tmp_path,
        f"""
        graph_dir: ../.gw/cache/graph
        repositories:
          demo:
            path: {repo}
            ignore:
              - "generated/**"
        """,
    )
    target = graph_target(layout)
    result = graph_cmd.build(target, full=True)
    assert result.ok

    from code_graph_io import open_reader

    reader = open_reader(graph_dir=target.graph_dir)
    try:
        names = {n.name for n in reader.find(kind="function")}
    finally:
        reader.close()
    assert "keep_me" in names
    assert "skip_me" not in names


def test_build_passes_full_through(tmp_path, calls):
    graph_cmd.build(_two_member_target(tmp_path), full=True)
    assert calls[0]["full"] is True


def test_only_scopes_the_build_to_one_member(tmp_path, calls):
    target = _two_member_target(tmp_path)
    assert graph_cmd.build(target, only="beta").ok
    assert calls[0]["members"] == [tmp_path / "repo-b"]


def test_an_unknown_only_key_is_generic_and_names_the_declared_members(tmp_path, calls):
    result = graph_cmd.build(_two_member_target(tmp_path), only="gamma")
    assert result.exit_code == exit_codes.GENERIC
    assert "gamma" in result.error
    assert "alpha, beta" in result.error
    assert calls == []


def test_a_target_with_no_members_is_not_in_git_repo(tmp_path, calls):
    result = graph_cmd.build(GraphTarget(graph_dir=tmp_path / "graph"))
    assert result.exit_code == exit_codes.NOT_IN_GIT_REPO
    assert result.error.startswith("error: ")
    assert calls == []


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (lambda: graph_cmd.update.NotInGitRepoError("not a repo"), exit_codes.NOT_IN_GIT_REPO),
        (lambda: graph_cmd.update.UpdateInProgressError("locked"), exit_codes.UPDATE_IN_PROGRESS),
        (lambda: graph_cmd.SchemaMismatchError(found="1", expected=3), exit_codes.SCHEMA_MISMATCH),
        (lambda: RuntimeError("something else entirely"), exit_codes.GENERIC),
    ],
)
def test_build_maps_every_documented_failure_onto_its_exit_code(tmp_path, monkeypatch, raised, expected):
    def _boom(members, *, graph_dir, full=False, lock_timeout_ms=None, member_ignore=None):
        raise raised()

    monkeypatch.setattr(graph_cmd.update, "run_workspace", _boom)
    result = graph_cmd.build(_two_member_target(tmp_path))
    assert result.exit_code == expected
    assert result.error.startswith("error: ")
    assert result.output == ""


@pytest.mark.parametrize(
    "call",
    [
        lambda t: graph_cmd.describe(t, kind="package", identifier="widgets"),
        lambda t: graph_cmd.find(t, kind="function"),
        lambda t: graph_cmd.export(t),
    ],
)
def test_a_missing_graph_is_not_initialized_on_every_reading_command(empty_graph_dir, call):
    result = call(GraphTarget(graph_dir=empty_graph_dir))
    assert result.exit_code == exit_codes.NOT_INITIALIZED
    assert result.error.startswith("error: ")
    assert result.output == ""


@pytest.mark.parametrize(
    "call",
    [
        lambda t: graph_cmd.describe(t, kind="package", identifier="widgets"),
        lambda t: graph_cmd.find(t, kind="function"),
        lambda t: graph_cmd.export(t),
    ],
)
def test_a_stale_schema_is_schema_mismatch_on_every_reading_command(stale_graph_dir, call):
    result = call(GraphTarget(graph_dir=stale_graph_dir))
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert "schema version mismatch" in result.error


def test_describe_carries_the_spine_result_through(graph_dir):
    target = GraphTarget(graph_dir=graph_dir)
    ok = graph_cmd.describe(target, kind="package", identifier="widgets")
    assert ok.ok
    assert "package widgets" in ok.output

    missing = graph_cmd.describe(target, kind="package", identifier="absent")
    assert missing.exit_code == exit_codes.GENERIC
    assert missing.error == "error: package not found: absent"

    ambiguous = graph_cmd.describe(target, kind="entry_point", identifier="shared")
    assert ambiguous.exit_code == exit_codes.AMBIGUOUS


def test_describe_repository_needs_no_identifier(graph_dir):
    result = graph_cmd.describe(GraphTarget(graph_dir=graph_dir), kind="repository", identifier=None)
    assert result.ok
    assert "repository demo" in result.output


def test_describe_passes_depth_down(graph_dir):
    result = graph_cmd.describe(GraphTarget(graph_dir=graph_dir), kind="package", identifier="widgets", depth=2)
    assert "children (depth 2)" in result.output


def test_find_renders_rows(graph_dir):
    result = graph_cmd.find(GraphTarget(graph_dir=graph_dir), kind="function")
    assert result.ok
    assert "alpha" in result.output


def test_zero_rows_is_success_for_every_filter(graph_dir):
    """§4.6 departure 1. The module this ports from returned GENERIC(1) when
    `--in-package` matched nothing while `--name`/`--kind` returned SUCCESS —
    a quirk it documents as traceable to one source line of the pre-typed CLI
    (`q_find.py:66-68`), not to a contract anyone chose."""
    target = GraphTarget(graph_dir=graph_dir)
    for result in (
        graph_cmd.find(target, in_package="no-such-package"),
        graph_cmd.find(target, name="no-such-name"),
        graph_cmd.find(target, kind="class"),
    ):
        assert result.exit_code == exit_codes.SUCCESS
        assert result.output == ""


def test_find_rejects_filters_the_reader_rejects(graph_dir):
    target = GraphTarget(graph_dir=graph_dir)
    assert graph_cmd.find(target).exit_code == exit_codes.GENERIC
    assert graph_cmd.find(target, kind="galaxy").exit_code == exit_codes.GENERIC


def test_the_truncation_notice_lands_in_output_and_never_in_error(graph_dir):
    """`error` is non-empty only on failure, so a fired row cap says so in
    `output` -- where `render` already writes it -- and nowhere else. Capturing
    it into `error` as well printed it twice for any consumer routing the two
    fields to stdout and stderr."""
    import sqlite3

    conn = sqlite3.connect(graph_dir / "code.db")
    try:
        with conn:
            conn.executemany(
                "INSERT INTO nodes (kind, name, path, line) VALUES ('function', ?, ?, ?)",
                [(f"fn_{i:03d}", "packages/widgets/src/b.py", i) for i in range(60)],
            )
    finally:
        conn.close()

    result = graph_cmd.find(GraphTarget(graph_dir=graph_dir), kind="function")
    assert result.ok
    assert result.error == ""
    lines = result.output.splitlines()
    assert len(lines) == 51
    assert lines[-1] == "... showing 50 of 64 (truncated)"


def test_export_with_no_out_returns_the_graphml(graph_dir):
    """§4.6 departure 2. The module this ports from spelled stdout as
    `out_path == Path("-")`; `-` is a CLI convention and a library has no
    stdout to name, so E7 maps `--out -` onto None."""
    result = graph_cmd.export(GraphTarget(graph_dir=graph_dir))
    assert result.ok
    assert result.output.startswith("<?xml")
    assert "graphml" in result.output
    assert result.error == ""


def test_export_to_a_path_writes_it_and_summarizes(graph_dir, tmp_path):
    out = tmp_path / "nested" / "graph.graphml"
    result = graph_cmd.export(GraphTarget(graph_dir=graph_dir), out=out)
    assert result.ok
    assert out.read_text(encoding="utf-8").startswith("<?xml")
    assert result.output.startswith("wrote ")
    assert str(out) in result.output


def test_every_reader_is_closed(graph_dir):
    """No fd leak and no lock left behind: a second command against the same
    graph after a failing one still succeeds."""
    target = GraphTarget(graph_dir=graph_dir)
    for _ in range(3):
        assert graph_cmd.describe(target, kind="package", identifier="absent").exit_code == exit_codes.GENERIC
        assert graph_cmd.find(target, kind="galaxy").exit_code == exit_codes.GENERIC
    assert graph_cmd.describe(target, kind="package", identifier="widgets").ok


def test_fmt_json_passes_through_describe(graph_dir):
    result = graph_cmd.describe(GraphTarget(graph_dir=graph_dir), kind="package", identifier="widgets", fmt="json")
    assert result.ok
    assert json.loads(result.output)["kind"] == "package"


def test_fmt_json_passes_through_find(graph_dir):
    result = graph_cmd.find(GraphTarget(graph_dir=graph_dir), kind="function", fmt="json")
    assert result.ok
    rows = json.loads(result.output)
    assert any(r["name"] == "alpha" for r in rows)


def test_describe_kind_none_infers_at_the_command_layer(graph_dir):
    target = GraphTarget(graph_dir=graph_dir)
    explicit = graph_cmd.describe(target, kind="package", identifier="widgets")
    inferred = graph_cmd.describe(target, identifier="widgets")
    assert inferred.ok
    assert inferred.output == explicit.output


def test_describe_with_no_kind_and_no_identifier_is_repository(graph_dir):
    """When both kind and identifier are omitted, describe defaults to
    repository and no identifier is required."""
    result = graph_cmd.describe(GraphTarget(graph_dir=graph_dir))
    assert result.ok
    assert "repository demo" in result.output
