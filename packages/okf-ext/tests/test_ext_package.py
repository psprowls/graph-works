"""The package is installed, typed, and shaped the way the layering claims."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from pathlib import Path

import okf_ext
from okf_ext import DEFAULT_NORMALIZATION, ExtContext, NormalizationPolicy
from test_ext_boundaries import capability_modules, capability_names

SRC = Path(__file__).resolve().parents[1] / "src" / "okf_ext"

#: The capability set `test_the_top_level_still_imports_no_capability_now_that_there_are_ten`
#: walks. A literal, not derived from the filesystem at collection time, so an
#: eleventh capability directory landing here is a choice a human makes rather
#: than something this tuple silently starts covering on its own -- guarded
#: against drift from the filesystem by
#: `test_the_capability_tuple_matches_the_filesystem` below, the same pattern
#: `test_ext_boundaries.py`'s own `capability` fixture uses.
CAPABILITY_NAMES = (
    "tags",
    "schemas",
    "render",
    "health",
    "search",
    "tables",
    "moves",
    "sections",
    "generators",
    "proposals",
    "bundle",
    "placement",
    "logs",
)


def test_version_is_static_and_pinned():
    """Static `version`, never hatch-vcs. Pre-1.0, minor is breaking and patch
    is compatible (ADR-0007). Three additive changes land across this range:
    the `logs` capability (the locked/atomic `log.md` append lifted out of
    `work_tracker_okf.compose` so a third consumer does not make a third
    copy), the `tags.merge` vocabulary splice, and `PendingWrite.rendered`
    widening to `str | bytes` (ADR-0031). `proposals.placement` is renamed to
    `proposal_path` (ADR-0030); `placement` stays as an additive deprecated
    alias with no behaviour change for any existing caller. All of it is
    compatible, so this is a patch.

    **`0.4.8`, not `0.4.7`.** `0.4.6` was cut independently on two branches
    -- `logs` on one, `proposal_path` on the other -- so both that number and
    the `0.4.7` that followed one of them name two different trees. The merge
    steps past the ambiguity rather than reusing a taken number.

    `0.4.9` routes `tags.inventory`'s `tags-not-a-sequence` detail through
    okf-io's new `value_shape` instead of `type(...).__name__`, which is why
    the okf-io floor moves to `0.2.3`.

    `0.5.0` is still not free: the README promises the `okf_ext.sections`
    re-export shim, and now the `placement` alias too, come out there."""
    assert okf_ext.__version__ == "0.4.11"


def test_the_distribution_version_matches_the_python_attribute():
    """ADR-0007's static-version policy has two places to go stale, not one:
    `packages/okf-ext/pyproject.toml`'s `[project].version` (what a wheel is
    built and tagged `okf-ext-vX.Y.Z` as) and `okf_ext.__version__` (what a
    caller reads at import time). Nothing connects them -- versions are
    static here, never `hatch-vcs`-derived from a tag, so both are hand-edited
    on every release, and every prior capability commit bumped them in
    lockstep by hand with nothing enforcing it. A mismatch means a wheel
    that would ship named `okf_ext-0.3.1-*.whl` while reporting a different
    `__version__` at import time -- two sources of truth for one version,
    disagreeing. `importlib.metadata.version` reads the installed
    distribution's metadata, built from `pyproject.toml`, so this compares
    the two independently of either one's own claim about itself."""
    assert importlib.metadata.version("okf-ext") == okf_ext.__version__


def test_the_capability_tuple_matches_the_filesystem() -> None:
    """`test_ext_boundaries.py`'s `test_every_capability_on_disk_is_covered_by_these_tests`
    guards its own capability literal against a filesystem walk so a new
    capability added without extending that literal fails loudly rather than
    silently going uncovered. `CAPABILITY_NAMES` above had no such guard until
    this test: a tenth capability landing on disk without this tuple being
    extended would not fail anything -- it would just silently stop being
    checked for `sys.modules` leakage by the test below, the exact class of
    bug this whole suite exists to catch. Derived from the same filesystem
    walk `test_ext_boundaries.py` already performs
    (`capability_names(capability_modules())`), not a second independent walk
    that could itself drift from the first."""
    assert set(CAPABILITY_NAMES) == capability_names(capability_modules())


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

    assert tags.DEFAULT_IGNORE == ("tags.yaml", "*/tags.yaml")


def test_the_top_level_still_imports_no_capability_now_that_there_are_ten():
    """The claim in `test_top_level_does_not_import_any_capability` was made
    when there was one capability to not import. Restated over all ten."""
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
    for name in CAPABILITY_NAMES:
        assert f"okf_ext.{name}" not in result.stdout


def test_the_second_capability_is_reachable_as_a_submodule():
    from okf_ext import schemas

    assert schemas.DEFAULT_IGNORE == ("schema/*", "*/schema/*")


def test_the_third_capability_is_reachable_as_a_submodule():
    from okf_ext import search

    assert search.DEFAULT_WEIGHTS == {"title": 3, "description": 2, "tags": 2, "body": 1}


def test_the_fourth_capability_is_reachable_as_a_submodule():
    from okf_ext import tables

    assert callable(tables.read_section)


def test_the_fifth_capability_is_reachable_as_a_submodule():
    from okf_ext import sections

    assert sections.DEFAULT_IGNORE == ("sections/*", "*/sections/*")


def test_the_sixth_capability_is_reachable_as_a_submodule():
    from okf_ext import proposals

    assert proposals.PROPOSAL_TYPE == "Proposal"


def test_the_seventh_capability_is_reachable_as_a_submodule():
    from okf_ext import logs

    assert callable(logs.append_entry)
