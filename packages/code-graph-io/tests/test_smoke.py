"""Smoke tests: package imports."""

from __future__ import annotations


def test_package_imports() -> None:
    import code_graph_io

    assert code_graph_io.__version__ == "0.1.1"
