"""Fixture discovery shared by every test module."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from okf_io import _yaml
from okf_io import bundle as _bundle
from okf_io.document import Document

FIXTURES = Path(__file__).parent / "fixtures"
BUNDLES = FIXTURES / "bundles"
LEGACY = FIXTURES / "legacy"
EDGE = FIXTURES / "edge"

#: Fixtures that are deliberately unparseable.
MALFORMED = frozenset(
    {
        "malformed_unterminated.md",
        "malformed_yaml.md",
        "malformed_not_mapping.md",
    }
)


def read(path: Path) -> str:
    """Read without newline translation, so CRLF and BOM survive."""
    return path.read_bytes().decode("utf-8")


def write(path: Path, text: str) -> None:
    """Write without newline translation, so the string's own endings survive.

    The write half of `read`, and the reason it exists: `Path.write_text` with
    no `newline=` translates every LF to `os.linesep`, so on Windows a fixture
    written from an LF string lands as CRLF. The document layer then reads the
    file's real bytes and the round-trip assertion compares that CRLF payload
    against an LF `read_text`, which un-translates on the way back in. Every
    fixture writer in this suite goes through here.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def write_tree(root: Path, files: Mapping[str, str]) -> Path:
    """Write a `{bundle-relative posix path: text}` mapping under *root*.

    The loop that thirteen modules had each re-implemented inline, each copy
    re-introducing the translating default. Returns *root* so callers can
    write `bundle.load(write_tree(tmp_path, files))`.
    """
    for relative, text in files.items():
        write(root / relative, text)
    return root


def load_tree_admitting_unwritable_names(root: Path, files: Mapping[str, str]) -> _bundle.Bundle:
    """Load *files* as a `Bundle`, admitting names the host filesystem cannot hold.

    A member the host cannot write is not skipped: it is injected into the loaded
    `Bundle` as a virtual member -- the same object `bundle._load` would have produced
    from that text -- so a test keyed on that member's content keeps running on a host
    where the name itself is unwritable. This is a probe, not a predicate (D-041): the
    helper attempts the write and treats `OSError` as "this host cannot hold this
    name," rather than encoding a second, driftable copy of `graph-works-core`'s
    name-shape rules -- which okf-io, a band-1 package, may not import anyway.

    Two refusals, both raising `ValueError`:
    - a member that could not be written and is not ASCII (D-045) -- an injected
      non-ASCII member would desynchronise `Bundle._canonical`, which this helper does
      not maintain; the NFC/NFD collision case has its own dedicated fixture.
    - a member that WAS written but whose id the walk disagrees with (D-047) -- proof
      the host silently mangled the name (e.g. stripping a trailing dot or space)
      rather than failing loudly, which would otherwise leave both a stray real file
      and an injected virtual member claiming the same id.
    """
    unwritable: dict[str, str] = {}
    for relative, text in files.items():
        try:
            write(root / relative, text)
        except OSError:
            unwritable[relative] = text

    loaded = _bundle.load(root)

    for relative in files:
        if relative in unwritable:
            continue
        if loaded.member_id(relative) != relative:
            raise ValueError(f"{relative!r} was written but the walk silently mangled it (D-047)")

    concepts = dict(loaded.concepts)
    indexes = dict(loaded.indexes)
    logs = dict(loaded.logs)
    assets = set(loaded.assets)

    for relative, text in unwritable.items():
        if not relative.isascii():
            raise ValueError(f"{relative!r} is not ASCII and cannot be admitted as a virtual member (D-045)")
        pure = PurePosixPath(relative)
        directory = "" if pure.parent.as_posix() == "." else pure.parent.as_posix()
        if pure.name == _bundle.INDEX_NAME:
            indexes[directory] = Document.parse(text)
        elif pure.name == _bundle.LOG_NAME:
            logs[directory] = Document.parse(text)
        elif pure.suffix == ".md":
            concepts[relative[: -len(".md")]] = Document.parse(text)
        else:
            assets.add(relative)

    return dataclasses.replace(
        loaded,
        concepts=MappingProxyType(dict(sorted(concepts.items()))),
        indexes=MappingProxyType(dict(sorted(indexes.items()))),
        logs=MappingProxyType(dict(sorted(logs.items()))),
        assets=frozenset(assets),
    )


def all_concept_files() -> list[Path]:
    """Every `.md` fixture: both vendored bundles, the v0.1 corpus, every edge case."""
    return sorted(BUNDLES.rglob("*.md")) + sorted(LEGACY.rglob("*.md")) + sorted(EDGE.rglob("*.md"))


def legacy_files() -> list[Path]:
    """Every v0.1 fixture: the vendored corpus plus the hand-built edge cases.

    The migration acceptance properties run over exactly this set. Edge cases
    are selected by the `legacy_` name prefix rather than listed, so adding a
    regression fixture enrolls it in the properties automatically -- which is
    the behaviour you want from a corpus whose whole job is to be walked.
    """
    return sorted(LEGACY.rglob("*.md")) + sorted(EDGE.rglob("legacy_*.md"))


def has_frontmatter(path: Path) -> bool:
    """Ask the splitter rather than re-deriving the answer.

    A local `lstrip("\\ufeff").startswith("---")` drifts: `lstrip` removes
    *every* leading BOM while the splitter removes exactly one, so the two
    disagree on a doubled-BOM file. This helper selects which fixtures the
    acceptance properties run against, so it must not disagree with the code
    under test about what counts as having frontmatter.
    """
    return _yaml.split(read(path)).has_frontmatter


def well_formed_files() -> list[Path]:
    return [p for p in all_concept_files() if p.name not in MALFORMED]


def mutable_files() -> list[Path]:
    """Well-formed fixtures that already carry a frontmatter block."""
    return [p for p in well_formed_files() if has_frontmatter(p)]


def fixture_id(path: Path) -> str:
    return str(path.relative_to(FIXTURES))


#: The hand-built whole-catalog corpus. **Deliberately not under `BUNDLES`**:
#: `all_concept_files()` feeds a prior child's acceptance properties, which assume
#: every file decodes as UTF-8 -- and one of these does not. `bundles/` is also
#: documented in FIXTURES.md as vendored verbatim, which this is not.
NONCONFORMANT = FIXTURES / "nonconformant"

#: Its reviewed expected output.
GOLDEN = FIXTURES / "nonconformant.golden.txt"


def render_finding(finding) -> str:
    """One finding as one reviewable line.

    Rendering lives in the tests, not in the package: okf-io reports findings,
    it does not format them, and inventing an output format inside the library
    would be the first step toward it owning one.
    """
    where = finding.path or "-"
    if finding.line is not None:
        where = f"{where}:{finding.line}"
    return f"{finding.severity}\t{finding.code}\t{where}\t{finding.spec}\t{finding.message}"
