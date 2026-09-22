"""The suggest phase: parse, validate against the lanes, file, report."""

from __future__ import annotations

import json
from pathlib import Path

from doc_wiki_okf.proposals.lanes import lane_set
from graph_works_core.ingest.proposal_reasoner import ProposalReasonerResult
from graph_works_core.ingest.suggest_pages import (
    MAX_SUGGESTIONS,
    _string_list,
    _validate_suggestion,
    apply_suggestions,
    build_curated_index,
    build_extract_prompt,
    catalog_lanes,
    parse_extractor_response,
    plan_suggestions,
    run_suggest_phase,
)
from ingest_helpers import AT, FakeLLM, FakeResponse, declarations, json_fence
from okf_ext.bundle import SCHEMA_DIRNAME
from okf_io import load_bundle
from suggest_fixtures import make_bundle  # see Step 2

LANES = ("tutorial", "how-to", "reference", "explanation", "adr")


def _suggestion(**overrides):
    base = {
        "lane": "explanation",
        "title": "Why splicing beats regeneration",
        "rationale": "The source argues for the design, so it is an explanation.",
        "description": "Why the writer splices.",
        "rank": 1,
        "confidence": "high",
        "evidence": ["the source says so"],
        "existing_pages_considered": [],
        "reasoning_summary": "It argues.",
        "potential_conflicts": [],
        "implementation_notes": [],
    }
    base.update(overrides)
    return base


def test_parse_accepts_the_object_form():
    parsed, ok = parse_extractor_response(json.dumps({"suggestions": [_suggestion()]}), lane_names=LANES)
    assert ok and len(parsed) == 1


def test_parse_accepts_a_bare_list_and_a_fenced_object():
    assert parse_extractor_response(json.dumps([_suggestion()]), lane_names=LANES)[1] is True
    assert parse_extractor_response(json_fence({"suggestions": [_suggestion()]}), lane_names=LANES)[1] is True


def test_parse_treats_a_null_suggestions_key_as_zero_and_parsed():
    assert parse_extractor_response('{"suggestions": null}', lane_names=LANES) == ([], True)


def test_parse_reports_a_miss_for_non_json_and_for_a_wrong_shape():
    assert parse_extractor_response("not json at all", lane_names=LANES) == ([], False)
    assert parse_extractor_response('"a string"', lane_names=LANES) == ([], False)
    assert parse_extractor_response("", lane_names=LANES) == ([], False)
    assert parse_extractor_response("```\n", lane_names=LANES) == ([], False)


def test_a_non_dict_suggestion_item_is_dropped():
    payload = {"suggestions": ["not a dict", _suggestion()]}
    parsed, ok = parse_extractor_response(json.dumps(payload), lane_names=LANES)
    assert ok
    assert len(parsed) == 1


def test_a_non_list_suggestions_value_is_a_parse_miss():
    assert parse_extractor_response(json.dumps({"suggestions": "oops"}), lane_names=LANES) == ([], False)


def test_a_bad_lane_a_blank_title_and_a_blank_rationale_are_dropped():
    payload = {
        "suggestions": [
            _suggestion(lane="concept"),
            _suggestion(title="   "),
            _suggestion(rationale=""),
            _suggestion(),
        ]
    }
    parsed, ok = parse_extractor_response(json.dumps(payload), lane_names=LANES)
    assert ok and len(parsed) == 1


def test_an_explicit_null_title_or_rationale_is_dropped_not_stringified():
    """`raw.get(key, default)` only falls back when the key is absent.

    A JSON `null` is present, so a naive `.get` returns `None` and `str(None)`
    is the non-blank literal `"None"` -- defeating the blank check below. The
    fallback has to trigger on `None` too, not only on a missing key.
    """
    assert _validate_suggestion({"lane": "explanation", "title": None, "rationale": "ok reason"}, LANES) is None
    assert _validate_suggestion({"lane": "explanation", "title": "T", "rationale": None}, LANES) is None


