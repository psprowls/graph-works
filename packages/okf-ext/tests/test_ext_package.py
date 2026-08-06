"""The package is installed, typed, and shaped the way the layering claims."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import okf_ext
from okf_ext import DEFAULT_NORMALIZATION, ExtContext, NormalizationPolicy

SRC = Path(__file__).resolve().parents[1] / "src" / "okf_ext"


def test_version_is_static_and_pinned():
    """Static `version`, never hatch-vcs. Pre-1.0, minor is breaking
    (ADR-0007): `tags`' internals moved into `okf_ext.writing` for this
    release even though its public names did not."""
    assert okf_ext.__version__ == "0.2.0"


def test_py_typed_marker_ships():
    assert (SRC / "py.typed").is_file()


def test_default_context_needs_no_arguments():
    """Every field defaults, so `ctx=None` is always substitutable."""
    assert ExtContext().normalization == DEFAULT_NORMALIZATION


def test_default_policy_matches_the_corpus_dialect():
    assert NormalizationPolicy(case="lower", separator="-", unicode_form="NFC", strip=True) == DEFAULT_NORMALIZATION


def test_top_level_does_not_import_any_capability():
    """Installing okf-ext for one capability must not load the machinery of the others.

    Asked of a fresh interpreter rather than of `vars(okf_ext)`: this process
    has already imported `okf_ext.tags` for other tests, and importing a
    submodule anywhere binds it on the parent package. Only a subprocess can
    still see what `__init__.py` alone pulls in.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import okf_ext, sys; print('okf_ext.tags' in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"


def test_the_capability_is_still_reachable_as_a_submodule():
    from okf_ext import tags

    assert tags.DEFAULT_IGNORE == ("_tags.yaml", "*/_tags.yaml")


def test_the_top_level_still_imports_no_capability_now_that_there_are_six():
    """The claim in `test_top_level_does_not_import_any_capability` was made
    when there was one capability to not import. Restated over all six."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import okf_ext, sys\nprint(sorted(m for m in sys.modules if m.startswith('okf_ext.')))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "okf_ext.tags" not in result.stdout
    assert "okf_ext.schemas" not in result.stdout
    assert "okf_ext.render" not in result.stdout
    assert "okf_ext.health" not in result.stdout
    assert "okf_ext.search" not in result.stdout
    assert "okf_ext.tables" not in result.stdout


def test_the_second_capability_is_reachable_as_a_submodule():
    from okf_ext import schemas

    assert schemas.DEFAULT_IGNORE == ("_schema/*", "*/_schema/*")


def test_the_third_capability_is_reachable_as_a_submodule():
    from okf_ext import search

    assert search.DEFAULT_WEIGHTS == {"title": 3, "description": 2, "tags": 2, "body": 1}


def test_the_fourth_capability_is_reachable_as_a_submodule():
    from okf_ext import tables

    assert callable(tables.read_section)
