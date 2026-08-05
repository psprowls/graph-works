"""Tests for the public code_graph_io.render module."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from code_graph_io import render


@dataclass(frozen=True)
class Row:
    kind: str
    name: str
    path: str
    line: int


# ── Public module existence ────────────────────────────────────────────────────


def test_render_module_has_all_format_functions() -> None:
    for name in [
        "format_package",
        "format_path",
        "format_repo",
        "format_entry_point",
        "format_suite",
        "format_app",
        "format_dependency",
        "format_builtin",
        "format_agent_plugin",
    ]:
        assert hasattr(render, name), f"render.{name} missing"


# ── render() works identically when called from render module directly ─────────


def test_render_json_via_public_module() -> None:
    rows = [Row("function", "foo", "a.py", 10)]
    out = render.render(rows, fmt="json")
    assert json.loads(out) == [{"kind": "function", "name": "foo", "path": "a.py", "line": 10}]


def test_render_human_via_public_module() -> None:
    rows = [Row("function", "foo", "a.py", 10)]
    out = render.render(rows, fmt="human")
    assert "function" in out
    assert "foo" in out


def test_render_invalid_format_via_public_module() -> None:
    with pytest.raises(ValueError):
        render.render([], fmt="xml")


# ── format_* output spot-checks (not byte-identical — that's in test_cli_describe.py) ──


def test_format_package_human_spine() -> None:
    from code_graph_io.queries import PackageDescription

    desc = PackageDescription(
        name="mypkg",
        language="python",
        version="1.0",
        files=["a.py", "b.py"],
        counts={"function": 2},
        entry_points=[],
        test_suites=[],
        internal_dependencies=["other"],
        internal_dependents=[],
    )
    out = render.format_package(desc, fmt="human")
    assert out.startswith("package mypkg\n  uri: pkg:mypkg")
    assert "  language: python" in out
    assert "  files:    2" in out
    assert "  counts:   2 functions" in out
    assert "  internal deps:" in out and "other" in out
    # internal dependents empty → omitted as a relationship line
    assert "internal dependents:" not in out
    assert "→ gw graph what-tests mypkg" in out
    assert "→ gw graph list-entry-points mypkg" in out


def test_format_package_json_spine() -> None:
    from code_graph_io.queries import PackageDescription

    desc = PackageDescription(
        name="mypkg",
        language="python",
        version="1.0",
        files=["a.py"],
        counts={"function": 1},
        entry_points=[],
        test_suites=[],
        internal_dependencies=["other"],
        internal_dependents=[],
    )
    parsed = json.loads(render.format_package(desc, fmt="json"))
    assert parsed["kind"] == "package"
    assert parsed["name"] == "mypkg"
    assert parsed["uri"] == "pkg:mypkg"
    assert parsed["attributes"]["files"] == 1
    assert parsed["attributes"]["counts"] == {"function": 1}
    assert parsed["relationships"]["internal_dependencies"] == ["other"]


def test_format_app_human_and_json() -> None:
    from code_graph_io.queries import AppDescription

    desc = AppDescription(
        name="my-cli",
        language="python",
        version="0.1",
        app_kind="cli",
        app_signals=["console_scripts"],
        files=["cli.py"],
        counts={"function": 3},
        entry_points=[],
        test_suites=[],
    )
    human = render.format_app(desc, fmt="human")
    assert human.startswith("app my-cli\n  uri: app:my-cli")
    assert "  app_kind: cli" in human
    assert "  signals:  console_scripts" in human
    parsed = json.loads(render.format_app(desc, fmt="json"))
    assert parsed["uri"] == "app:my-cli"
    assert parsed["attributes"]["app_kind"] == "cli"
    assert parsed["attributes"]["signals"] == ["console_scripts"]


def test_format_suite_spine() -> None:
    from code_graph_io.queries import SuiteDescription

    desc = SuiteDescription(name="mytest", uri="test://x", kind="pytest", file_count=3)
    out = render.format_suite(desc, fmt="human")
    assert out.startswith("test_suite mytest\n  uri: test://x")
    assert "  kind:  pytest" in out
    assert "  files: 3" in out


def test_format_repo_spine() -> None:
    from code_graph_io.queries import RepoDescription

    desc = RepoDescription(
        name="agent-research",
        uri="repo://agent-research",
        owner="pat",
        url="git@x",
        default_branch="develop",
        package_count=11,
    )
    human = render.format_repo(desc, fmt="human")
    assert human.startswith("repository agent-research\n  uri: repo://agent-research")
    assert "  owner:" in human and "pat" in human
    assert "package_count:" in human and "11" in human
    assert "→ gw graph list --kind package" in human
    assert "→ gw graph list --kind app" in human
    parsed = json.loads(render.format_repo(desc, fmt="json"))
    assert parsed["uri"] == "repo://agent-research"
    assert parsed["attributes"]["package_count"] == 11


def test_format_dependency_spine() -> None:
    from code_graph_io.queries import DependencyDescription

    desc = DependencyDescription(
        ecosystem="pypi", name="boto3", uri="dependency:pypi/boto3", versions_in_use=["1.38"], used_by=["demo"]
    )
    human = render.format_dependency(desc, fmt="human")
    assert human.startswith("dependency boto3\n  uri: dependency:pypi/boto3")
    assert "  ecosystem:       pypi" in human
    assert "  versions_in_use: 1.38" in human
    assert "  used_by:" in human and "demo" in human
    parsed = json.loads(render.format_dependency(desc, fmt="json"))
    assert parsed["attributes"]["versions_in_use"] == ["1.38"]
    assert parsed["relationships"]["used_by"] == ["demo"]


def test_format_builtin_spine() -> None:
    from code_graph_io.queries import BuiltinDescription

    desc = BuiltinDescription(language="python", module_name="pathlib", uri="builtin:python/pathlib", used_by=["demo"])
    human = render.format_builtin(desc, fmt="human")
    assert human.startswith("builtin pathlib\n  uri: builtin:python/pathlib")
    assert "  language:    python" in human
    assert "  module_name: pathlib" in human
    assert "  used_by:" in human
    parsed = json.loads(render.format_builtin(desc, fmt="json"))
    assert parsed["name"] == "pathlib"
    assert parsed["relationships"]["used_by"] == ["demo"]


def test_format_agent_plugin_spine() -> None:
    from code_graph_io.queries import AgentPluginDescription

    desc = AgentPluginDescription(
        name="agent-workspace",
        uri="agent_plugin:agent-workspace",
        ecosystem="claude-code",
        version="0.1.1",
        description="x",
        commands=[{"id": "a"}],
        agents=[],
        skills=[{"id": "s"}],
        scripts=[],
        hooks=[],
        mcp_servers=[],
    )
    human = render.format_agent_plugin(desc, fmt="human")
    assert human.startswith("agent_plugin agent-workspace\n  uri: agent_plugin:agent-workspace")
    assert "  ecosystem:   claude-code" in human
    assert "  commands:    1" in human
    assert "  skills:      1" in human
    assert "→ gw graph list --kind agent_plugin" in human
    parsed = json.loads(render.format_agent_plugin(desc, fmt="json"))
    assert parsed["attributes"]["commands"] == 1
    assert parsed["attributes"]["mcp_servers"] == 0


def test_format_entry_point_spine() -> None:
    from code_graph_io.queries import EntryPointDescription

    desc = EntryPointDescription(
        name="gw",
        uri="ep://gw",
        kind="console_script",
        callable="mod:main",
        implemented_by_path="src/mod.py",
        source="pyproject",
    )
    human = render.format_entry_point(desc, fmt="human")
    assert human.startswith("entry_point gw\n  uri: ep://gw")
    assert "  callable: mod:main" in human
    assert "  path:     src/mod.py" in human
    assert "→ gw graph describe src/mod.py" in human


def test_format_entry_point_no_nav_when_no_path() -> None:
    from code_graph_io.queries import EntryPointDescription

    desc = EntryPointDescription(
        name="gw", uri="ep://gw", kind="console_script", callable=None, implemented_by_path=None, source="pyproject"
    )
    human = render.format_entry_point(desc, fmt="human")
    assert "  callable: (none)" in human
    assert "→" not in human


# ============================================================================
# format_symbol / format_matches
# ============================================================================


def test_format_symbol_human_spine() -> None:
    from code_graph_io.queries import CallRecord, SymbolDescription

    desc = SymbolDescription(
        kind="function",
        name="process",
        path="foo/a.py",
        line=42,
        package="foo",
        exported_from="foo/__init__.py",
        token_count=99,
        callers=[CallRecord(name="run_scan", path="foo/a.py", line=1, depth=1)],
        callees=[CallRecord(name="validate", path="foo/a.py", line=80, depth=1)],
    )
    out = render.format_symbol(desc, "human")
    assert out.startswith("function process\n  path: foo/a.py:42")
    assert "  exported: yes (from foo/__init__.py)" in out
    assert "  tokens:" in out and "99" in out  # token_count attribute (Design decision 8)
    assert "  package:  foo" in out
    assert "  callers: run_scan" in out
    assert "  callees: validate" in out
    assert "→ gw graph callers process --depth 3" in out
    assert "→ gw graph callees process --depth 3" in out


def test_format_symbol_graceful_omissions_spine() -> None:
    from code_graph_io.queries import SymbolDescription

    desc = SymbolDescription(
        kind="class",
        name="Widget",
        path="foo/a.py",
        line=5,
        package=None,
        exported_from=None,
        callers=[],
        callees=[],
    )
    out = render.format_symbol(desc, "human")
    assert out.startswith("class Widget\n  path: foo/a.py:5")
    assert "  exported: no" in out
    assert "tokens:" not in out  # token_count None → attribute omitted (Design decision 8)
    assert "callers:" not in out and "callees:" not in out  # empty relationships omitted


def test_format_symbol_json_spine() -> None:
    from code_graph_io.queries import SymbolDescription

    desc = SymbolDescription(
        kind="type",
        name="Foo",
        path="a.ts",
        line=3,
        token_count=15,
        package="p",
        exported_from=None,
        callers=[],
        callees=[],
    )
    parsed = json.loads(render.format_symbol(desc, "json"))
    assert parsed["kind"] == "type" and parsed["name"] == "Foo"
    assert parsed["path"] == "a.ts:3"
    assert parsed["attributes"]["token_count"] == 15  # moved under attributes (Design decision 8)
    assert parsed["relationships"] == {}


def test_format_matches_human_and_json() -> None:
    import json

    from code_graph_io.queries import MatchRecord

    rows = [
        MatchRecord(
            kind="function", address="foo/a.py:10", command="gw graph describe run --kind function --in-package foo"
        ),
        MatchRecord(kind="class", address="foo/a.py:20", command="gw graph describe run --kind class --in-package foo"),
    ]
    human = render.format_matches(rows, "human")
    assert "function" in human and "foo/a.py:10" in human
    assert "gw graph describe run --kind class --in-package foo" in human
    parsed = json.loads(render.format_matches(rows, "json"))
    assert [r["kind"] for r in parsed] == ["function", "class"]


def test_format_path_spine() -> None:
    from code_graph_io.queries import ExportRecord, NodeRecord, PathDescription

    desc = PathDescription(
        path="foo/a.py",
        children=[NodeRecord(kind="function", name="alpha", path="foo/a.py", line=10, attrs={"token_count": 7})],
        imports=[NodeRecord(kind="file", name="b.py", path="foo/b.py", line=None, attrs={})],
        role_flags={"is_test": False, "is_init": True},
        token_count=20,
        exports=[ExportRecord(name="alpha", kind="function", line=10)],
    )
    human = render.format_path(desc, "human")
    assert human.startswith("file foo/a.py\n  path: foo/a.py")
    assert "  role_flags: is_init" in human
    assert "  tokens:" in human and "20" in human  # file token_count attribute (Design decision 8)
    assert "  children: function alpha (line 10)" in human
    assert "(7 tokens)" in human  # per-child token suffix preserved on the children relationship
    assert "  imports:  b.py" in human
    assert "  exports:  function alpha (line 10)" in human  # exports carry no attrs → no token suffix
    assert "→ gw graph imported-by foo/a.py" in human
    parsed = json.loads(render.format_path(desc, "json"))
    assert parsed["path"] == "foo/a.py"
    assert parsed["attributes"]["token_count"] == 20
    assert parsed["relationships"]["exports"] == ["function alpha (line 10)"]
    assert parsed["attributes"]["role_flags"] == {"is_test": False, "is_init": True}


# ── describe_block spine builder ────────────────────────────────────


def test_pluralize_rules() -> None:
    assert render._pluralize("function", 2) == "functions"
    assert render._pluralize("class", 4) == "classes"
    assert render._pluralize("method", 7) == "methods"
    assert render._pluralize("type", 3) == "types"
    assert render._pluralize("function", 1) == "function"  # singular at n==1


def test_counts_human_breakdown() -> None:
    out = render._counts_human({"function": 12, "class": 4, "method": 7})
    assert out == "12 functions · 4 classes · 7 methods"
    assert render._counts_human({}) == "(none)"


def test_describe_block_human_full() -> None:
    out = render.describe_block(
        kind="package",
        name="code-graph-io",
        identity_label="uri",
        identity_value="pkg:code-graph-io",
        attributes=[
            render.Attr.scalar("language", "language", "python"),
            render.Attr.scalar("version", "version", "1.8.0"),
            render.Attr.scalar("files", "files", 23),
            render.Attr("counts", "counts", "12 functions · 4 classes", {"function": 12, "class": 4}),
        ],
        relationships=[
            render.Rel("internal deps", "internal_dependencies", ["code-parser"]),
            render.Rel("internal dependents", "internal_dependents", ["agent-workspace-core"]),
        ],
        nav=["gw graph what-tests code-graph-io", "gw graph list-entry-points code-graph-io"],
        fmt="human",
    )
    expected = (
        "package code-graph-io\n"
        "  uri: pkg:code-graph-io\n"
        "\n"
        "attributes\n"
        "  language: python\n"
        "  version:  1.8.0\n"
        "  files:    23\n"
        "  counts:   12 functions · 4 classes\n"
        "\n"
        "relationships\n"
        "  internal deps:       code-parser\n"
        "  internal dependents: agent-workspace-core\n"
        "\n"
        "→ gw graph what-tests code-graph-io\n"
        "→ gw graph list-entry-points code-graph-io"
    )
    assert out == expected


def test_describe_block_human_omits_empty_sections() -> None:
    out = render.describe_block(
        kind="test_suite",
        name="code-graph-io-tests",
        identity_label="uri",
        identity_value="test://code-graph-io-tests",
        attributes=[render.Attr.scalar("kind", "kind", "pytest")],
        relationships=[],
        nav=[],
        fmt="human",
    )
    assert out == ("test_suite code-graph-io-tests\n  uri: test://code-graph-io-tests\n\nattributes\n  kind: pytest")
    assert "relationships" not in out
    assert "→" not in out


def test_describe_block_json_mirrors_spine() -> None:
    out = render.describe_block(
        kind="package",
        name="code-graph-io",
        identity_label="uri",
        identity_value="pkg:code-graph-io",
        attributes=[
            render.Attr.scalar("files", "files", 23),
            render.Attr("counts", "counts", "ignored-in-json", {"function": 12}),
        ],
        relationships=[render.Rel("domains", "domains", ["graph"])],
        nav=["gw graph what-tests code-graph-io"],
        fmt="json",
    )
    assert json.loads(out) == {
        "kind": "package",
        "name": "code-graph-io",
        "uri": "pkg:code-graph-io",
        "attributes": {"files": 23, "counts": {"function": 12}},
        "relationships": {"domains": ["graph"]},
        "nav": ["gw graph what-tests code-graph-io"],
    }


def test_describe_block_json_empty_sections_present() -> None:
    out = render.describe_block(
        kind="repository",
        name="r",
        identity_label="uri",
        identity_value="repo://r",
        attributes=[],
        relationships=[],
        nav=[],
        fmt="json",
    )
    parsed = json.loads(out)
    assert parsed["attributes"] == {} and parsed["relationships"] == {} and parsed["nav"] == []


def test_describe_block_scalar_none_renders_none() -> None:
    out = render.describe_block(
        kind="domain",
        name="d",
        identity_label="uri",
        identity_value="dom://d",
        attributes=[render.Attr.scalar("parent", "parent", None)],
        relationships=[],
        nav=[],
        fmt="human",
    )
    assert "  parent: (none)" in out


def test_describe_block_invalid_fmt() -> None:
    with pytest.raises(ValueError):
        render.describe_block(
            kind="x",
            name="y",
            identity_label="uri",
            identity_value="z",
            attributes=[],
            relationships=[],
            nav=[],
            fmt="xml",
        )


# ── children section (ASCII tree + JSON array) ───────────────────────


def test_describe_block_children_human_tree() -> None:
    from code_graph_io.queries import ChildNode

    tree = [
        ChildNode(
            kind="subpackage",
            uri="subpkg:x/sub",
            path="x/sub",
            line=None,
            children=[ChildNode(kind="file", uri=None, path="x/sub/a.py", line=None)],
        ),
        ChildNode(kind="file", uri=None, path="x/b.py", line=None),
    ]
    out = render.describe_block(
        kind="package",
        name="x",
        identity_label="uri",
        identity_value="pkg:x",
        attributes=[],
        relationships=[],
        nav=["gw graph what-tests x"],
        fmt="human",
        children=tree,
        children_depth=2,
    )
    assert "children (depth 2)" in out
    assert "├─ subpkg:x/sub" in out
    assert "│  └─ x/sub/a.py" in out
    assert "└─ x/b.py" in out
    # go-deeper hint uses the resolvable name (uri identity -> fall back to name)
    assert "→ gw graph describe x --depth 3" in out
    assert "→ gw graph what-tests x" in out


def test_describe_block_children_human_multilevel_connectors() -> None:
    # First top-level child is non-last AND has a nested child -> its descendant
    # must carry the `│  ` continuation prefix; the last-position leaf uses `└─ `.
    from code_graph_io.queries import ChildNode

    tree = [
        ChildNode(
            kind="subpackage",
            uri="subpkg:x/sub",
            path="x/sub",
            line=None,
            children=[ChildNode(kind="file", uri=None, path="x/sub/a.py", line=None)],
        ),
        ChildNode(kind="file", uri=None, path="x/b.py", line=None),
    ]
    out = render.describe_block(
        kind="package",
        name="x",
        identity_label="uri",
        identity_value="pkg:x",
        attributes=[],
        relationships=[],
        nav=[],
        fmt="human",
        children=tree,
        children_depth=2,
    )
    block = "\n".join(["children (depth 2)", "├─ subpkg:x/sub", "│  └─ x/sub/a.py", "└─ x/b.py"])
    assert block in out


def test_describe_block_children_json_array() -> None:
    from code_graph_io.queries import ChildNode

    tree = [ChildNode(kind="file", uri=None, path="x/b.py", line=None)]
    parsed = json.loads(
        render.describe_block(
            kind="package",
            name="x",
            identity_label="uri",
            identity_value="pkg:x",
            attributes=[],
            relationships=[],
            nav=[],
            fmt="json",
            children=tree,
            children_depth=1,
        )
    )
    assert parsed["children_depth"] == 1
    assert parsed["children"] == [
        {"kind": "file", "uri": None, "path": "x/b.py", "line": None, "name": None, "children": []}
    ]
    assert parsed["nav"] == ["gw graph describe x --depth 2"]


def test_describe_block_no_children_omits_section() -> None:
    out = render.describe_block(
        kind="package",
        name="x",
        identity_label="uri",
        identity_value="pkg:x",
        attributes=[],
        relationships=[],
        nav=[],
        fmt="human",
        children=[],
        children_depth=1,
    )
    assert "children (depth" not in out
    parsed = json.loads(
        render.describe_block(
            kind="package",
            name="x",
            identity_label="uri",
            identity_value="pkg:x",
            attributes=[],
            relationships=[],
            nav=[],
            fmt="json",
            children=[],
            children_depth=1,
        )
    )
    assert "children" not in parsed and "children_depth" not in parsed


def test_describe_block_children_label_fallback_path_line() -> None:
    from code_graph_io.queries import ChildNode

    tree = [ChildNode(kind="function", uri=None, path="a.py", line=12)]
    out = render.describe_block(
        kind="file",
        name="a.py",
        identity_label="path",
        identity_value="a.py",
        attributes=[],
        relationships=[],
        nav=[],
        fmt="human",
        children=tree,
        children_depth=2,
    )
    assert "└─ a.py:12" in out
    # path-identity kind -> go-deeper hint uses identity_value (the path)
    assert "→ gw graph describe a.py --depth 3" in out


def test_describe_block_children_symbol_label_uses_name() -> None:
    # Source-code symbols (function/class/method/type) have no uri -> show the
    # node name, not the path:line of the enclosing file.
    from code_graph_io.queries import ChildNode

    tree = [
        ChildNode(
            kind="class",
            uri=None,
            path="a.py",
            line=4,
            name="Widget",
            children=[ChildNode(kind="method", uri=None, path="a.py", line=8, name="render")],
        )
    ]
    out = render.describe_block(
        kind="file",
        name="a.py",
        identity_label="path",
        identity_value="a.py",
        attributes=[],
        relationships=[],
        nav=[],
        fmt="human",
        children=tree,
        children_depth=2,
    )
    assert "└─ Widget" in out
    assert "   └─ render" in out
    assert "a.py:4" not in out


def test_format_path_threads_children() -> None:
    from code_graph_io.queries import ChildNode, PathDescription

    desc = PathDescription(path="a.py", children=[], imports=[], role_flags=None, token_count=None)
    tree = [ChildNode(kind="function", uri=None, path="a.py", line=3)]
    out = render.format_path(desc, "human", children=tree, effective_depth=2)
    assert "children (depth 2)" in out and "└─ a.py:3" in out


# ── render(): record-shape handling, importer batches, truncation ─────────────


def test_render_accepts_plain_mappings_not_just_dataclasses() -> None:
    """`_to_dict` takes the Mapping branch, so dict rows render like records."""
    out = render.render([{"kind": "file", "name": "a.py"}], "json")
    assert json.loads(out) == [{"kind": "file", "name": "a.py"}]


def test_render_rejects_a_record_that_is_not_mapping_shaped() -> None:
    with pytest.raises(TypeError, match="not renderable as a mapping"):
        render.render(["just a string"], "json")


def test_render_unknown_format_raises() -> None:
    with pytest.raises(ValueError, match="unknown format"):
        render.render([Row("file", "a", "a.py", 1)], "yaml")


def test_render_human_with_no_rows_is_empty() -> None:
    assert render.render([], "human") == ""


def test_render_human_truncates_and_reports_the_total() -> None:
    rows = [Row("file", f"n{i}", f"{i}.py", i) for i in range(5)]
    seen: list[tuple[int, int]] = []
    out = render.render(rows, "human", cap=2, on_truncate=lambda c, t: seen.append((c, t)))
    assert out.splitlines()[-1] == "... showing 2 of 5 (truncated)"
    assert seen == [(2, 5)], "on_truncate must receive (cap, total)"


def test_render_json_truncates_silently() -> None:
    """JSON truncation emits a flat capped array — no trailer, no envelope."""
    rows = [Row("file", f"n{i}", f"{i}.py", i) for i in range(5)]
    assert len(json.loads(render.render(rows, "json", cap=2))) == 2


def test_render_does_not_invoke_on_truncate_below_the_cap() -> None:
    seen: list[tuple[int, int]] = []
    render.render([Row("file", "a", "a.py", 1)], "human", cap=9, on_truncate=lambda c, t: seen.append((c, t)))
    assert seen == []


@dataclass(frozen=True)
class ImporterRecord:
    """Name-matched stand-in — `_is_importer_batch` dispatches on type name."""

    path: str
    symbols: tuple[str, ...]
    depth: int


def test_render_importer_batch_human_aligns_and_parenthesizes_symbols() -> None:
    rows = [ImporterRecord("a/long/path.py", ("x", "y"), 1), ImporterRecord("b.py", (), 2)]
    lines = render.render(rows, "human").splitlines()
    assert "(x, y)" in lines[0]
    assert lines[1].startswith("b.py")
    assert len({len(line) for line in lines}) == 1, "columns should be width-aligned"


def test_render_importer_batch_json_flattens_one_row_per_symbol() -> None:
    rows = [ImporterRecord("a.py", ("x", "y"), 1), ImporterRecord("b.py", (), 2)]
    assert json.loads(render.render(rows, "json")) == [
        {"path": "a.py", "symbol": "x", "depth": 1},
        {"path": "a.py", "symbol": "y", "depth": 1},
        {"path": "b.py", "symbol": None, "depth": 2},
    ]


def test_render_importer_batch_human_truncates() -> None:
    rows = [ImporterRecord(f"{i}.py", (), i) for i in range(4)]
    out = render.render(rows, "human", cap=1)
    assert out.splitlines()[-1] == "... showing 1 of 4 (truncated)"


def test_render_importer_batch_unknown_format_raises() -> None:
    with pytest.raises(ValueError, match="unknown format"):
        render.render([ImporterRecord("a.py", (), 1)], "yaml")


# ── _pluralize ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("word", "n", "expected"),
    [
        ("file", 1, "file"),  # n == 1 is always the bare word
        ("class", 2, "classes"),  # -s  -> -es
        ("box", 2, "boxes"),  # -x  -> -es
        ("match", 2, "matches"),  # -ch -> -es
        ("dish", 2, "dishes"),  # -sh -> -es
        ("dependency", 2, "dependencies"),  # consonant + y -> -ies
        ("key", 2, "keys"),  # vowel + y -> -s
        ("file", 2, "files"),  # default -> -s
    ],
)
def test_pluralize(word: str, n: int, expected: str) -> None:
    assert render._pluralize(word, n) == expected
