"""Serve ships at gw's version (ADR-0058) and declares its console script."""

from __future__ import annotations

import tomllib
from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[2]


def _project(name: str) -> dict[str, object]:
    with (PACKAGES / name / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def test_serve_version_equals_the_cli_version() -> None:
    assert _project("graph-works-serve")["version"] == _project("graph-works-cli")["version"]


def test_gw_serve_is_the_console_script() -> None:
    assert _project("graph-works-serve")["scripts"] == {"gw-serve": "graph_works_serve.main:run"}


def test_the_cli_is_not_a_runtime_dependency() -> None:
    deps = _project("graph-works-serve")["dependencies"]
    assert isinstance(deps, list)
    assert not any(str(d).startswith("graph-works-cli") for d in deps)
