"""Distribution metadata — mirrors code-wiki-okf's and work-tracker-okf's."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

MANIFEST = Path(__file__).resolve().parents[1] / "pyproject.toml"


def manifest() -> dict[str, Any]:
    with MANIFEST.open("rb") as handle:
        return tomllib.load(handle)


def test_version() -> None:
    import doc_wiki_okf

    assert doc_wiki_okf.__version__ == "0.2.0"


def test_dependencies_are_exactly_the_three_the_spec_declares() -> None:
    """Spec §1.1: the tier-3 triple, minus code-graph-io. A fourth runtime
    dependency is a scope decision, not an implementation detail."""
    names = {entry.split(">")[0].split("<")[0].split("=")[0].strip() for entry in manifest()["project"]["dependencies"]}
    assert names == {"okf-io", "okf-ext[schemas]", "typer"}


def test_the_console_script_names_the_module_that_exists() -> None:
    """Spec §6: the CLI fills the entry `pyproject.toml` reserved for it. An
    entry point naming a module nobody wrote is broken in every environment
    that installs the wheel, so this asserts the module imports."""
    import importlib

    assert manifest()["project"]["scripts"] == {"doc-wiki-okf": "doc_wiki_okf.cli:app"}
    assert importlib.import_module("doc_wiki_okf.cli").app is not None


def test_the_module_root_is_flat_and_singular() -> None:
    """ADR-0006: one module root, no namespace nesting."""
    config = manifest()
    assert config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/doc_wiki_okf"]
    assert config["project"]["name"] == "doc-wiki-okf"


def test_py_typed_ships_inside_the_package() -> None:
    import importlib.resources

    assert (importlib.resources.files("doc_wiki_okf") / "py.typed").is_file()
