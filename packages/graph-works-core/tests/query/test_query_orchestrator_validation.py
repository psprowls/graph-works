"""The structured-output contract. This is the vertical's safety property."""

from __future__ import annotations

import json

import pytest
from graph_works_core.query import query_orchestrator as qo
from okf_io import Document


def _payload(**overrides):
    base = {
        "answer_markdown": "Tokens rotate on refresh.",
        "citations": ["concepts/auth"],
        "evidence": [
            {
                "id": "e1",
                "source_type": "wiki",
                "path": "concepts/auth",
                "freshness": "fresh",
                "staleness_reason": None,
                "excerpt": "Refresh rotates the token.",
                "line_refs": [],
            }
        ],
        "answer_evidence_map": [{"claim": "Tokens rotate on refresh.", "evidence_ids": ["e1"]}],
        "worker_plan": [],
        "worker_results": [],
        "gaps": [],
        "confidence": "high",
    }
    base.update(overrides)
    return base


def test_a_conforming_payload_parses():
    output = qo.parse_orchestrator_output(json.dumps(_payload()))
    assert output.confidence == "high"
    assert output.evidence[0].id == "e1"
    assert output.worker_plan == ()


@pytest.mark.parametrize("missing", sorted(qo.REQUIRED_TOP_LEVEL_KEYS))
def test_every_required_top_level_key_is_required(missing):
    payload = _payload()
    del payload[missing]
    with pytest.raises(qo.OrchestratorValidationError, match="Missing required"):
        qo.parse_orchestrator_output(json.dumps(payload))


def test_an_unexpected_top_level_key_is_rejected():
    with pytest.raises(qo.OrchestratorValidationError, match="Unexpected"):
        qo.parse_orchestrator_output(json.dumps(_payload(extra_key="no")))


def test_invalid_json_is_a_validation_error_not_a_json_error():
    with pytest.raises(qo.OrchestratorValidationError, match="Invalid JSON"):
        qo.parse_orchestrator_output("{not json")


def test_orchestrator_output_as_dict_round_trips_through_json():
    payload = _payload(worker_plan=[{"worker": "librarian", "task_id": "t1"}])
    output = qo.parse_orchestrator_output(json.dumps(payload))

    projection = qo.orchestrator_output_as_dict(output)

    assert projection["worker_plan"] == [{"worker": "librarian", "task_id": "t1"}]
    assert isinstance(projection["worker_plan"][0], dict)
    assert json.loads(json.dumps(projection)) == projection


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("```json\n{payload}\n```", id="fenced-labelled"),
        pytest.param("```\n{payload}\n```", id="fenced-unlabelled"),
        pytest.param("Here is the JSON:\n{payload}", id="prose-prefaced"),
        pytest.param("Here is the JSON:\n```json\n{payload}\n```", id="prose-and-fence"),
        pytest.param("```json\n{payload}\n```\nHope that helps.", id="trailing-prose"),
    ],
)
def test_fenced_and_prefaced_json_parse_like_bare_json(raw):
    payload_text = json.dumps(_payload())
    bare = qo.parse_orchestrator_output(payload_text)
    decorated = qo.parse_orchestrator_output(raw.format(payload=payload_text))
    assert decorated == bare


def test_source_type_is_a_closed_vocabulary():
    payload = _payload()
    payload["evidence"][0]["source_type"] = "graph"
    with pytest.raises(qo.OrchestratorValidationError, match="source_type"):
        qo.parse_orchestrator_output(json.dumps(payload))


def test_freshness_is_a_closed_vocabulary():
    payload = _payload()
    payload["evidence"][0]["freshness"] = "recent"
    with pytest.raises(qo.OrchestratorValidationError, match="freshness"):
        qo.parse_orchestrator_output(json.dumps(payload))


def test_confidence_is_a_closed_vocabulary():
    with pytest.raises(qo.OrchestratorValidationError, match="confidence"):
        qo.parse_orchestrator_output(json.dumps(_payload(confidence="very high")))


