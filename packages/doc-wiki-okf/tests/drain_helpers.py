"""A tiny wiki with one ADR, one Explanation, one Reference and Sources citing them."""

from __future__ import annotations

from pathlib import Path

from okf_io import Bundle, load_bundle

#: Shaped like `entry_keys_from(seed schema set)`: tests pass this map rather
#: than literals inside the code under test.
ENTRY_KEYS = {"Adr": "decisions", "Explanation": "claims"}

ADR = """---
type: Adr
title: A
description: d
status: accepted
decisions:
  - id: D1
    claim: One.
  - id: D2
    claim: Two.
sources:
  - id: s
    resource: /sources/2026-09-s.md
---

## Decision
d
"""

EXPLANATION = """---
type: Explanation
title: E
description: d
claims:
  - id: C1
    claim: One.
sources:
  - id: s
    resource: sources/2026-09-s.md#top
---

## Context
d
"""

REFERENCE = """---
type: Reference
title: R
description: d
claims:
  - id: C2
    claim: Two.
sources:
  - id: s
    resource: /sources/2026-09-s.md
---

## Facts
d
"""


def source(
    *,
    drain: str = "",
    claims: str = "1. One.\n   - nested\n2. Two.\n3. Three.\n",
    cited: str = "- [A](/adrs/a.md)\n",
) -> str:
    return (
        "---\ntype: Source\ntitle: S\ndescription: d\nsource_path: sources/references/2026-09-s.md\n"
        f"{drain}---\n\n## TL;DR\nt\n\n## Key claims\n{claims}\n## Where it's cited in this wiki\n{cited}"
    )


def build(root: Path, members: dict[str, str]) -> Bundle:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# b\n", encoding="utf-8")
    for member, text in members.items():
        target = root / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return load_bundle(root)


def wiki(root: Path, source_text: str, **extra: str) -> Bundle:
    return build(
        root,
        {
            "adrs/a.md": ADR,
            "docs/explanations/e.md": EXPLANATION,
            "docs/reference/r.md": REFERENCE,
            "sources/2026-09-s.md": source_text,
            **extra,
        },
    )
