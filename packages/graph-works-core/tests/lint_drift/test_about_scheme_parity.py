"""Only core imports both doc-wiki-okf and code-wiki-okf, so only core can
assert the seed's `about:` scheme list and the scanner's resource prefixes
agree. A new code-wiki type without a matching scheme would make every
`about:` naming it a `schemas.invalid`."""

from __future__ import annotations

import importlib.resources
import json
import re

from code_wiki_okf.placement import _RESOURCE_PREFIXES


def test_the_about_scheme_pattern_names_exactly_the_scanner_prefixes() -> None:
    base = importlib.resources.files("doc_wiki_okf") / "assets" / "schema" / "_base-diataxis.schema.json"
    schema = json.loads(base.read_text(encoding="utf-8"))
    pattern = schema["$defs"]["about"]["items"]["pattern"]
    match = re.fullmatch(r"\^\(([^)]*)\):\\S\.\*\$", pattern)
    assert match is not None, pattern
    assert sorted(match.group(1).split("|")) == sorted(_RESOURCE_PREFIXES.values())
