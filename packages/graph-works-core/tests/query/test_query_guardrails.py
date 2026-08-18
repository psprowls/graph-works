"""G4 then G1, over a bundle rather than a filesystem glob."""

from __future__ import annotations

from pathlib import Path

from graph_works_core.query import commands as q
from okf_io import load_bundle
from subagents_io import FanOutResult


def _bundle(tmp_path: Path):
    root = tmp_path / "okf"
    (root / "concepts").mkdir(parents=True)
    (root / "concepts" / "auth.md").write_text("---\ntitle: Auth\n---\n\nBody.\n", encoding="utf-8")
    return load_bundle(root)


def _result(answer: str, citations: list[str]) -> q.QueryResult:
    return q.QueryResult(
        answer=answer,
        citations=citations,
        pages_drilled=1,
        search_scores={},
        path="orchestrated",
        fallback_error=None,
    )


def test_extract_links_yields_concept_ids():
    text = "See [Auth](/concepts/auth.md) and [Storage](/lane/storage.md)."
    assert q._extract_links(text) == ["concepts/auth", "lane/storage"]


def test_extract_links_ignores_non_root_absolute_and_external_links():
    text = "[rel](concepts/auth.md) [up](../auth.md) [ext](https://example.test/a.md) [nomd](/concepts/auth)"
    assert q._extract_links(text) == []


def test_g1_passes_a_link_present_in_the_bundle(tmp_path):
    bundle = _bundle(tmp_path)
    result = _result("Answer citing [Auth](/concepts/auth.md).", ["concepts/auth"])
    guarded = q.apply_guardrails(result, bundle, FanOutResult(successes=[("p", "x")], errors=[]))
    assert guarded.answer == result.answer
    assert "warning" not in guarded.answer


def test_g1_flags_a_link_absent_from_the_bundle(tmp_path):
    bundle = _bundle(tmp_path)
    result = _result("Answer citing [Ghost](/concepts/ghost.md).", ["concepts/ghost"])
    guarded = q.apply_guardrails(result, bundle, FanOutResult(successes=[("p", "x")], errors=[]))
    assert "did not resolve" in guarded.answer
    assert "concepts/ghost" in guarded.answer
    assert guarded.answer.startswith(result.answer)  # appended, never rewritten


def test_g1_flags_a_citation_absent_from_the_body_and_the_bundle(tmp_path):
    # The widening: G1 used to scan only the answer body's markdown links.
    # This citation is never linked in the body at all, so the only way it
    # gets flagged is if `_unresolved_links` checks `citations` too.
    bundle = _bundle(tmp_path)
    result = _result("No links here.", ["concepts/ghost"])
    guarded = q.apply_guardrails(result, bundle, FanOutResult(successes=[("p", "x")], errors=[]))
    assert "did not resolve" in guarded.answer
    assert "concepts/ghost" in guarded.answer


def test_g4_fires_on_empty_successes_with_citations(tmp_path):
    bundle = _bundle(tmp_path)
    result = _result("Confident answer citing [Auth](/concepts/auth.md).", ["concepts/auth"])
    guarded = q.apply_guardrails(result, bundle, FanOutResult(successes=[], errors=[]))
    assert guarded.citations == []
    assert "unsupported by retrieved pages" in guarded.answer


def test_g4_runs_before_g1(tmp_path):
    # The ordering regression: with G1 first, the cleared citation list would
    # still have been scanned and every link flagged as unresolved on top of
    # the G4 warning. Exactly one warning is the assertion.
    bundle = _bundle(tmp_path)
    result = _result("Answer citing [Auth](/concepts/auth.md).", ["concepts/auth"])
    guarded = q.apply_guardrails(result, bundle, FanOutResult(successes=[], errors=[]))
    assert guarded.answer.count("[warning:") == 1


def test_skip_g4_suppresses_only_g4(tmp_path):
    bundle = _bundle(tmp_path)
    result = _result("Answer citing [Ghost](/concepts/ghost.md).", ["concepts/ghost"])
    guarded = q.apply_guardrails(result, bundle, FanOutResult(successes=[], errors=[]), skip_g4=True)
    assert guarded.citations == ["concepts/ghost"]  # G4 did not clear them
    assert "did not resolve" in guarded.answer  # G1 still ran


def test_an_answer_with_no_findings_is_returned_unchanged(tmp_path):
    bundle = _bundle(tmp_path)
    result = _result("No links here.", [])
    assert q.apply_guardrails(result, bundle, FanOutResult(successes=[("p", "x")], errors=[])) == result
