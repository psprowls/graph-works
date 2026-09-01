"""Fixture discovery shared by every test module."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from okf_io import _yaml

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
