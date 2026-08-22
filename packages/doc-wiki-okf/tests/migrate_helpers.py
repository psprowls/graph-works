"""The 13-proposal fixture corpus, and the tmp_path bundle built from it.

The fixtures are the live ledger at `$GRAPH_WIKI_WORKSPACE/wiki/proposals/`,
copied **byte-exact** and marked `-text` in `.gitattributes` -- okf-io's own
handling of the same problem, for the same reason: `test_migrate_fixtures.py`
asserts every migrated body is byte-identical to the fixture it came from, and
EOL normalization would break that regression silently.

Files, not Python string literals. `proposal_helpers.LIVE_SOURCES` inlines live
data and is right to -- there the point is the *shape* of a handful of
`sources[]` entries. Here the point is the **bytes**, and "byte-exact" is a much
weaker claim inside a literal where escapes, editor reflow and formatters all
get a vote.

Each test copies the corpus into its own `tmp_path` bundle rather than
migrating in place, so the originals stay byte-exact and the suite has no
ordering dependency.
"""

from __future__ import annotations

from pathlib import Path

from okf_io import Bundle, load_bundle
from proposal_helpers import seeded_root

#: `schema/` and `sections/` are declarations, not concepts -- `cli.IGNORE`,
#: restated here so the helper does not import the CLI.
IGNORE = ("schema/*", "*/schema/*", "sections/*", "*/sections/*")

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "proposals"


def fixture_paths() -> tuple[Path, ...]:
    """Every fixture, sorted -- 11 at the top level plus 2 under `_archive/`."""
    return tuple(sorted(FIXTURES.rglob("*.md")))


def fixture_member(path: Path) -> str:
    """*path* as the bundle-relative posix member it becomes in a copy."""
    return f"proposals/{path.relative_to(FIXTURES).as_posix()}"


def fixture_bytes() -> dict[str, bytes]:
    """Member path -> the fixture's original bytes, for a byte-exactness assertion."""
    return {fixture_member(path): path.read_bytes() for path in fixture_paths()}


def fixture_bundle(root: Path) -> Bundle:
    """A seeded bundle at *root* carrying the 13 fixtures under `proposals/`.

    Copied with `read_bytes`/`write_bytes`, never `read_text`, so a CRLF
    dialect or a BOM survives the copy exactly as it survives the migration.
    """
    seeded_root(root)
    for path in fixture_paths():
        target = root / fixture_member(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    return load_bundle(root, ignore=IGNORE)


def reload_bundle(root: Path) -> Bundle:
    """*root* re-walked. Required between a rewrite and a move: neither
    `okf_ext.proposals.apply` nor `okf_ext.moves.apply` updates the in-memory
    `Bundle`, and both document that the caller reloads."""
    return load_bundle(root, ignore=IGNORE)
