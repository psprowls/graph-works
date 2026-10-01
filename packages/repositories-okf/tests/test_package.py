"""Distribution metadata — mirrors doc-wiki-okf's and work-tracker-okf's."""

from __future__ import annotations

import importlib.resources
import tomllib
from pathlib import Path
from typing import Any

MANIFEST = Path(__file__).resolve().parents[1] / "pyproject.toml"


def manifest() -> dict[str, Any]:
    with MANIFEST.open("rb") as handle:
        return tomllib.load(handle)


def test_version() -> None:
    import repositories_okf

    assert repositories_okf.__version__ == "0.1.3"
    assert manifest()["project"]["version"] == "0.1.3"


def test_dependencies_are_exactly_okf_io_and_okf_ext() -> None:
    """Git runs through stdlib subprocess in git.py only; no additional dependency."""
    names = {entry.split(">")[0].split("<")[0].split("=")[0].strip() for entry in manifest()["project"]["dependencies"]}
    assert names == {"okf-io", "okf-ext[schemas]"}


def test_the_module_root_is_flat_and_singular() -> None:
    config = manifest()
    assert config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/repositories_okf"]
    assert config["project"]["name"] == "repositories-okf"
    assert "scripts" not in config["project"]


def test_py_typed_ships_inside_the_package() -> None:
    assert (importlib.resources.files("repositories_okf") / "py.typed").is_file()