def test_a_null_description_and_reasoning_summary_fall_back_instead_of_stringifying():
    entry = _validate_suggestion(
        {
            "lane": "explanation",
            "title": "T",
            "rationale": "R",
            "description": None,
            "reasoning_summary": None,
        },
        LANES,
    )
    assert entry is not None
    assert entry["description"] == "R"
    assert entry["reasoning_summary"] == ""


def test_string_list_handles_none_and_a_bare_scalar():
    assert _string_list(None) == []
    assert _string_list("solo evidence") == ["solo evidence"]
    assert _string_list("   ") == []


def test_parse_sorts_by_rank_and_caps_the_list():
    payload = {"suggestions": [_suggestion(title=f"T{index}", rank=10 - index) for index in range(8)]}
    parsed, _ = parse_extractor_response(json.dumps(payload), lane_names=LANES)
    assert len(parsed) == MAX_SUGGESTIONS
    assert [entry["rank"] for entry in parsed] == sorted(entry["rank"] for entry in parsed)


def test_an_unparseable_rank_and_confidence_fall_back():
    parsed, _ = parse_extractor_response(
        json.dumps({"suggestions": [_suggestion(rank="soon", confidence="certain")]}), lane_names=LANES
    )
    assert parsed[0]["rank"] == 999
    assert parsed[0]["confidence"] == "medium"


def test_catalog_lanes_covers_global_discovery_indexes_but_not_a_top_level_files_lane(tmp_path):
    root = make_bundle(tmp_path)
    schema_set, _section_set = declarations(root)
    lanes = set(catalog_lanes(lane_set(schema_set), schema_set))

    assert {
        "repositories",
        "packages",
        "apps",
        "agent-plugins",
        "test-suites",
        "dependencies",
        "docs/explanations",
        "adrs",
        "sources",
    } <= lanes
    assert "files" not in lanes


def test_the_curated_index_lists_existing_pages_with_their_lane(tmp_path):
    root = make_bundle(tmp_path)
    bundle = load_bundle(root)
    index = build_curated_index(bundle, lane_set(declarations(root)[0]))
    assert {"lane": "explanation", "id": "docs/explanations/why", "title": "Why", "summary": "The reason"} in index


def test_the_extract_prompt_names_the_existing_pages_and_says_so_when_there_are_none():
    assert "(no curated pages yet)" in build_extract_prompt("analysis", [])
    listed = build_extract_prompt("analysis", [{"lane": "adr", "id": "adrs/x", "title": "X", "summary": "s"}])
    assert "adrs/x" in listed and "analysis" in listed


async def _phase(root: Path, *, extractor_payload, reasoner=None, monkeypatch):
    schema_set, _section_set = declarations(root)
    bundle = load_bundle(root)
    monkeypatch.setattr(
        "graph_works_core.ingest.suggest_pages.make_llm",
        lambda *args, **kwargs: FakeLLM(FakeResponse(extractor_payload)),
    )

    async def fake_reasoner(**kwargs):
        return reasoner or ProposalReasonerResult(status="ok", analysis="the analysis")

    monkeypatch.setattr("graph_works_core.ingest.suggest_pages.run_proposal_reasoner", fake_reasoner)
    return await run_suggest_phase(
        bundle=bundle,
        schema_set=schema_set,
        lane_set=lane_set(schema_set),
        material=Path("/tmp/thing.md"),
        source_text="short",
        source_page="sources/2026-08-thing.md",
        source_title="Thing",
        source_kind="note",
        origin="/tmp/thing.md",
        page_text="body",
        entity_uri=None,
        entity_page=None,
        by="agent:test",
        at=AT,
    )


async def _plan_phase(root: Path, *, extractor_payload, reasoner=None, monkeypatch, bundle=None):
    schema_set, _section_set = declarations(root)
    bundle = load_bundle(root) if bundle is None else bundle
    monkeypatch.setattr(
        "graph_works_core.ingest.suggest_pages.make_llm",
        lambda *args, **kwargs: FakeLLM(FakeResponse(extractor_payload)),
    )

    async def fake_reasoner(**kwargs):
        return reasoner or ProposalReasonerResult(status="ok", analysis="the analysis")

    monkeypatch.setattr("graph_works_core.ingest.suggest_pages.run_proposal_reasoner", fake_reasoner)
    planned, status = await plan_suggestions(
        bundle=bundle,
        schema_set=schema_set,
        lane_set=lane_set(schema_set),
        material=Path("/tmp/thing.md"),
        source_text="short",
        source_page="sources/2026-08-thing.md",
        source_title="Thing",
        source_kind="note",
        origin="/tmp/thing.md",
        page_text="body",
        entity_uri=None,
        entity_page=None,
        by="agent:test",
        at=AT,
    )
    return planned, status, bundle


