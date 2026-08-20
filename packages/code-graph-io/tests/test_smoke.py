"""Smoke tests: package imports."""

from __future__ import annotations

import importlib.metadata


def test_package_imports() -> None:
    import code_graph_io

    assert code_graph_io.__version__ == "0.1.1"


def test_version_matches_package_metadata() -> None:
    """The static version lives in two places; nothing else ties them together.

    Without this check they drifted once already, while the package still
    carried the versions it was ported in with. Mirrors okf-io's guard.
    """
    import code_graph_io

    assert code_graph_io.__version__ == importlib.metadata.version("code-graph-io")
