from __future__ import annotations

import re
from datetime import date

import pytest
from graph_works_core.tag_policy.disposition import DispositionError, load, loads, render
from graph_works_core.tag_policy.model import Disposition, TagVerdict

TODAY = date(2026, 9, 8)


def make(*verdicts: TagVerdict) -> Disposition:
    return Disposition(generated=TODAY, total_tags=len(verdicts), tagged_pages=432, verdicts=verdicts)


def test_render_groups_by_verdict_and_round_trips():
    original = make(
        TagVerdict(tag="lint", uses=40, verdict="keep", reason="survivor"),
        TagVerdict(tag="okf-io", uses=17, verdict="strip", reason="entity-dup"),
        TagVerdict(tag="portability", uses=117, verdict="keep", reason="survivor"),
        TagVerdict(tag="wiki-lint", uses=5, verdict="merge", reason="semantic", into="lint"),
    )
    text = render(original)
    assert "keep:" in text and "merge:" in text and "strip:" in text
    assert loads(text, "t.yaml") == original


def test_render_ends_with_a_newline_and_uses_no_carriage_returns():
    text = render(make(TagVerdict(tag="a", uses=9, verdict="keep", reason="survivor")))
    assert text.endswith("\n")
    assert "\r" not in text


def test_render_carries_the_generation_header():
    text = render(make(TagVerdict(tag="a", uses=9, verdict="keep", reason="survivor")))
    assert "2026-09-08" in text
    assert "432" in text


def test_a_merge_needs_a_target():
    with pytest.raises(DispositionError, match="`into`"):
        loads("generated: 2026-09-08\ntagged_pages: 4\nmerge:\n  - {tag: a, uses: 5, reason: semantic}\n", "t.yaml")


def test_a_merge_target_must_itself_be_kept():
    text = (
        "generated: 2026-09-08\ntagged_pages: 4\n"
        "keep:\n  - {tag: b, uses: 9, reason: survivor}\n"
        "merge:\n  - {tag: a, uses: 5, reason: semantic, into: gone}\n"
    )
    with pytest.raises(DispositionError, match="not kept"):
        loads(text, "t.yaml")


def test_a_keep_or_strip_may_not_carry_a_target():
    text = "generated: 2026-09-08\ntagged_pages: 4\nkeep:\n  - {tag: a, uses: 9, reason: survivor, into: b}\n"
    with pytest.raises(DispositionError, match="only meaningful"):
        loads(text, "t.yaml")


def test_a_tag_declared_twice_is_refused():
    text = (
        "generated: 2026-09-08\ntagged_pages: 4\n"
        "keep:\n  - {tag: a, uses: 9, reason: survivor}\n"
        "strip:\n  - {tag: a, uses: 9, reason: below-floor}\n"
    )
    with pytest.raises(DispositionError, match="declared twice"):
        loads(text, "t.yaml")


def test_an_unknown_key_is_refused_top_level_and_per_entry():
    with pytest.raises(DispositionError, match="top-level"):
        loads("generated: 2026-09-08\ntagged_pages: 4\nkept:\n  - {tag: a, uses: 1, reason: survivor}\n", "t.yaml")
    with pytest.raises(DispositionError, match="entry"):
        loads(
            "generated: 2026-09-08\ntagged_pages: 4\nkeep:\n  - {tag: a, uses: 1, reason: survivor, why: x}\n",
            "t.yaml",
        )


def test_an_unknown_reason_is_refused():
    with pytest.raises(DispositionError, match="reason"):
        loads("generated: 2026-09-08\ntagged_pages: 4\nkeep:\n  - {tag: a, uses: 1, reason: vibes}\n", "t.yaml")


def test_a_missing_generated_date_is_refused():
    with pytest.raises(DispositionError, match="generated"):
        loads("tagged_pages: 4\nkeep:\n  - {tag: a, uses: 1, reason: survivor}\n", "t.yaml")


def test_malformed_yaml_names_the_source():
    with pytest.raises(DispositionError, match=re.escape("t.yaml")):
        loads("keep: [\n", "t.yaml")


def test_load_reads_a_file_and_propagates_a_missing_one(tmp_path):
    target = tmp_path / "d.yaml"
    original = make(TagVerdict(tag="a", uses=9, verdict="keep", reason="survivor"))
    target.write_text(render(original), encoding="utf-8", newline="")
    assert load(target) == original
    with pytest.raises(OSError):
        load(tmp_path / "absent.yaml")