async def test_plan_suggestions_writes_nothing(tmp_path, monkeypatch):
    root = make_bundle(tmp_path)
    payload = json.dumps({"suggestions": [_suggestion(title="Deferred")]})
    planned, status, _bundle = await _plan_phase(root, extractor_payload=payload, monkeypatch=monkeypatch)
    assert len(planned) == 1
    assert planned[0].target == "docs/explanations/deferred.md"
    assert status["proposals"] == 1  # optimistic: len(planned), nothing applied yet
    assert not (root / "proposals").exists()


async def test_apply_suggestions_applies_a_previously_planned_proposal(tmp_path, monkeypatch):
    root = make_bundle(tmp_path)
    payload = json.dumps({"suggestions": [_suggestion(title="Deferred")]})
    planned, _plan_status, bundle = await _plan_phase(root, extractor_payload=payload, monkeypatch=monkeypatch)
    assert not (root / "proposals").exists()  # nothing written by planning

    reports, apply_status = apply_suggestions(bundle, planned)
    assert apply_status == {"proposals": 1, "failed": [], "errored": []}
    assert [report["target"] for report in reports] == ["docs/explanations/deferred.md"]
    assert (root / planned[0].plan.proposal).exists()


async def test_an_apply_failure_is_reported_by_apply_suggestions_not_plan_suggestions(tmp_path, monkeypatch):
    root = make_bundle(tmp_path)
    payload = json.dumps({"suggestions": [_suggestion(title="Blocked")]})
    planned, plan_status, bundle = await _plan_phase(root, extractor_payload=payload, monkeypatch=monkeypatch)
    assert plan_status["failed"] == []  # nothing has been applied yet

    (root / "proposals").write_text("not a directory\n", encoding="utf-8")
    reports, apply_status = apply_suggestions(bundle, planned)
    assert reports == []
    assert apply_status["proposals"] == 0
    assert apply_status["failed"] == ["Blocked: mkdir-error"]


