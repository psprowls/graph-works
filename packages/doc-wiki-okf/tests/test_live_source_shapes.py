"""The live source-page shape, validated against the shipped `Source` schema.

The frontmatter below is copied from a real `wiki/sources/` page with two keys
added -- `type: Source` and `description` -- which is exactly what the live-vault
migration will stamp. Inlined rather than read from the vault, following
`test_live_proposal_shapes.py`: this suite depends on nothing outside the repo.

What it keeps honest is S-J's measured claim that requiring `source_path` costs
exactly one of 237 live pages at the level of key *presence*. If a future schema
edit widens the required set, this fails.

`source_type: spec` below is deliberate and is **not** a stale copy of the key
K-A renamed. Live pages keep it until the live-vault migration rewrites them;
until then it is an undeclared extra property, and this suite is what asserts
`additionalProperties: true` still tolerates it.
"""

from __future__ import annotations

import importlib.resources
from datetime import date
from pathlib import Path

from okf_ext.schemas import load_schemas, schema_rule
from okf_io import load_bundle
from okf_io import validate as okf_validate

#: A live `category: source` page's frontmatter, with the two keys the
#: migration adds. `summary` is kept alongside `description` because
#: `additionalProperties: true` must tolerate the vault keys this package does
#: not own -- 18 pages carry `last_sync_commit`, 8 carry `source_url`.
LIVE_FRONTMATTER = """type: Source
title: Design Spec — Scaffold okf-ext and Tag Management
description: The spec that settled the write-staging protocol.
summary: The spec that settled the write-staging protocol.
source_type: spec
source_path: sources/references/2026-08-design-spec-scaffold-okf-ext.md
origin: raw/specs/design-spec-scaffold-okf-ext.md
ingested: '2026-08-04'
last_sync_commit: 3ef847b2
source_url: https://example.invalid/spec
tags:
  - okf-ext
  - tags
"""

LIVE_BODY = """## TL;DR

The one-paragraph serialize-then-write model.

## Key claims

- Staging precedes any live write.

## Touches

- `okf_ext.writing`
"""


def _schema_set():
    return load_schemas(str(importlib.resources.files("doc_wiki_okf") / "assets" / "schema"))


def _codes(tmp_path: Path, frontmatter: str) -> list[str]:
    root = tmp_path / "bundle"
    (root / "sources").mkdir(parents=True, exist_ok=True)
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# bundle\n", encoding="utf-8")
    (root / "sources" / "2026-08-a.md").write_text(f"---\n{frontmatter}---\n\n{LIVE_BODY}", encoding="utf-8")
    report = okf_validate(load_bundle(root), today=date(2026, 8, 12), extra_rules=[schema_rule(_schema_set())])
    return sorted(finding.code for finding in report.findings if finding.code.startswith("schemas."))


def test_the_live_shape_validates_once_type_and_description_are_present(tmp_path: Path) -> None:
    assert _codes(tmp_path, LIVE_FRONTMATTER) == []


def test_the_one_live_page_with_no_source_path_is_the_measured_cost(tmp_path: Path) -> None:
    """S-J: requiring `source_path` fails exactly one of the 237 live pages."""
    without = "".join(line + "\n" for line in LIVE_FRONTMATTER.splitlines() if not line.startswith("source_path:"))
    assert _codes(tmp_path, without) == ["schemas.invalid"]


def test_a_page_still_carrying_only_summary_fails_on_description(tmp_path: Path) -> None:
    """S-N and hand-off 2: after `type: Source` lands, a missing `description`
    becomes a schema error rather than an okf-io warning. 240 live pages carry
    `summary` and none carries `description`."""
    without = "".join(line + "\n" for line in LIVE_FRONTMATTER.splitlines() if not line.startswith("description:"))
    assert _codes(tmp_path, without) == ["schemas.invalid"]
