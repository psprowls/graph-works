"""§ADR-0027's canonical-collision hazard, synthesized. Two on-disk members
whose names are NFC-equal but byte-different cannot be committed as real
sibling files and checked out on a normalization-folding filesystem (macOS's
default APFS among them) -- so this rule's collision fixture is a `Bundle`
built with `dataclasses.replace`, not real files. See `test_catalog.py`'s
`NOT_REPRODUCIBLE_FROM_DISK` for the golden-corpus side of the same
constraint."""

from __future__ import annotations

import dataclasses
import unicodedata
from datetime import date
from pathlib import Path
from types import MappingProxyType

from helpers import write
from okf_io import bundle
from okf_io.validate import validate

TODAY = date(2026, 8, 3)


def _write(tmp_path: Path, rel: str, text: str) -> None:
    write(tmp_path / rel, text)


def test_no_collisions_fires_nothing(tmp_path):
    _write(tmp_path, "a.md", "---\ntype: Metric\ntitle: T\ndescription: D\n---\n\n# D\n")
    report = validate(bundle.load(tmp_path), today=TODAY)
    assert report.by_code("identity.canonical-collision") == ()
    assert report.ok is True


def test_a_collision_is_an_error_naming_every_raw_id(tmp_path):
    _write(tmp_path, "a.md", "---\ntype: Metric\ntitle: T\ndescription: D\n---\n\n# D\n")
    loaded = bundle.load(tmp_path)
    nfd = unicodedata.normalize("NFD", "café")
    nfc = unicodedata.normalize("NFC", "café")
    synthetic = dataclasses.replace(
        loaded,
        canonical_collisions=MappingProxyType({f"concepts/{nfc}.md": (f"concepts/{nfd}.md", f"concepts/{nfc}.md")}),
    )
    report = validate(synthetic, today=TODAY)
    finding = report.by_code("identity.canonical-collision")[0]
    assert finding.severity == "error"
    assert finding.path == f"concepts/{nfc}.md"
    escaped_nfd = f"concepts/{nfd}.md".encode("unicode_escape").decode("ascii")
    assert escaped_nfd in finding.message
    assert f"concepts/{nfd}.md" not in finding.message  # escaped, not the raw NFD form
    assert report.ok is False