async def test_run_suggest_phase_merges_plan_time_and_apply_time_errored_entries_in_order(tmp_path, monkeypatch):
    """A plan-time failure and an apply-time failure, in the same run.

    In the un-split `run_suggest_phase`, one try/except wrapped classify ->
    `plan_file` -> `apply_plan` -> report-append per suggestion, so a plan-time
    and an apply-time error interleaved in suggestion order within one
    `errored` list. The split instead runs `plan_suggestions` to completion --
    collecting every plan-time `errored` entry -- and only then runs
    `apply_suggestions` over what it planned, collecting apply-time `errored`
    entries separately. `run_suggest_phase`'s wrapper concatenates
    plan-phase-first, apply-phase-second.

    Here `ApplyBoom` (rank 1, the earlier suggestion) plans fine but fails
    when applied; `PlanBoom` (rank 2, the later suggestion) fails while still
    being planned, so it never reaches `apply_suggestions` at all. The old
    single-list contract would have reported `PlanBoom` before `ApplyBoom`
    only if apply ran inline per suggestion in submission order -- the split's
    actual, intended merge puts every plan-time entry first regardless of
    which suggestion is later, which is exactly what this asserts.
    """
    root = make_bundle(tmp_path)
    schema_set, _section_set = declarations(root)
    payload = json.dumps(
        {
            "suggestions": [
                _suggestion(title="ApplyBoom", rank=1),
                _suggestion(title="PlanBoom", rank=2),
            ]
        }
    )
    monkeypatch.setattr(
        "graph_works_core.ingest.suggest_pages.make_llm",
        lambda *args, **kwargs: FakeLLM(FakeResponse(payload)),
    )

    async def fake_reasoner(**kwargs):
        return ProposalReasonerResult(status="ok", analysis="a")

    monkeypatch.setattr("graph_works_core.ingest.suggest_pages.run_proposal_reasoner", fake_reasoner)

    from doc_wiki_okf.diataxis.classify import classify as real_classify

    def fake_classify(schema_set_arg, **kwargs):
        if kwargs.get("title") == "PlanBoom":
            raise RuntimeError("boom-plan")
        return real_classify(schema_set_arg, **kwargs)

    monkeypatch.setattr("graph_works_core.ingest.suggest_pages.classify", fake_classify)

    from okf_ext.proposals import apply as real_apply_plan

    def fake_apply(bundle_arg, plan):
        if plan.target == "docs/explanations/applyboom.md":
            raise RuntimeError("boom-apply")
        return real_apply_plan(bundle_arg, plan)

    monkeypatch.setattr("graph_works_core.ingest.suggest_pages.apply_plan", fake_apply)

    reports, status = await run_suggest_phase(
        bundle=load_bundle(root),
        schema_set=schema_set,
        lane_set=lane_set(schema_set),
        material=Path("/tmp/thing.md"),
        source_text="short",
        source_page="sources/2026-08-thing.md",
        source_title="Thing",
        source_kind="note",
        origin="/tmp/thing.md",
        page_text="body",
        entity_uri=None,
        entity_page=None,
        by="agent:test",
        at=AT,
    )
    assert reports == []
    assert status["proposals"] == 0
    assert status["failed"] == []
    # Plan-phase entries first (PlanBoom, the later suggestion), then
    # apply-phase entries (ApplyBoom, the earlier one) -- the merge order,
    # not suggestion order.
    assert status["errored"] == ["PlanBoom: RuntimeError", "ApplyBoom: RuntimeError"]


async def test_apply_suggestions_skips_apply_plan_for_an_is_empty_planned_item(tmp_path, monkeypatch):
    """A same-target refiling with nothing new to merge plans zero `Write`s.

    Filing the identical suggestion a second time -- same title, description,
    and source `resource` -- reaches `plan_propose`'s no-op merge (see
    `okf_ext.proposals.tests.test_proposals_plan.
    test_re_proposing_what_is_already_there_plans_nothing` for the same shape
    one layer down): the source is already on the live proposal by `resource`,
    and neither title nor description changed, so the second `plan_file` call
    plans no `Write` at all and `plan.is_empty` is `True`.

    `apply_suggestions` must not call `apply_plan` for such an item -- there is
    nothing to apply -- and must still report it as a landed proposal, because
    nothing needing a write does not mean nothing was proposed.
    """
    root = make_bundle(tmp_path)
    payload = json.dumps({"suggestions": [_suggestion(title="Repeatable")]})

    first_planned, _first_status, bundle = await _plan_phase(root, extractor_payload=payload, monkeypatch=monkeypatch)
    apply_suggestions(bundle, first_planned)  # lands the first filing on disk

    second_planned, second_status, _bundle2 = await _plan_phase(
        root, extractor_payload=payload, monkeypatch=monkeypatch
    )
    assert second_status["proposals"] == 1
    item = second_planned[0]
    assert item.plan.ok
    assert item.plan.is_empty

    def _forbidden_apply(*args, **kwargs):
        raise AssertionError("apply_plan must not be called for an is_empty plan")

    monkeypatch.setattr("graph_works_core.ingest.suggest_pages.apply_plan", _forbidden_apply)
    reports, status = apply_suggestions(load_bundle(root), [item])
    assert [report["status"] for report in reports] == ["proposed"]
    assert status == {"proposals": 1, "failed": [], "errored": []}


