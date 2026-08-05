"""Fixture discovery for the okf-ext suite.

Named `ext_helpers` rather than `helpers`: both test directories are on
`pythonpath`, and okf-io's `tests/helpers.py` already owns that module name.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from okf_io import Bundle, load_bundle

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TAGGED = FIXTURES / "tagged"
VOCABULARY = TAGGED / "_tags.yaml"
BAD_VERSION = FIXTURES / "bad_vocab_version.yaml"
BAD_DANGLING = FIXTURES / "bad_vocab_dangling.yaml"

#: Concepts the tag functions cannot read, and why. Tests assert against this
#: rather than restating the reasons, so a fixture change cannot leave a test
#: quietly asserting the old corpus.
UNUSABLE = {"broken": "parse-error", "scalar_tags": "tags-not-a-sequence"}


def read(path: Path) -> str:
    """Read without newline translation, so CRLF and a BOM would survive."""
    return path.read_bytes().decode("utf-8")


def tagged_bundle() -> Bundle:
    """The read-only corpus. Never pass this to `apply()`."""
    return load_bundle(TAGGED)


def bundle_copy(tmp_path: Path) -> Path:
    """A writable byte-identical copy of the corpus.

    Every mutating test goes through this. `copy2` preserves bytes, which the
    round-trip differential depends on.
    """
    target = tmp_path / "tagged"
    shutil.copytree(TAGGED, target, copy_function=shutil.copy2)
    return target


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file under *root*, keyed by relative posix path."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }
