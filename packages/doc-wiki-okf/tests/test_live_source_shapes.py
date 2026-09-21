"""The current source-page shape, validated against the shipped `Source` schema.

What it keeps honest is that requiring `source_path` costs exactly the pages that
lack it, that `description` is required, and that the schema is strict: the
graph-wiki-era vault keys (`summary`, `source_type`, `last_sync_commit`,
`source_url`) are retired, so a page still carrying one is a finding rather than
silently tolerated.
"""

from __future__ import annotations

import importlib.resources
from datetime import date
from pathlib import Path

from okf_ext.schemas import load_schemas, schema_rule
from okf_io import load_bundle
from okf_io import validate as okf_validate

#: A current `type: Source` page's frontmatter, as `gw ingest` writes it.
LIVE_FRONTMATTER = """type: Source
title: Design Spec — Scaffold okf-ext and Tag Management
description: The spec that settled the write-staging protocol.
source_kind: spec
source_path: sources/references/2026-08-design-spec-scaffold-okf-ext.md
origin: raw/specs/design-spec-scaffold-okf-ext.md
ingested: '2026-08-04'
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
    (root / "sources" / "references").mkdir(parents=True, exist_ok=True)
    (root / "sources" / "references" / "2026-08-design-spec-scaffold-okf-ext.md").write_text(
        "# Design spec\n", encoding="utf-8", newline=""
    )
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# bundle\n", encoding="utf-8")
    (root / "sources" / "2026-08-a.md").write_text(f"---\n{frontmatter}---\n\n{LIVE_BODY}", encoding="utf-8")
    report = okf_validate(load_bundle(root), today=date(2026, 8, 12), extra_rules=[schema_rule(_schema_set())])
    return sorted(finding.code for finding in report.findings if finding.code.startswith("schemas."))


def test_the_current_shape_validates(tmp_path: Path) -> None:
    assert _codes(tmp_path, LIVE_FRONTMATTER) == []


def test_the_one_live_page_with_no_source_path_is_the_measured_cost(tmp_path: Path) -> None:
    """Requiring `source_path` fails exactly the page that lacks it."""
    without = "".join(line + "\n" for line in LIVE_FRONTMATTER.splitlines() if not line.startswith("source_path:"))
    assert _codes(tmp_path, without) == ["schemas.invalid"]


def test_a_page_still_carrying_only_summary_fails_on_description(tmp_path: Path) -> None:
    """A missing `description` is a schema error, not an okf-io warning: the retired
    `summary` key does not stand in for it."""
    without = "".join(line + "\n" for line in LIVE_FRONTMATTER.splitlines() if not line.startswith("description:"))
    # Two findings: the missing `description`, and the undeclared `summary` that does not replace it.
    assert set(_codes(tmp_path, without + "summary: A summary.\n")) == {"schemas.invalid"}


def test_a_retired_vault_key_is_a_finding(tmp_path: Path) -> None:
    for retired in ("source_type: spec", "last_sync_commit: 3ef847b2", "source_url: https://example.invalid/spec"):
        assert _codes(tmp_path, LIVE_FRONTMATTER + retired + "\n") == ["schemas.invalid"], retired