async def test_each_lane_files_to_its_declared_target(tmp_path, monkeypatch):
    root = make_bundle(tmp_path)
    payload = json.dumps(
        {"suggestions": [_suggestion(lane=lane, title=f"Page {lane}", rank=index) for index, lane in enumerate(LANES)]}
    )
    reports, status = await _phase(root, extractor_payload=payload, monkeypatch=monkeypatch)
    targets = {report["target"] for report in reports}
    assert targets == {
        "docs/tutorials/page-tutorial.md",
        "docs/how-tos/page-how-to.md",
        "docs/reference/page-reference.md",
        "docs/explanations/page-explanation.md",
        "adrs/page-adr.md",
    }
    assert status["extractor"] == "ok"
    assert status["proposals"] == 5
    assert all("mode" not in report for report in reports)


async def test_a_classify_refusal_drops_the_suggestion_and_records_the_reason(tmp_path, monkeypatch):
    """`classify` gets a narrower SchemaSet than the LaneSet was built from.

    That is the real asymmetry: `lane_set` needs every Diataxis type to derive
    its directories (a missing one is a `KeyError` at construction), while
    `classify` is validating against whatever the bundle actually declares.
    Dropping `Reference` from the set `classify` sees reaches `undeclared-type`
    without breaking lane construction.
    """
    from okf_ext.schemas import load_schemas

    root = make_bundle(tmp_path)
    narrow_dir = tmp_path / "narrow"
    narrow_dir.mkdir()
    for schema in (root / SCHEMA_DIRNAME).glob("*.json"):
        if schema.name != "Reference.schema.json":
            (narrow_dir / schema.name).write_text(schema.read_text(encoding="utf-8"), encoding="utf-8")

    full_schema_set, _ = declarations(root)
    monkeypatch.setattr(
        "graph_works_core.ingest.suggest_pages.make_llm",
        lambda *args, **kwargs: FakeLLM(
            FakeResponse(json.dumps({"suggestions": [_suggestion(lane="reference", title="Options")]}))
        ),
    )

    async def fake_reasoner(**kwargs):
        return ProposalReasonerResult(status="ok", analysis="a")

    monkeypatch.setattr("graph_works_core.ingest.suggest_pages.run_proposal_reasoner", fake_reasoner)
    reports, status = await run_suggest_phase(
        bundle=load_bundle(root),
        schema_set=load_schemas(narrow_dir),
        lane_set=lane_set(full_schema_set),
        material=Path("/tmp/thing.md"),
        source_text="short",
        source_page="sources/2026-08-thing.md",
        source_title="Thing",
        source_kind="note",
        origin="/tmp/thing.md",
        page_text="body",
        entity_uri=None,
        entity_page=None,
        by="agent:test",
        at=AT,
    )
    assert reports == []
    assert status["proposals"] == 0
    assert status["unclassified"] == ["Options: undeclared-type"]
    assert not (root / "docs" / "reference").exists()


async def test_mode_is_derived_from_the_bundle_not_proposed(tmp_path, monkeypatch):
    """An existing target selects update mode regardless of model suggestions."""
    root = make_bundle(tmp_path)
    (root / "docs" / "explanations" / "repeatable.md").write_text(
        "---\ntype: Explanation\ntitle: Repeatable\ndescription: d\n---\n\nbody\n",
        encoding="utf-8",
        newline="",
    )
    payload = json.dumps({"suggestions": [_suggestion(title="Repeatable", mode="create_new")]})
    reports, _ = await _phase(root, extractor_payload=payload, monkeypatch=monkeypatch)
    proposal = (root / reports[0]["proposal"]).read_text(encoding="utf-8")
    assert "Update existing Explanation page" in proposal
    assert reports[0]["target"] == "docs/explanations/repeatable.md"


async def test_a_changed_renderer_context_refuses_without_replacing_the_proposal(tmp_path, monkeypatch):
    """A target appearing changes renderer output; reconcile before merging."""
    root = make_bundle(tmp_path)
    payload = json.dumps({"suggestions": [_suggestion(title="Repeatable")]})
    first, _ = await _phase(root, extractor_payload=payload, monkeypatch=monkeypatch)
    note = root / first[0]["proposal"]
    before = note.read_bytes()
    (root / "docs" / "explanations" / "repeatable.md").write_text(
        "---\ntype: Explanation\ntitle: Repeatable\ndescription: d\n---\n\nbody\n",
        encoding="utf-8",
        newline="",
    )
    second_payload = json.dumps(
        {"suggestions": [_suggestion(title="Repeatable", description="A new angle on the same page.")]}
    )
    reports, status = await _phase(root, extractor_payload=second_payload, monkeypatch=monkeypatch)
    assert reports == []
    assert status["refused"] == ["Repeatable: unrenderable-body"]
    assert note.read_bytes() == before


