"""Smoke tests: package imports."""

from __future__ import annotations

import importlib.metadata


def test_version_matches_breaking_dependency_contract() -> None:
    import code_graph_io

    assert code_graph_io.__version__ == "0.3.1"


def test_version_matches_package_metadata() -> None:
    """The static version lives in two places; nothing else ties them together.

    Without this check they drifted once already, while the package still
    carried the versions it was ported in with. Mirrors okf-io's guard.
    """
    import code_graph_io

    assert code_graph_io.__version__ == importlib.metadata.version("code-graph-io")


def test_ignore_matching_is_public() -> None:
    from code_graph_io import IgnoreSpec, compile_ignore

    spec = compile_ignore(["**/fixtures/**", "AGENTS.md"])
    assert isinstance(spec, IgnoreSpec)
    assert spec.matches("pkg/tests/fixtures/a.py")
    assert spec.matches("AGENTS.md")
    assert not spec.matches("pkg/AGENTS.md")
