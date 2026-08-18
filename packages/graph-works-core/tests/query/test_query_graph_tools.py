"""The tool factory, and the three graph states that are not an error."""

from __future__ import annotations

from pathlib import Path

from code_graph_io import GraphNotInitializedError, SchemaMismatchError
from graph_works_core.graph.commands import GraphTarget
from graph_works_core.query import commands as q

CG_NAMES = {"cg_find", "cg_describe", "cg_callers", "cg_callees", "cg_imports"}


class FakeReader:
    def __init__(self, nodes: int = 3) -> None:
        self.nodes = nodes
        self.closed = False
        self.seen: list[tuple[str, dict]] = []

    def node_count(self) -> int:
        return self.nodes

    def close(self) -> None:
        self.closed = True

    def find(self, *, name=None, kind=None, in_package=None):
        self.seen.append(("find", {"name": name, "kind": kind, "in_package": in_package}))
        return []

    def callers(self, *, name, depth=3):
        self.seen.append(("callers", {"name": name, "depth": depth}))
        return []

    def callees(self, *, name, depth=3):
        self.seen.append(("callees", {"name": name, "depth": depth}))
        return []

    def imports(self, *, path):
        self.seen.append(("imports", {"path": path}))
        return []


def test_the_factory_binds_exactly_the_five_cg_tools():
    tools = q.build_graph_tools(FakeReader())
    assert {t.name for t in tools} == CG_NAMES
    assert len(tools) == 5


def test_each_tool_reaches_the_reader_it_closed_over():
    reader = FakeReader()
    by_name = {t.name: t for t in q.build_graph_tools(reader)}

    by_name["cg_find"].invoke({"name": "SubagentPool"})
    by_name["cg_callers"].invoke({"name": "run_all", "depth": 2})
    by_name["cg_imports"].invoke({"path": "pkg/mod.py"})

    assert [call for call, _ in reader.seen] == ["find", "callers", "imports"]
    assert reader.seen[1][1] == {"name": "run_all", "depth": 2}


def test_a_filterless_find_is_refused_before_the_reader_is_asked():
    reader = FakeReader()
    by_name = {t.name: t for t in q.build_graph_tools(reader)}
    assert "at least one of" in by_name["cg_find"].invoke({})
    assert reader.seen == []


def _target(tmp_path: Path) -> GraphTarget:
    return GraphTarget(graph_dir=tmp_path / "graph")


def test_an_uninitialized_graph_yields_no_tools_and_one_stderr_line(tmp_path, monkeypatch, capsys):
    def boom(*, graph_dir):
        raise GraphNotInitializedError("no graph")

    monkeypatch.setattr(q, "open_reader", boom)
    reader, tools = q._load_query_graph_tools(_target(tmp_path))
    assert (reader, tools) == (None, [])
    assert capsys.readouterr().err.count("\n") == 1


def test_a_schema_mismatch_yields_no_tools_and_one_stderr_line(tmp_path, monkeypatch, capsys):
    def boom(*, graph_dir):
        raise SchemaMismatchError("v1", 2)

    monkeypatch.setattr(q, "open_reader", boom)
    assert q._load_query_graph_tools(_target(tmp_path)) == (None, [])
    assert capsys.readouterr().err.count("\n") == 1


def test_a_zero_node_graph_is_treated_as_unavailable_and_the_reader_is_closed(tmp_path, monkeypatch, capsys):
    # Not defensive padding: a schema-valid empty graph binds tools that always
    # return nothing, and the librarian then loops to its iteration cap.
    empty = FakeReader(nodes=0)
    monkeypatch.setattr(q, "open_reader", lambda *, graph_dir: empty)
    assert q._load_query_graph_tools(_target(tmp_path)) == (None, [])
    assert empty.closed is True
    assert capsys.readouterr().err.count("\n") == 1


def test_a_populated_graph_yields_five_tools_and_an_open_reader(tmp_path, monkeypatch, capsys):
    populated = FakeReader(nodes=12)
    monkeypatch.setattr(q, "open_reader", lambda *, graph_dir: populated)
    reader, tools = q._load_query_graph_tools(_target(tmp_path))
    assert reader is populated
    assert populated.closed is False  # the caller closes, in `finally`
    assert {t.name for t in tools} == CG_NAMES
    assert capsys.readouterr().err == ""
