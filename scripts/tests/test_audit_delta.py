"""Acceptance tests for `scripts/audit_delta.py`.

Outside the repo's `testpaths` and coverage `source` list on purpose: `scripts/`
is repo tooling, not a package, so these do not move the 95% gate. Run them
with `uv run pytest scripts/tests`.

The parsing cases are drawn from the real `plugins/PATCHES.md`, including the
one that bites: entry #0 documents the block format *inside a fenced code
block*, so a naive regex over the whole file finds a block that is not a claim.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit_delta import (  # noqa: E402
    Entry,
    LedgerError,
    compare,
    last_split_sha,
    parse_base,
    parse_entries,
    strip_fenced,
)

import pytest  # noqa: E402


LEDGER = """\
# Patches

## How to read an entry

```
<!-- audit-delta
state: patched
file: docs/example.md
-->
```

## Entry #1

<!-- audit-delta-base
base: v5.5.0
theirs: v6.4.0
grafted-roots: commands/ hooks/ skills/
-->

## Entry #2

<!-- audit-delta
state: patched
file: .claude-plugin/plugin.json
-->

## Entry #3

<!-- audit-delta
state: planned
file: hooks/hooks.json
file: hooks/session-start
-->
"""


def test_fenced_blocks_are_not_claims():
    assert "docs/example.md" not in strip_fenced(LEDGER)


def test_parse_entries_skips_the_documented_example():
    entries = parse_entries(LEDGER)
    files = [f for e in entries for f in e.files]
    assert "docs/example.md" not in files
    assert files == [".claude-plugin/plugin.json", "hooks/hooks.json", "hooks/session-start"]


def test_parse_entries_reads_state():
    entries = parse_entries(LEDGER)
    assert [e.state for e in entries] == ["patched", "planned"]


def test_parse_base():
    base = parse_base(LEDGER)
    assert base.base == "v5.5.0"
    assert base.grafted_roots == ("commands/", "hooks/", "skills/")


def test_parse_base_missing_is_an_error():
    with pytest.raises(LedgerError):
        parse_base("# Patches\n\nno block here\n")


def test_last_split_sha_takes_the_last_ledger_row():
    sync = (
        "| Date | Upstream tag | `git-subtree-split` | Our merge commit |\n"
        "|---|---|---|---|\n"
        "| 2026-08-11 | v6.4.0 | `" + "a" * 40 + "` | `622c1d8` |\n"
        "| 2026-09-01 | v6.5.0 | `" + "b" * 40 + "` | `deadbee` |\n"
    )
    assert last_split_sha(sync) == "b" * 40


def test_last_split_sha_with_no_row_is_an_error():
    with pytest.raises(LedgerError):
        last_split_sha("| Date | Tag |\n|---|---|\n")


def test_compare_flags_undocumented_divergence():
    report = compare(
        divergent={"skills/brainstorming/SKILL.md"},
        entries=[Entry(state="patched", files=[".claude-plugin/plugin.json"])],
        grafted=set(),
    )
    assert report.undocumented == ["skills/brainstorming/SKILL.md"]
    assert report.retired == [".claude-plugin/plugin.json"]


def test_compare_is_clean_when_claims_match_the_tree():
    report = compare(
        divergent={".claude-plugin/plugin.json"},
        entries=[Entry(state="patched", files=[".claude-plugin/plugin.json"])],
        grafted=set(),
    )
    assert report.ok
    assert report.undocumented == [] and report.retired == []


def test_planned_entries_do_not_count_as_claims():
    report = compare(
        divergent=set(),
        entries=[Entry(state="planned", files=["hooks/hooks.json"])],
        grafted=set(),
    )
    assert report.ok
    assert report.planned == ["hooks/hooks.json"]


def test_removed_entries_cover_without_claiming():
    """A deleted grafted file is neither a retired patch nor a coverage gap."""
    report = compare(
        divergent=set(),
        entries=[Entry(state="removed", files=["commands/gate-check.md"])],
        grafted={"commands/gate-check.md"},
    )
    assert report.ok
    assert report.retired == []
    assert report.uncovered == []


def test_compare_flags_uncovered_and_duplicated_grafted_files():
    report = compare(
        divergent=set(),
        entries=[
            Entry(state="verbatim", files=["skills/a/SKILL.md"]),
            Entry(state="planned", files=["skills/a/SKILL.md"]),
        ],
        grafted={"skills/a/SKILL.md", "skills/b/SKILL.md"},
    )
    assert report.uncovered == ["skills/b/SKILL.md"]
    assert report.duplicated == ["skills/a/SKILL.md"]
    assert not report.ok
