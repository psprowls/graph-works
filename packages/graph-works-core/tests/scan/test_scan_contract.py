"""The versioned worklist contract: the shape, the round trip, the refusal."""

from __future__ import annotations

import json

import pytest
from graph_works_core.scan.scan_contract import (
    SCHEMA_VERSION,
    ApplyResult,
    ProseRefreshResult,
    ProseRefreshTask,
    ScanResults,
    ScanWorklist,
    SkippedPage,
    UnsupportedWorklistSchema,
    result_from_payload,
    result_payload,
    worklist_from_payload,
    worklist_payload,
)


def _task(uri: str, *, diff: str | None = "packages/widgets/src/a.py") -> ProseRefreshTask:
    return ProseRefreshTask(
        uri=uri,
        kind="Package",
        name="widgets",
        page_path="packages/widgets.md",
        entity_root="/repo/packages/widgets",
        trigger="diff" if diff is None else "first_fill",
        diff=diff,
        changed_files=("packages/widgets/src/a.py",),
        page_content="---\ntype: Package\n---\n\n## Purpose\n",
        prose_sections={"## Purpose": "> TODO: <One paragraph>", "## Public API": ""},
        graph_context="package widgets",
        owning_short_head="0123456789ab",
    )


def test_schema_version_is_four():
    assert SCHEMA_VERSION == 4


def test_a_schema_three_payload_is_refused_by_number():
    payload = worklist_payload(ScanWorklist())
    payload["schema_version"] = 3
    with pytest.raises(UnsupportedWorklistSchema) as caught:
        worklist_from_payload(payload)
    assert "unsupported worklist schema: 3" in str(caught.value)
    assert "this build reads 4" in str(caught.value)


def test_skipped_pages_and_adoptions_round_trip():
    worklist = ScanWorklist(
        head_commit="0123456789abcdef0123456789abcdef01234567",
        short_head="0123456789ab",
        prose_tasks=(_task("pkg:a/b/widgets"),),
        truncated=1,
        skipped=(
            SkippedPage(page="packages/ghost.md", reason="unresolved-resource"),
            SkippedPage(page="packages/stuck.md", reason="attempts-exhausted"),
        ),
        adopted=("repositories/demo.md",),
    )
    text = json.dumps(worklist_payload(worklist))
    assert worklist_from_payload(json.loads(text)) == worklist


def test_an_empty_worklist_carries_no_skips_and_no_adoptions():
    worklist = worklist_from_payload(worklist_payload(ScanWorklist()))
    assert worklist.skipped == ()
    assert worklist.adopted == ()


def test_a_worklist_round_trips_through_json():
    worklist = ScanWorklist(
        head_commit="0123456789abcdef0123456789abcdef01234567",
        short_head="0123456789ab",
        prose_tasks=(_task("pkg:a/b/widgets"), _task("pkg:a/b/gadgets", diff=None)),
        truncated=4,
    )
    text = json.dumps(worklist_payload(worklist))
    assert worklist_from_payload(json.loads(text)) == worklist


def test_a_result_round_trips_and_keeps_a_none_error():
    result = ProseRefreshResult(uri="pkg:a/b/widgets", sections={"## Purpose": "Real prose."})
    assert result_from_payload(json.loads(json.dumps(result_payload(result)))) == result


def test_provider_errors_are_runtime_only():
    """`ScanResults.provider_errors` is not part of the wire contract -- a
    file-surface results directory carries per-task results and nothing else."""
    assert ScanResults().provider_errors == ()
    assert ApplyResult().entity_errors == ()


def test_apply_result_dry_run_defaults_to_false():
    assert ApplyResult().dry_run is False
    assert ApplyResult(dry_run=True).dry_run is True
