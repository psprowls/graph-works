"""The five reader-level callables, and the describe spine underneath them.

Every assertion here is on rendered output from a real graph opened through
the real reader — the spec's §7 risk is that `just check` cannot see a broken
seam, and mocks are exactly what would hide one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from code_graph_io import exit_codes, open_reader
from graph_works_core.graph import graph_tools


@pytest.fixture
def reader(graph_dir: Path):
    handle = open_reader(graph_dir=graph_dir)
    try:
        yield handle
    finally:
        handle.close()


def test_describe_covers_every_one_of_the_nine_kinds(reader):
    cases = [
        ("repository", "demo", "repository demo"),
        ("package", "widgets", "package widgets"),
        ("app", "console", "app console"),
        ("path", "packages/widgets/src/a.py", "packages/widgets/src/a.py"),
        ("test_suite", "tests", "test_suite tests"),
        ("entry_point", "widgets:demo-cli", "demo-cli"),
        ("dependency", "requests", "dependency requests"),
        ("agent_plugin", "demo-plugin", "agent_plugin demo-plugin"),
        ("builtin", "python/json", "builtin json"),
    ]
    for kind, identifier, expected in cases:
        code, out, err = graph_tools._describe(reader, kind, identifier)
        assert code == exit_codes.SUCCESS, (kind, err)
        assert expected in out, (kind, out)


def test_repository_needs_no_identifier(reader):
    code, out, _ = graph_tools._describe(reader, "repository", None)
    assert code == exit_codes.SUCCESS
    assert "repository demo" in out


def test_every_other_kind_requires_one(reader):
    code, out, err = graph_tools._describe(reader, "package", None)
    assert code == exit_codes.GENERIC
    assert out == ""
    assert err == "error: identifier required for kind 'package'"


def test_unknown_kind_is_generic_and_names_the_valid_set(reader):
    code, _, err = graph_tools._describe(reader, "domain", "anything")
    assert code == exit_codes.GENERIC
    assert err.startswith("error: invalid kind 'domain'; valid: ")
    assert "agent_plugin" in err


@pytest.mark.parametrize(
    ("kind", "identifier"),
    [
        ("package", "absent"),
        ("app", "absent"),
        ("path", "no/such/file.py"),
        ("test_suite", "absent"),
        ("dependency", "absent"),
        ("dependency", "npm/absent"),
        ("agent_plugin", "absent"),
        ("builtin", "python/absent"),
        ("entry_point", "widgets:absent"),
        ("entry_point", "absent"),
    ],
)
def test_a_missing_entity_is_generic_not_an_exception(reader, kind, identifier):
    code, out, err = graph_tools._describe(reader, kind, identifier)
    assert code == exit_codes.GENERIC
    assert out == ""
    assert err.startswith("error: ")


def test_a_malformed_builtin_uri_is_generic(reader):
    code, _, err = graph_tools._describe(reader, "builtin", "json")
    assert code == exit_codes.GENERIC
    assert err == "error: malformed builtin URI: json"


def test_a_builtin_uri_prefix_is_accepted(reader):
    code, out, _ = graph_tools._describe(reader, "builtin", "builtin:python/json")
    assert code == exit_codes.SUCCESS
    assert "builtin json" in out


def test_a_bare_dependency_name_defaults_to_pypi(reader):
    code, out, _ = graph_tools._describe(reader, "dependency", "requests")
    assert code == exit_codes.SUCCESS
    assert "pypi" in out


def test_an_ambiguous_bare_entry_point_is_ambiguous_seven(reader):
    code, out, err = graph_tools._describe(reader, "entry_point", "shared")
    assert code == exit_codes.AMBIGUOUS
    assert out == ""
    assert "widgets" in err
    assert "console" in err
    assert "use 'package:entry'" in err


def test_a_bare_unambiguous_entry_point_resolves(reader):
    code, out, _ = graph_tools._describe(reader, "entry_point", "demo-cli")
    assert code == exit_codes.SUCCESS
    assert "demo-cli" in out


def test_depth_reaches_the_render_layer(reader):
    _, shallow, _ = graph_tools._describe(reader, "package", "widgets", 1)
    _, deep, _ = graph_tools._describe(reader, "package", "widgets", 2)
    assert "children (depth 1)" in shallow
    assert "children (depth 2)" in deep


def test_describe_returns_the_rendered_string_on_success(reader):
    assert "package widgets" in graph_tools.describe(reader, kind="package", identifier="widgets")


def test_describe_returns_the_error_line_on_failure(reader):
    assert graph_tools.describe(reader, kind="package", identifier="absent").startswith("error: ")
    assert graph_tools.describe(reader, kind="domain", identifier="x").startswith("error: invalid kind")


def test_describe_ignores_the_identifier_for_repository(reader):
    assert "repository demo" in graph_tools.describe(reader, kind="repository", identifier="ignored")


def test_find_needs_at_least_one_filter(reader):
    assert graph_tools.find(reader) == "error: at least one of name, kind, in_package required"


def test_find_by_kind_renders_rows(reader):
    out = graph_tools.find(reader, kind="function")
    assert "alpha" in out
    assert "beta" in out


def test_find_by_name_and_in_package(reader):
    assert "alpha" in graph_tools.find(reader, name="alpha")
    assert "a.py" in graph_tools.find(reader, kind="file", in_package="widgets")


def test_find_with_no_match_is_an_empty_string_not_an_error(reader):
    assert graph_tools.find(reader, name="nothing-by-this-name") == ""


def test_find_with_an_unknown_kind_is_a_recoverable_error(reader):
    out = graph_tools.find(reader, kind="galaxy")
    assert out.startswith("error: unknown kind 'galaxy'")


def test_callers_and_callees_walk_the_call_edge(reader):
    assert "alpha" in graph_tools.callers(reader, name="beta")
    assert "beta" in graph_tools.callees(reader, name="alpha")


def test_callers_of_an_unknown_symbol_is_empty_not_an_error(reader):
    assert graph_tools.callers(reader, name="nobody") == ""


def test_imports_lists_the_imported_file(reader):
    assert "b.py" in graph_tools.imports(reader, path="packages/widgets/src/a.py")


def test_the_row_cap_truncates_and_says_so(tmp_path, graph_dir):
    """A graph with more than ROW_CAP functions renders 50 rows plus a notice."""
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

    handle = open_reader(graph_dir=graph_dir)
    try:
        out = graph_tools.find(handle, kind="function")
    finally:
        handle.close()
    lines = out.splitlines()
    assert len(lines) == graph_tools.ROW_CAP + 1
    assert lines[-1] == "... showing 50 of 64 (truncated)"


def test_fmt_json_threads_through_describe(reader):
    code, out, _ = graph_tools._describe(reader, "package", "widgets", fmt="json")
    assert code == exit_codes.SUCCESS
    data = json.loads(out)
    assert data["kind"] == "package"
    assert data["name"] == "widgets"


def test_fmt_defaults_to_human(reader):
    code, out, _ = graph_tools._describe(reader, "package", "widgets")
    assert code == exit_codes.SUCCESS
    assert "package widgets" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_fmt_json_threads_through_find(reader):
    out = graph_tools.find(reader, kind="function", fmt="json")
    rows = json.loads(out)
    assert any(r["name"] == "alpha" for r in rows)


def test_describe_fmt_json_via_reader_describe(reader):
    out = graph_tools.describe(reader, kind="package", identifier="widgets", fmt="json")
    assert json.loads(out)["kind"] == "package"


def test_describe_symbol_function_hit_shows_callees(reader):
    code, out, _ = graph_tools._describe(reader, "function", "alpha")
    assert code == exit_codes.SUCCESS
    assert "function alpha" in out
    assert "beta" in out


@pytest.mark.parametrize("kind", ["function", "class", "method", "type"])
def test_a_missing_code_symbol_is_generic_not_an_exception(reader, kind):
    code, out, err = graph_tools._describe(reader, kind, "absent")
    assert code == exit_codes.GENERIC
    assert out == ""
    assert err == f"error: {kind} not found: absent"


def test_describe_kinds_covers_all_thirteen():
    assert graph_tools.DESCRIBE_KINDS.index("builtin") == len(graph_tools.DESCRIBE_KINDS) - 1
    for kind in ("function", "class", "method", "type"):
        assert kind in graph_tools.DESCRIBE_KINDS
    assert len(graph_tools.DESCRIBE_KINDS) == 13


def test_kind_none_infers_a_single_match_package(reader):
    explicit = graph_tools._describe(reader, "package", "widgets")
    inferred = graph_tools._describe(reader, None, "widgets")
    assert inferred == explicit


def test_kind_none_infers_a_single_match_function(reader):
    explicit = graph_tools._describe(reader, "function", "alpha")
    inferred = graph_tools._describe(reader, None, "alpha")
    assert inferred == explicit


def test_kind_none_multi_match_is_a_disambiguation_menu(reader):
    code, out, err = graph_tools._describe(reader, None, "shared")
    assert code == exit_codes.SUCCESS
    assert err == ""
    # The menu shows entry_point rows (build_menu currently shows duplicate commands for
    # path-less entities like entry_points with the same name, which is a limitation of
    # the current code-graph-io build_menu). The fact that there are two suggests they
    # came from two different nodes.
    lines = out.splitlines()
    assert len(lines) == 2
    assert all("entry_point" in line for line in lines)

    # The explicit entry_point path still disagrees on purpose.
    explicit_code, _, explicit_err = graph_tools._describe(reader, "entry_point", "shared")
    assert explicit_code == exit_codes.AMBIGUOUS
    assert "use 'package:entry'" in explicit_err


def test_kind_none_unresolvable_selector_falls_back_to_path(reader):
    code, out, err = graph_tools._describe(reader, None, "no/such/thing")
    assert code == exit_codes.GENERIC
    assert out == ""
    assert err.startswith("error: path not found in graph")


def test_kind_none_with_no_identifier_is_repository(reader):
    code, out, _ = graph_tools._describe(reader, None, None)
    assert code == exit_codes.SUCCESS
    assert "repository demo" in out


def test_kind_none_builtin_prefix_still_resolves(reader):
    code, out, _ = graph_tools._describe(reader, None, "builtin:python/json")
    assert code == exit_codes.SUCCESS
    assert "builtin json" in out


def test_fmt_json_on_a_disambiguation_menu(reader):
    code, out, _ = graph_tools._describe(reader, None, "shared", fmt="json")
    assert code == exit_codes.SUCCESS
    rows = json.loads(out)
    assert {r["kind"] for r in rows} == {"entry_point"}


def test_kind_none_infers_file_kind_and_uses_path_identifier(reader):
    """When kind=None resolves to a single file match, _identifier_for
    extracts and uses the path attribute."""
    # Selector "a.py" resolves to exactly one file node
    inferred = graph_tools._describe(reader, None, "a.py")
    # Should match the explicit kind="path" call with the full path
    explicit = graph_tools._describe(reader, "path", "packages/widgets/src/a.py")
    assert inferred == explicit


def test_kind_none_infers_dependency_kind_and_uses_ecosystem_name_identifier(reader):
    """When kind=None resolves to a single dependency match, _identifier_for
    constructs the ecosystem/name identifier."""
    # Selector "requests" resolves to exactly one dependency node
    inferred = graph_tools._describe(reader, None, "requests")
    # Should match the explicit kind="dependency" call with the bare name
    # (which defaults to pypi ecosystem)
    explicit = graph_tools._describe(reader, "dependency", "requests")
    assert inferred == explicit


def test_kind_none_infers_builtin_kind_and_uses_builtin_prefix_identifier(reader):
    """When kind=None resolves to a single builtin match, _identifier_for
    constructs the builtin:language/module_name identifier."""
    # Selector "json" resolves to exactly one builtin node
    inferred = graph_tools._describe(reader, None, "json")
    # Should match the explicit kind="builtin" call with the builtin: prefix
    explicit = graph_tools._describe(reader, "builtin", "builtin:python/json")
    assert inferred == explicit


def test_in_package_narrows_a_cross_package_symbol_collision(reader):
    both = reader.resolve_selector(selector="gamma")
    assert len(both) == 2
    assert {m.path for m in both} == {
        "packages/widgets/src/b.py",
        "apps/console/src/index.ts",
    }

    widgets_only = reader.resolve_selector(selector="gamma", in_package="widgets")
    assert len(widgets_only) == 1
    assert widgets_only[0].path == "packages/widgets/src/b.py"

    console_only = reader.resolve_selector(selector="gamma", in_package="console")
    assert len(console_only) == 1
    assert console_only[0].path == "apps/console/src/index.ts"


def test_describe_symbol_in_package_reaches_the_dispatch(reader):
    code, out, _ = graph_tools._describe(reader, "function", "gamma", in_package="widgets")
    assert code == exit_codes.SUCCESS
    assert "packages/widgets/src/b.py" in out
    assert "apps/console/src/index.ts" not in out
