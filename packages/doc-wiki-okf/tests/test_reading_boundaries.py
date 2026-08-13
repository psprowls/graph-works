"""`reading/` imports the standard library and itself, and nothing else.

Modelled on `packages/okf-ext/tests/test_ext_boundaries.py`, and on the two
lessons that file's docstring draws from its own earlier version passing while
a fabricated violation sat in the tree:

**The module set is derived from disk.** `READING.rglob("*.py")`, never an
enumerated list — a `.py` file added under `reading/` in six months is covered
the moment it exists, with no companion edit anyone can forget.

**The allowlist is derived from the interpreter.** `sys.stdlib_module_names`,
not a hand-maintained tuple that drifts as the port grows.

And the check rejects `doc_wiki_okf.<anything but reading>` as well as
non-stdlib third parties: that is the seam that keeps `reading/` from reaching
sideways into brief assembly once it exists, and it is the half a stdlib-only
check would miss entirely.

`test_the_checker_flags_a_planted_import` is why the negative cases are here:
without them a checker with an inverted condition reports green forever.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

READING = Path(__file__).resolve().parents[1] / "src" / "doc_wiki_okf" / "reading"
INGEST = Path(__file__).resolve().parents[1] / "src" / "doc_wiki_okf" / "ingest"

#: The only `doc_wiki_okf.*` prefix `reading/` may import.
INTERNAL = "doc_wiki_okf.reading"

#: `ingest/` may reach `reading/` as well as itself, and nothing else.
INGEST_INTERNAL = ("doc_wiki_okf.ingest", "doc_wiki_okf.reading")


def reading_modules() -> list[Path]:
    return sorted(READING.rglob("*.py"))


def ingest_modules() -> list[Path]:
    return sorted(INGEST.rglob("*.py"))


def is_allowed(module: str, internal: tuple[str, ...] = (INTERNAL,)) -> bool:
    top = module.split(".")[0]
    if top == "doc_wiki_okf":
        return any(module == prefix or module.startswith(prefix + ".") for prefix in internal)
    return top in sys.stdlib_module_names


def offenders_in(source: str, module_id: str, internal: tuple[str, ...] = (INTERNAL,)) -> list[str]:
    """Every import in `source` that this subpackage is not allowed to make."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            # `level > 0` is a relative intra-package import. Allowed.
            if node.level == 0 and node.module is not None and not is_allowed(node.module, internal):
                found.append(f"{module_id}:{node.lineno} from {node.module} import ...")
        elif isinstance(node, ast.Import):
            found.extend(
                f"{module_id}:{node.lineno} import {alias.name}"
                for alias in node.names
                if not is_allowed(alias.name, internal)
            )
    return found


def test_the_reading_subpackage_exists() -> None:
    assert reading_modules(), "no modules found under reading/; the layout moved"


def test_reading_imports_the_stdlib_and_itself_only() -> None:
    offenders: list[str] = []
    for path in reading_modules():
        module_id = path.relative_to(READING).as_posix()
        offenders.extend(offenders_in(path.read_text(encoding="utf-8"), module_id))
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize(
    "source",
    [
        "import okf_io\n",
        "from okf_io import load_bundle\n",
        "import okf_ext.proposals\n",
        "from okf_ext.proposals import plan_propose\n",
        "import typer\n",
        "import doc_wiki_okf\n",
        "from doc_wiki_okf import __version__\n",
        "from doc_wiki_okf.briefs import build_ingest_brief\n",
        "import doc_wiki_okf.cli\n",
    ],
)
def test_the_checker_flags_a_planted_import(source: str) -> None:
    """A checker with an inverted condition reports green forever. This is the
    case `test_ext_boundaries.py` documents having been missed once already."""
    assert offenders_in(source, "planted.py")


@pytest.mark.parametrize(
    "source",
    [
        "import json\n",
        "import html.parser\n",
        "from pathlib import Path\n",
        "from dataclasses import dataclass\n",
        "from doc_wiki_okf.reading.links import iter_link_targets\n",
        "import doc_wiki_okf.reading.slug\n",
        "from . import slug\n",
        "from .links import resolve_companion\n",
    ],
)
def test_the_checker_accepts_stdlib_and_intra_package_imports(source: str) -> None:
    assert not offenders_in(source, "planted.py")


def test_the_allowlist_comes_from_the_interpreter() -> None:
    """Not a hand-maintained tuple. 3.12 ships ~300 names; the assertion is on
    the mechanism, not the count."""
    assert "json" in sys.stdlib_module_names
    assert "okf_io" not in sys.stdlib_module_names


def test_the_ingest_subpackage_exists() -> None:
    assert ingest_modules(), "no modules found under ingest/; the layout moved"


def test_ingest_imports_the_stdlib_and_reading_only() -> None:
    """Design spec §7. `ingest/` may import the standard library and `reading/`.
    It imports neither `diataxis/` nor `proposals/`: no Diátaxis type touches a
    brief, and proposals are filed by a layer that has both, not by a brief."""
    offenders: list[str] = []
    for path in ingest_modules():
        module_id = path.relative_to(INGEST).as_posix()
        offenders.extend(offenders_in(path.read_text(encoding="utf-8"), module_id, INGEST_INTERNAL))
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize(
    "source",
    [
        "import okf_io\n",
        "from okf_ext.proposals import plan_propose\n",
        "import typer\n",
        "from doc_wiki_okf.diataxis import classify\n",
        "from doc_wiki_okf.proposals import plan_file\n",
        "import doc_wiki_okf.cli\n",
    ],
)
def test_the_ingest_checker_flags_a_planted_import(source: str) -> None:
    assert offenders_in(source, "planted.py", INGEST_INTERNAL)


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\n",
        "from types import MappingProxyType\n",
        "from doc_wiki_okf.reading import extract, slugify\n",
        "from doc_wiki_okf.ingest.layout import IngestLayout\n",
        "from .layout import GRAPH_WIKI_LAYOUT\n",
    ],
)
def test_the_ingest_checker_accepts_stdlib_and_reading(source: str) -> None:
    assert not offenders_in(source, "planted.py", INGEST_INTERNAL)


def test_reading_may_not_import_ingest() -> None:
    """The seam runs one way. `reading/` reaching into brief assembly is the
    case the reading contract's own docstring named before `ingest/` existed."""
    assert offenders_in("from doc_wiki_okf.ingest import plan_folder_brief\n", "planted.py")