async def test_a_reasoner_failure_writes_nothing(tmp_path, monkeypatch):
    root = make_bundle(tmp_path)
    reports, status = await _phase(
        root,
        extractor_payload="{}",
        reasoner=ProposalReasonerResult(status="failed", analysis="", error="reasoner died"),
        monkeypatch=monkeypatch,
    )
    assert reports == []
    assert status["reasoner"] == "failed"
    assert status["extractor"] == "skipped"
    assert status["error"] == "reasoner died"
    assert not (root / "proposals").exists()


async def test_a_reasoner_that_raises_writes_nothing(tmp_path, monkeypatch):
    """The `except Exception` guard around the `await`, not the `status="failed"` return.

    `test_a_reasoner_failure_writes_nothing` covers the reasoner returning a
    failed result; this covers the reasoner itself raising, which is caught
    and folded into the same `status["reasoner"] == "failed"` shape.
    """
    root = make_bundle(tmp_path)
    schema_set, _ = declarations(root)
    monkeypatch.setattr(
        "graph_works_core.ingest.suggest_pages.make_llm",
        lambda *args, **kwargs: FakeLLM(FakeResponse("{}")),
    )

    async def fake_reasoner(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("graph_works_core.ingest.suggest_pages.run_proposal_reasoner", fake_reasoner)
    reports, status = await run_suggest_phase(
        bundle=load_bundle(root),
        schema_set=schema_set,
        lane_set=lane_set(schema_set),
        material=Path("/tmp/thing.md"),
        source_text="short",
        source_page="sources/2026-08-thing.md",
        source_title="Thing",
        source_kind="note",
        origin="/tmp/thing.md",
        page_text="body",
        entity_uri=None,
        entity_page=None,
        by="agent:test",
        at=AT,
    )
    assert reports == []
    assert status["reasoner"] == "failed"
    assert status["extractor"] == "skipped"
    assert not (root / "proposals").exists()


async def test_a_plan_file_refusal_drops_the_suggestion_and_records_the_reason(tmp_path, monkeypatch):
    """`classify` accepting a suggestion doesn't guarantee `plan_file` will.

    Seed a live, already-decided `Proposal` at the exact target the
    suggestion resolves to (`docs/explanations/blocked.md`): `plan_propose` refuses
    a re-proposal against a decided proposal with `already-decided`, never
    silently reopening it. That refusal has to surface under its own key --
    `status["refused"]` -- with the bare refusal kind, no proposal filed,
    nothing written under the target. It is not a `classify` refusal and does
    not belong in `unclassified`, whose vocabulary is `Unclassified.reason`'s.
    """
    root = make_bundle(tmp_path)
    (root / "proposals").mkdir()
    (root / "proposals" / "blocked.md").write_text(
        "---\n"
        "type: Proposal\n"
        "title: Blocked\n"
        "description: d\n"
        "target: docs/explanations/blocked.md\n"
        "page_status: approved\n"
        "generated:\n"
        "  by: agent:test\n"
        "  at: 2026-08-01T00:00:00+00:00\n"
        "sources: []\n"
        "---\n\n"
        "body\n",
        encoding="utf-8",
    )
    payload = json.dumps({"suggestions": [_suggestion(title="Blocked")]})
    reports, status = await _phase(root, extractor_payload=payload, monkeypatch=monkeypatch)
    assert reports == []
    assert status["proposals"] == 0
    assert status["refused"] == ["Blocked: already-decided"]
    assert not (root / "explanations" / "blocked.md").exists()


async def test_an_extractor_parse_miss_writes_nothing(tmp_path, monkeypatch):
    root = make_bundle(tmp_path)
    reports, status = await _phase(root, extractor_payload="not json", monkeypatch=monkeypatch)
    assert reports == []
    assert status["extractor"] == "failed"
    assert status["error"] == "extractor output did not parse"


async def test_an_extractor_call_failure_writes_nothing(tmp_path, monkeypatch):
    root = make_bundle(tmp_path)
    schema_set, _ = declarations(root)
    monkeypatch.setattr(
        "graph_works_core.ingest.suggest_pages.make_llm",
        lambda *args, **kwargs: FakeLLM(fail=True),
    )

    async def fake_reasoner(**kwargs):
        return ProposalReasonerResult(status="ok", analysis="a")

    monkeypatch.setattr("graph_works_core.ingest.suggest_pages.run_proposal_reasoner", fake_reasoner)
    reports, status = await run_suggest_phase(
        bundle=load_bundle(root),
        schema_set=schema_set,
        lane_set=lane_set(schema_set),
        material=Path("/tmp/thing.md"),
        source_text="short",
        source_page="sources/2026-08-thing.md",
        source_title="Thing",
        source_kind="note",
        origin="/tmp/thing.md",
        page_text="body",
        entity_uri=None,
        entity_page=None,
        by="agent:test",
        at=AT,
    )
    assert reports == []
    assert status["extractor"] == "failed"


async def test_a_non_text_extractor_response_is_a_failure(tmp_path, monkeypatch):
    root = make_bundle(tmp_path)
    reports, status = await _phase(root, extractor_payload=[{"type": "text"}], monkeypatch=monkeypatch)
    assert reports == []
    assert status["extractor"] == "failed"


async def test_two_suggestions_for_one_target_file_once_and_count_once(tmp_path, monkeypatch):
    """The bundle is a snapshot; the loop's own writes do not appear in it.

    Both suggestions resolve to `docs/explanations/same-page.md`, so `plan_file`
    derived `mode="create"` twice and the second write was refused as `stale`.
    Nothing read that refusal, so the run reported two proposals and one file.

    Merging the second would add nothing: `plan_propose` dedups `sources[]` on
    `resource`, and both entries carry the same source page. Dropping it with a
    recorded reason is the honest outcome.
    """
    root = make_bundle(tmp_path)
    payload = json.dumps(
        {
            "suggestions": [
                _suggestion(title="Same Page", rank=1),
                _suggestion(title="Same Page", rank=2, description="a different angle"),
            ]
        }
    )
    reports, status = await _phase(root, extractor_payload=payload, monkeypatch=monkeypatch)
    assert status["proposals"] == 1
    assert len(reports) == 1
    assert len(list((root / "proposals").glob("*.md"))) == 1
    assert status["duplicates"] == ["Same Page: docs/explanations/same-page.md"]


async def test_a_proposal_whose_write_fails_is_recorded_not_counted(tmp_path, monkeypatch):
    """`proposals` is a regular file, so the create's parent `mkdir` fails.

    The point is not the filesystem: it is that `apply`'s `ApplyResult` was
    discarded, so *any* write failure came back as `status: "proposed"` and was
    counted into `IngestResult.proposal_status` and the `log.md` line.
    """
    root = make_bundle(tmp_path)
    (root / "proposals").write_text("not a directory\n", encoding="utf-8")
    payload = json.dumps({"suggestions": [_suggestion(title="Blocked")]})
    reports, status = await _phase(root, extractor_payload=payload, monkeypatch=monkeypatch)
    assert reports == []
    assert status["proposals"] == 0
    assert status["failed"] == ["Blocked: mkdir-error"]


async def test_a_clean_run_records_no_write_failures(tmp_path, monkeypatch):
    root = make_bundle(tmp_path)
    payload = json.dumps({"suggestions": [_suggestion(title="Fine")]})
    reports, status = await _phase(root, extractor_payload=payload, monkeypatch=monkeypatch)
    assert len(reports) == 1
    assert status["failed"] == []


async def test_a_blank_source_text_skips_both_model_calls(tmp_path, monkeypatch):
    """P-2: binary material extracts to nothing. Reasoning over nothing files
    proposals the material does not support."""
    root = make_bundle(tmp_path)
    schema_set, _section_set = declarations(root)

    def exploding_make_llm(role, **kwargs):
        raise AssertionError(f"{role} was called for a blank source")

    for path in (
        "graph_works_core.ingest.suggest_pages",
        "graph_works_core.ingest.proposal_reasoner",
    ):
        monkeypatch.setattr(f"{path}.make_llm", exploding_make_llm, raising=False)

    planned, status = await plan_suggestions(
        bundle=load_bundle(root),
        schema_set=schema_set,
        lane_set=lane_set(schema_set),
        material=Path("/tmp/scan.pdf"),
        source_text="   \n\t  ",
        source_page="sources/2026-08-scan.md",
        source_title="Scan",
        source_kind="note",
        origin="/tmp/scan.pdf",
        page_text="body",
        entity_uri=None,
        entity_page=None,
        by="agent:test",
        at=AT,
    )

    assert planned == []
    assert status["reasoner"] == "skipped"
    assert status["extractor"] == "skipped"
    assert status["proposals"] == 0
    assert status["error"] is None


async def test_one_run_files_each_drop_kind_under_its_own_key(tmp_path, monkeypatch):
    """The three drops are three shapes, so they are three keys.

    `classify` sees a SchemaSet with `Reference` removed, which is how
    `undeclared-type` is reached without breaking lane construction (the
    `LaneSet` is still built from the full set). A live already-decided
    `Proposal` at `docs/explanations/blocked.md` reaches `plan_file`'s
    `already-decided`. Two suggestions with the same title reach the same-run
    duplicate drop. One run, all three.
    """
    from okf_ext.schemas import load_schemas

    root = make_bundle(tmp_path)
    narrow_dir = tmp_path / "narrow"
    narrow_dir.mkdir()
    for schema in (root / SCHEMA_DIRNAME).glob("*.json"):
        if schema.name != "Reference.schema.json":
            (narrow_dir / schema.name).write_text(schema.read_text(encoding="utf-8"), encoding="utf-8")

    (root / "proposals").mkdir()
    (root / "proposals" / "blocked.md").write_text(
        "---\n"
        "type: Proposal\n"
        "title: Blocked\n"
        "description: d\n"
        "target: docs/explanations/blocked.md\n"
        "page_status: approved\n"
        "generated:\n"
        "  by: agent:test\n"
        "  at: 2026-08-01T00:00:00+00:00\n"
        "sources: []\n"
        "---\n\n"
        "body\n",
        encoding="utf-8",
    )

    full_schema_set, _ = declarations(root)
    payload = json.dumps(
        {
            "suggestions": [
                _suggestion(lane="reference", title="Options", rank=1),
                _suggestion(lane="explanation", title="Blocked", rank=2),
                _suggestion(lane="explanation", title="Same Page", rank=3),
                _suggestion(lane="explanation", title="Same Page", rank=4, description="another angle"),
            ]
        }
    )
    monkeypatch.setattr(
        "graph_works_core.ingest.suggest_pages.make_llm",
        lambda *args, **kwargs: FakeLLM(FakeResponse(payload)),
    )

    async def fake_reasoner(**kwargs):
        return ProposalReasonerResult(status="ok", analysis="a")

    monkeypatch.setattr("graph_works_core.ingest.suggest_pages.run_proposal_reasoner", fake_reasoner)
    reports, status = await run_suggest_phase(
        bundle=load_bundle(root),
        schema_set=load_schemas(narrow_dir),
        lane_set=lane_set(full_schema_set),
        material=Path("/tmp/thing.md"),
        source_text="short",
        source_page="sources/2026-08-thing.md",
        source_title="Thing",
        source_kind="note",
        origin="/tmp/thing.md",
        page_text="body",
        entity_uri=None,
        entity_page=None,
        by="agent:test",
        at=AT,
    )
    assert status["unclassified"] == ["Options: undeclared-type"]
    assert status["refused"] == ["Blocked: already-decided"]
    assert status["duplicates"] == ["Same Page: docs/explanations/same-page.md"]
    assert status["proposals"] == 1
    assert [report["title"] for report in reports] == ["Same Page"]
