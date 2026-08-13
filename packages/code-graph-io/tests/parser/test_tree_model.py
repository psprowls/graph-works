import dataclasses
from pathlib import Path

import pytest
from code_graph_io.parser import (
    Reference,
    SourceNode,
    Span,
)


def test_span_frozen():
    span = Span(0, 10, 1, 2, 0, 5)
    with pytest.raises(dataclasses.FrozenInstanceError):
        span.start_byte = 99


def test_reference_construction_and_defaults():
    span = Span(0, 5, 1, 1, 0, 5)
    ref = Reference(kind="call", target_name="foo", target_module=None, site=span)
    assert ref.kind == "call"
    assert ref.attrs == {}


def test_source_node_attrs_independent_per_instance():
    span = Span(0, 0, 1, 1, 0, 0)
    a = SourceNode(
        kind="file",
        name=None,
        span=span,
        path=Path("a.py"),
        language="python",
        package=None,
    )
    b = SourceNode(
        kind="file",
        name=None,
        span=span,
        path=Path("b.py"),
        language="python",
        package=None,
    )
    a.attrs["x"] = 1
    assert "x" not in b.attrs
    assert b.children == []
    assert b.refs == []


def test_source_node_can_be_mutated():
    span = Span(0, 0, 1, 1, 0, 0)
    node = SourceNode(
        kind="file",
        name=None,
        span=span,
        path=Path("a.py"),
        language="python",
        package=None,
    )
    child = SourceNode(
        kind="function",
        name="f",
        span=span,
        path=Path("a.py"),
        language="python",
        package=None,
    )
    node.children.append(child)
    assert node.children == [child]
