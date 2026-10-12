"""Static distribution shape and explicitly declared runtime dependencies."""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]


def test_distribution_shape() -> None:
    data = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["name"] == "gw-orchestrate"
    assert data["project"]["version"] == "0.1.0"
    assert data["project"]["requires-python"] == ">=3.12"
    assert set(data["project"]["dependencies"]) == {
        "gw-kernel>=0.7,<0.8",
        "gw-pipeline>=0.1,<0.2",
        "work-tracker-okf>=0.6.0,<0.7",
        "okf-io>=0.2.6,<0.3",
        "okf-ext[schemas]>=0.5.5,<0.6",
        "subagents-io>=0.2,<0.3",
    }
    assert data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/gw_orchestrate"]
    assert set(data["tool"]["uv"]["sources"]) == {
        "gw-kernel",
        "gw-pipeline",
        "gw-work",
        "work-tracker-okf",
        "okf-io",
        "okf-ext",
        "subagents-io",
    }
    assert data["dependency-groups"]["dev"] == ["gw-work>=0.1,<0.2"]
    assert not any(dep.startswith("gw-work") for dep in data["project"]["dependencies"])
    assert (PACKAGE / "src/gw_orchestrate/py.typed").is_file()


def test_application_import_resolves_to_this_checkout() -> None:
    spec = importlib.util.find_spec("gw_orchestrate")
    assert spec is not None and spec.origin is not None
    assert Path(spec.origin).resolve() == PACKAGE / "src/gw_orchestrate/__init__.py"
