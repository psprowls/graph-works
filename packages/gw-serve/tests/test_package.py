"""Serve ships at gw's version (ADR-0058) and declares its console script."""

from __future__ import annotations

import tomllib
from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[2]


def _project(name: str) -> dict[str, object]:
    with (PACKAGES / name / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def test_serve_version_equals_the_cli_version() -> None:
    assert _project("gw-serve")["version"] == _project("gw-cli")["version"]


def test_gw_serve_is_the_console_script() -> None:
    assert _project("gw-serve")["scripts"] == {"gw-serve": "gw_serve.main:run"}


def test_the_cli_is_not_a_runtime_dependency() -> None:
    deps = _project("gw-serve")["dependencies"]
    assert isinstance(deps, list)
    assert not any(str(d).startswith("gw-cli") for d in deps)


def test_interface_packages_pin_the_renamed_wire() -> None:
    assert _project("gw-wire")["version"] == "0.3.0"
    assert _project("gw-cli")["version"] == "0.5.0"
    for name in ("gw-cli", "gw-serve"):
        deps = _project(name)["dependencies"]
        assert isinstance(deps, list)
        assert "gw-wire>=0.3,<0.4" in deps
        assert not any(str(d).startswith("graph-works-") for d in deps)