def test_duplicate_evidence_ids_are_rejected():
    payload = _payload()
    payload["evidence"].append(dict(payload["evidence"][0]))
    with pytest.raises(qo.OrchestratorValidationError, match="unique"):
        qo.parse_orchestrator_output(json.dumps(payload))


def test_a_claim_referencing_a_missing_evidence_id_is_rejected():
    payload = _payload(answer_evidence_map=[{"claim": "c", "evidence_ids": ["nope"]}])
    with pytest.raises(qo.OrchestratorValidationError, match="missing evidence ids"):
        qo.parse_orchestrator_output(json.dumps(payload))


def _stale_payload(**overrides):
    payload = _payload(**overrides)
    payload["evidence"][0]["freshness"] = "stale"
    payload["evidence"][0]["staleness_reason"] = "last_updated_commit mismatch"
    return payload


def test_a_stale_only_claim_needs_a_gap_or_an_uncertainty_note():
    with pytest.raises(qo.OrchestratorValidationError, match="stale-only"):
        qo.parse_orchestrator_output(json.dumps(_stale_payload()), require_stale_claim_gaps=True)


def test_a_stale_only_claim_with_a_gap_is_accepted():
    payload = _stale_payload(gaps=[{"question": "Is this current?", "reason": "only stale evidence"}])
    assert qo.parse_orchestrator_output(json.dumps(payload), require_stale_claim_gaps=True).gaps


def test_a_stale_only_claim_with_uncertainty_wording_is_accepted():
    payload = _stale_payload(answer_markdown="Tokens may rotate on refresh.")
    assert qo.parse_orchestrator_output(json.dumps(payload), require_stale_claim_gaps=True)


def test_a_month_named_may_is_not_uncertainty_wording():
    # The named regression: `\bmay\b(?!\s+\d{4}\b)` — "May 2026" is a date.
    payload = _stale_payload(answer_markdown="Recorded in May 2026.")
    with pytest.raises(qo.OrchestratorValidationError, match="stale-only"):
        qo.parse_orchestrator_output(json.dumps(payload), require_stale_claim_gaps=True)


def test_parse_orchestrator_output_rejects_a_stale_only_claim_by_default():
    with pytest.raises(qo.OrchestratorValidationError, match="stale-only"):
        qo.parse_orchestrator_output(json.dumps(_stale_payload()))


def test_validate_orchestrator_output_rejects_a_stale_only_claim_by_default():
    output = qo.parse_orchestrator_output(json.dumps(_stale_payload()), require_stale_claim_gaps=False)
    with pytest.raises(qo.OrchestratorValidationError, match="stale-only"):
        qo.validate_orchestrator_output(output)


def _doc(text: str) -> Document:
    return Document.parse(text)


def test_freshness_matches_the_repo_head():
    doc = _doc(
        "---\ntitle: Auth\nlast_updated_commit: abc123\n---\n\n" + "Real narrative content, well over the floor. " * 3
    )
    assert qo.classify_wiki_freshness(doc, repo_head="abc123").freshness == "fresh"


def test_a_commit_mismatch_is_stale():
    doc = _doc("---\ntitle: Auth\nlast_updated_commit: abc123\n---\n\n" + "Real narrative content here. " * 4)
    result = qo.classify_wiki_freshness(doc, repo_head="def456")
    assert (result.freshness, result.reason) == ("stale", "last_updated_commit mismatch")


def test_placeholder_content_is_stale():
    doc = _doc("---\ntitle: Auth\n---\n\nTODO\n")
    assert qo.classify_wiki_freshness(doc, repo_head=None).freshness == "stale"


def test_a_degraded_status_is_stale():
    doc = _doc("---\ntitle: Auth\nstatus: degraded\n---\n\n" + "Real narrative content here. " * 4)
    result = qo.classify_wiki_freshness(doc, repo_head=None)
    assert (result.freshness, result.reason) == ("stale", "degraded status")


def test_no_signal_is_unknown():
    doc = _doc("---\ntitle: Auth\n---\n\n" + "Real narrative content here. " * 4)
    assert qo.classify_wiki_freshness(doc, repo_head=None).freshness == "unknown"
