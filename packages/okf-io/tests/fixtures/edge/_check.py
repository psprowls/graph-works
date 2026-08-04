"""Standalone sanity check for the hand-built edge fixtures.

Not a test module — it runs before okf_io exists. It asserts the fixtures are
what they claim to be, so a later failure is a library bug, not a typo here.
"""

from __future__ import annotations

import sys
from pathlib import Path

EDGE = Path(__file__).parent

EXPECTED = [
    "verified_bare_mapping.md",
    "verified_list.md",
    "dates_preparsed.md",
    "dates_quoted.md",
    "keyword_keys.md",
    "dialect_block.md",
    "dialect_indented.md",
    "dialect_flow.md",
    "links_mixed.md",
    "footnotes_join.md",
    "legacy_timestamp.md",
    "legacy_citations.md",
    "malformed_unterminated.md",
    "malformed_yaml.md",
    "malformed_not_mapping.md",
    "no_frontmatter.md",
    "empty_frontmatter.md",
    "dashes_in_scalar.md",
    "body_delimiters.md",
    "names/events_.md",
    "names/a___b.md",
    "encoding/bom.md",
    "encoding/crlf.md",
    "encoding/no_trailing_newline.md",
    "nonmarkdown/viz.html",
    "nonmarkdown/attester.py",
]


def main() -> int:
    missing = [name for name in EXPECTED if not (EDGE / name).exists()]
    if missing:
        print(f"MISSING: {missing}", file=sys.stderr)
        return 1

    for path in EDGE.rglob("*"):
        if path.is_file() and path.name != "_check.py":
            # A fixture emptied by a bad edit or merge still decodes as UTF-8, so
            # check for it explicitly: otherwise the regression it guards silently
            # stops being guarded and only surfaces as a confusing failure later.
            if path.stat().st_size == 0:
                print(f"EMPTY: {path}", file=sys.stderr)
                return 1
            try:
                path.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                print(f"NOT UTF-8: {path}", file=sys.stderr)
                return 1

    bom = (EDGE / "encoding/bom.md").read_bytes()
    if not bom.startswith(b"\xef\xbb\xbf"):
        print("bom.md has no BOM", file=sys.stderr)
        return 1

    crlf = (EDGE / "encoding/crlf.md").read_bytes()
    if b"\r\n" not in crlf or crlf.replace(b"\r\n", b"") .count(b"\n"):
        print("crlf.md is not uniformly CRLF", file=sys.stderr)
        return 1

    tail = (EDGE / "encoding/no_trailing_newline.md").read_bytes()
    if tail.endswith(b"\n"):
        print("no_trailing_newline.md ends with a newline", file=sys.stderr)
        return 1

    print("edge fixtures OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
