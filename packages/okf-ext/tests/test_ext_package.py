"""The package is installed, typed, and shaped the way the layering claims."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import okf_ext
from okf_ext import DEFAULT_NORMALIZATION, ExtContext, NormalizationPolicy

SRC = Path(__file__).resolve().parents[1] / "src" / "okf_ext"


def test_version_is_static_and_pinned():
    """ADR-0007: static `version`, never hatch-vcs."""
    assert okf_ext.__version__ == "0.1.0"


def test_py_typed_marker_ships():
    assert (SRC / "py.typed").is_file()


def test_default_context_needs_no_arguments():
    """Every field defaults, so `ctx=None` is always substitutable."""
    assert ExtContext().normalization == DEFAULT_NORMALIZATION


def test_default_policy_matches_the_corpus_dialect():
    assert (
        NormalizationPolicy(case="lower", separator="-", unicode_form="NFC", strip=True)
        == DEFAULT_NORMALIZATION
    )


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
