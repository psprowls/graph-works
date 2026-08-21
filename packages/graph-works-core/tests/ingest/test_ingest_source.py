"""run_ingest_source end to end: seams, degradation, atomicity, one write."""

from __future__ import annotations

import json
from pathlib import Path

import doc_wiki_okf.cli
import pytest
from graph_works_core.ingest.commands import BUNDLE_IGNORE, run_ingest_source
from graph_works_core.workspace.layout import layout_for
from ingest_helpers import AT, TODAY, FakeLLM, FakeReader, FakeResponse
from suggest_fixtures import make_bundle

_RESPONSE = (
    "---\n"
    "title: A Thing\n"
    "description: One line about the thing.\n"
    "source_kind: spec\n"
    "---\n\n"
    "## TL;DR\n\nIt is a thing.\n\n## Touches\n\n"
)

_SUGGESTIONS = json.dumps(
    {
        "suggestions": [
            {
                "lane": "explanation",
                "title": "Why the thing",
                "rationale": "The source argues for it.",
                "description": "Why.",
                "rank": 1,
                "confidence": "high",
            }
        ]
    }
)


def test_bundle_ignore_is_pinned_to_doc_wiki_okf_cli_ignore():
    """`BUNDLE_IGNORE`'s own comment says a test pins it to the original."""
    assert BUNDLE_IGNORE == doc_wiki_okf.cli.IGNORE


@pytest.fixture
def workspace(tmp_path):
    """A `.works/` layout whose bundle carries the declarations, plus material."""
    root = tmp_path / ".works"
    root.mkdir()
    bundle_root = make_bundle(root)  # -> <root>/okf
    # `repositories` is a MAPPING keyed by repo name, not a list -- see
    # `code_wiki_okf.config.load_config`. `declarations_dir` is absent, which
    # means "at the bundle root", where `make_bundle` installed the seeds.
    (bundle_root / "_repositories.yaml").write_text(
        f"graph_dir: {tmp_path / 'graph'}\nrepositories:\n  repo:\n    path: {tmp_path / 'repo'}\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    material = repo / "docs" / "thing.md"
    material.write_text("# A Thing\n\nSome prose about a thing.\n", encoding="utf-8")
    return layout_for(root), repo, material


def _models(monkeypatch, *, ingestor=_RESPONSE, extractor=_SUGGESTIONS, reasoner="analysis"):
    """Point every `make_llm` call site at a scripted fake."""
    scripts = {"ingestor": ingestor, "extractor": extractor, "proposal_reasoner": reasoner}

    def fake_make_llm(role, **kwargs):
        return FakeLLM(FakeResponse(scripts[role]))

    module_paths = {
        "ingest": "graph_works_core.ingest.commands",
        "suggest_pages": "graph_works_core.ingest.suggest_pages",
        "proposal_reasoner": "graph_works_core.ingest.proposal_reasoner",
    }
    for path in module_paths.values():
        monkeypatch.setattr(f"{path}.make_llm", fake_make_llm, raising=False)


async def test_a_path_and_a_layout_are_enough(workspace, monkeypatch):
    layout, repo, material = workspace
    _models(monkeypatch)
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert result.ok
    assert result.page == "sources/2026-08-a-thing.md"
    assert result.copy == "sources/references/2026-08-a-thing.md"
    assert (layout.bundle_dir / result.page).exists()
    assert (layout.bundle_dir / result.copy).read_text(encoding="utf-8").startswith("# A Thing")


async def test_the_llm_classification_beats_the_hint(workspace, monkeypatch):
    layout, repo, material = workspace
    _models(monkeypatch)
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT, source_kind="article")
    assert result.source_kind == "spec"


async def test_an_out_of_enum_classification_falls_back_to_the_hint(workspace, monkeypatch):
    layout, repo, material = workspace
    _models(monkeypatch, ingestor=_RESPONSE.replace("source_kind: spec", "source_kind: blogpost"))
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT, source_kind="article")
    assert result.source_kind == "article"


async def test_an_absent_matcher_and_gate_still_complete(workspace, monkeypatch):
    layout, repo, material = workspace
    _models(monkeypatch)
    result = await run_ingest_source(
        material, layout=layout, repo=repo, today=TODAY, at=AT, match_entity=None, state_gate=None
    )
    assert result.ok
    assert result.entity_uri is None
    assert result.entity_page is None
    page = (layout.bundle_dir / result.page).read_text(encoding="utf-8")
    assert "entity_uri" not in page
    assert "last_sync_commit" not in page


async def test_a_present_matcher_writes_the_uri_and_the_forward_link(workspace, monkeypatch):
    layout, repo, material = workspace
    _models(monkeypatch)
    from graph_works_core.ingest.entity_match import entity_matcher
    from okf_ext.schemas import load_schemas

    # `make_bundle` installs only doc-wiki-okf's five declarations (Source
    # plus the four Diataxis types); the entity lane's `Package` schema is
    # `code-wiki-okf`'s own, installed separately by a real workspace's scan.
    # Without it `entity_match.page_id_for` declines (logs and returns
    # `None`) rather than raising, so this test adds the one schema file the
    # forward-link path actually needs.
    (layout.bundle_dir / "_schema" / "Package.schema.json").write_text(
        '{"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",'
        ' "required": ["type"], "properties": {"type": {"const": "Package"}},'
        ' "x-okf-directory": "packages/"}',
        encoding="utf-8",
    )
    reader = FakeReader(by_path={"docs/thing.md": ("okf-io", "pkg:okf-io")})
    schema_set = load_schemas(layout.bundle_dir / "_schema")
    result = await run_ingest_source(
        material,
        layout=layout,
        repo=repo,
        today=TODAY,
        at=AT,
        match_entity=entity_matcher(reader, schema_set),
    )
    page = (layout.bundle_dir / result.page).read_text(encoding="utf-8")
    assert result.entity_uri == "pkg:okf-io"
    assert "[/packages/okf-io.md](/packages/okf-io.md)" in page


async def test_a_matcher_and_a_gate_supplied_together_do_not_interfere(workspace, monkeypatch):
    """Both seams wired at once. The matcher still produces its uri and forward
    link; the gate produces nothing observable now that K-F deleted its one
    consumer, and asserting that here is what would catch it quietly coming
    back through the matcher path."""
    layout, repo, material = workspace
    _models(monkeypatch, ingestor=_RESPONSE.replace("source_kind: spec", "source_kind: doc"))
    from graph_works_core.ingest.entity_match import entity_matcher
    from okf_ext.schemas import load_schemas

    # Same schema seed as `test_a_present_matcher_writes_the_uri_and_the_forward_link`:
    # the forward-link path declines silently without `Package.schema.json`.
    (layout.bundle_dir / "_schema" / "Package.schema.json").write_text(
        '{"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",'
        ' "required": ["type"], "properties": {"type": {"const": "Package"}},'
        ' "x-okf-directory": "packages/"}',
        encoding="utf-8",
    )
    reader = FakeReader(by_path={"docs/thing.md": ("okf-io", "pkg:okf-io")})
    schema_set = load_schemas(layout.bundle_dir / "_schema")

    def gate(repo_path, /, *, workspace):
        return {"allowed": True, "reason": "", "head_commit": "cafe1234"}

    result = await run_ingest_source(
        material,
        layout=layout,
        repo=repo,
        today=TODAY,
        at=AT,
        match_entity=entity_matcher(reader, schema_set),
        state_gate=gate,
    )
    page = (layout.bundle_dir / result.page).read_text(encoding="utf-8")
    assert result.entity_uri == "pkg:okf-io"
    assert "[/packages/okf-io.md](/packages/okf-io.md)" in page
    assert "last_sync_commit" not in page


async def test_no_ingest_writes_a_drift_stamp(workspace, monkeypatch):
    """K-F: the one case that used to fire -- kind `doc`, gate `allowed`, a
    head commit present -- now writes nothing. The reference copy under
    `sources/references/` is the drift baseline."""
    layout, repo, material = workspace
    _models(monkeypatch, ingestor=_RESPONSE.replace("source_kind: spec", "source_kind: doc"))

    def gate(repo_path, /, *, workspace):
        return {"allowed": True, "reason": "", "head_commit": "cafe1234"}

    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT, state_gate=gate)
    assert result.ok
    assert "last_sync_commit" not in (layout.bundle_dir / result.page).read_text(encoding="utf-8")


async def test_an_uninitialized_graph_degrades_rather_than_raising(workspace, monkeypatch):
    layout, repo, material = workspace
    _models(monkeypatch)
    # `graph_dir` in `_repositories.yaml` points at a directory with no code.db.
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert result.ok
    assert result.entity_uri is None


async def test_a_real_but_empty_graph_builds_and_closes_its_own_matcher(workspace, monkeypatch, tmp_path):
    """`_matcher_for`'s success path: `open_reader` works, so it builds its own
    `entity_matcher` over the real reader rather than yielding `None`, and
    closes the reader in its `finally` on the way out.

    `code_graph_io.testing.open_store` is the sanctioned test-only seam for
    standing up a real (empty) `code.db` -- `graph_dir` in `_repositories.yaml`
    is `<tmp_path>/graph`, matching the `workspace` fixture. An empty graph has
    no rows to match against, so `entity_uri` stays `None`; the point is that
    the reader opened and closed cleanly, not that it found anything.
    """
    from code_graph_io import testing as graph_testing

    layout, repo, material = workspace
    _models(monkeypatch)
    store = graph_testing.open_store(tmp_path / "graph" / "code.db", create=True)
    store.close()
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert result.ok
    assert result.entity_uri is None


async def test_the_page_and_the_copy_land_together(workspace, monkeypatch):
    layout, repo, material = workspace
    _models(monkeypatch)
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert result.written == (result.page, result.copy)


async def test_a_re_ingest_is_refused_and_lands_neither(workspace, monkeypatch):
    layout, repo, material = workspace
    _models(monkeypatch)
    first = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    (layout.bundle_dir / first.copy).unlink()
    _models(monkeypatch)
    second = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert second.ok is False
    assert second.written == ()
    assert second.refusals
    assert not (layout.bundle_dir / first.copy).exists()


async def test_a_predicted_page_collision_refuses_before_constructing_an_llm(workspace, monkeypatch):
    layout, repo, material = workspace
    target_page = layout.bundle_dir / "sources" / "2026-08-a-thing.md"
    target_page.parent.mkdir(parents=True, exist_ok=True)
    target_page.write_text("---\ntype: Source\n---\n\n## TL;DR\n", encoding="utf-8")

    def fail_if_constructed(*args, **kwargs):
        raise AssertionError("LLM constructed")

    monkeypatch.setattr("graph_works_core.ingest.commands.make_llm", fail_if_constructed)
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)

    assert result.ok is False
    assert result.written == ()
    assert any("already a member" in refusal for refusal in result.refusals)


async def test_a_duplicate_origin_refuses_before_constructing_an_llm(workspace, monkeypatch):
    layout, repo, material = workspace
    origin = "https://example.invalid/already-ingested"
    existing = layout.bundle_dir / "sources" / "existing.md"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("---\ntype: Source\norigin: https://example.invalid/already-ingested\n---\n", encoding="utf-8")

    def fail_if_constructed(*args, **kwargs):
        raise AssertionError("LLM constructed")

    monkeypatch.setattr("graph_works_core.ingest.commands.make_llm", fail_if_constructed)
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT, origin=origin)

    assert result.ok is False
    assert result.written == ()
    assert any("already ingested" in refusal for refusal in result.refusals)


async def test_a_retitled_reingest_with_the_same_origin_is_refused(workspace, monkeypatch):
    """Fix A end to end: `origin` defaults to the material's own path and is
    unchanged between the two calls, so a retitled re-ingest is refused now
    that `plan_ingest` keys its duplicate check on `origin`, not just the
    title-derived page path.
    """
    layout, repo, material = workspace
    _models(monkeypatch)
    first = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert first.ok

    _models(monkeypatch, ingestor=_RETITLED)
    second = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert second.ok is False
    assert second.written == ()
    assert any("already ingested" in refusal for refusal in second.refusals)
    assert not (layout.bundle_dir / "sources" / "2026-08-the-splicing-writer.md").exists()


async def test_relative_and_absolute_spellings_share_one_canonical_fallback_origin(workspace, monkeypatch):
    """Origin identity must refuse a changed predicted path before model construction."""
    layout, repo, material = workspace
    monkeypatch.chdir(repo)
    _models(monkeypatch)
    first = await run_ingest_source(Path("docs/thing.md"), layout=layout, repo=repo, today=TODAY, at=AT)
    assert first.ok

    material.write_text("# A Different Prediction\n\nChanged prose.\n", encoding="utf-8")

    def fail_if_constructed(*args, **kwargs):
        raise AssertionError("duplicate origin must refuse before constructing an LLM")

    monkeypatch.setattr("graph_works_core.ingest.commands.make_llm", fail_if_constructed)
    second = await run_ingest_source(
        material.resolve(),
        layout=layout,
        repo=repo,
        today=TODAY.replace(month=9),
        at=AT,
    )

    assert not second.ok
    assert second.written == ()
    assert any("already ingested" in refusal for refusal in second.refusals)
    assert not (layout.bundle_dir / "sources" / "2026-09-a-different-prediction.md").exists()


async def test_absolute_dot_segment_alias_shares_the_canonical_fallback_origin(workspace, monkeypatch):
    """An absolute ``..`` spelling must refuse before any model construction."""
    layout, repo, material = workspace
    _models(monkeypatch)
    first = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert first.ok

    alias_parent = material.parent / "alias-parent"
    alias_parent.mkdir()
    alias = alias_parent / ".." / material.name
    assert alias.is_absolute()
    assert ".." in alias.parts
    material.write_text("# A Dot Segment Prediction\n\nChanged prose.\n", encoding="utf-8")

    def fail_if_constructed(*args, **kwargs):
        raise AssertionError("canonical origin must refuse before constructing an LLM")

    monkeypatch.setattr("graph_works_core.ingest.commands.make_llm", fail_if_constructed)
    second = await run_ingest_source(
        alias,
        layout=layout,
        repo=repo,
        today=TODAY.replace(month=9),
        at=AT,
    )

    assert not second.ok
    assert second.written == ()
    assert any("already ingested" in refusal for refusal in second.refusals)
    assert not (layout.bundle_dir / "sources" / "2026-09-a-dot-segment-prediction.md").exists()


async def test_symlink_alias_shares_the_canonical_fallback_origin(workspace, monkeypatch):
    """A symlink spelling must refuse before any model construction."""
    layout, repo, material = workspace
    _models(monkeypatch)
    first = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert first.ok

    alias = material.parent / "thing-alias.md"
    try:
        alias.symlink_to(material)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    material.write_text("# A Symlink Prediction\n\nChanged prose.\n", encoding="utf-8")

    def fail_if_constructed(*args, **kwargs):
        raise AssertionError("canonical origin must refuse before constructing an LLM")

    monkeypatch.setattr("graph_works_core.ingest.commands.make_llm", fail_if_constructed)
    second = await run_ingest_source(
        alias,
        layout=layout,
        repo=repo,
        today=TODAY.replace(month=9),
        at=AT,
    )

    assert not second.ok
    assert second.written == ()
    assert any("already ingested" in refusal for refusal in second.refusals)
    assert not (layout.bundle_dir / "sources" / "2026-09-a-symlink-prediction.md").exists()


async def test_exactly_one_write_reaches_the_source_page(workspace, monkeypatch):
    layout, repo, material = workspace
    _models(monkeypatch)
    # `okf_ext.proposals.__init__` does `from okf_ext.proposals.apply import
    # apply`, which -- exactly like `okf_io.validate` -- rebinds the `apply`
    # attribute on the *package* to the function, shadowing the submodule.
    # `import okf_ext.proposals.apply as apply_module` walks that same
    # shadowed attribute chain and would bind the function, not the module;
    # `importlib.import_module` goes through `sys.modules` instead and gets
    # the real submodule, whose own `write_all` name is what `apply()` calls.
    import importlib

    apply_module = importlib.import_module("okf_ext.proposals.apply")

    calls: list[int] = []
    real = apply_module.write_all

    def counting(pending, **kwargs):
        if any("sources/2026-08-a-thing" in item.path.as_posix() for item in pending):
            calls.append(len(pending))
        return real(pending, **kwargs)

    monkeypatch.setattr(apply_module, "write_all", counting)
    await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert calls == [2]  # the page and its copy, in one batch, once


async def test_an_apply_failure_on_the_source_page_is_reported(workspace, monkeypatch):
    layout, repo, material = workspace
    _models(monkeypatch)
    # Same shadowing dodge as `test_exactly_one_write_reaches_the_source_page`:
    # `okf_ext.proposals.apply` (the function) shadows the submodule of the
    # same name, so `importlib.import_module` is what gets the real submodule
    # whose `write_all` name `apply()` actually calls.
    import importlib

    from okf_ext.writing import ApplyResult, WriteFailure

    apply_module = importlib.import_module("okf_ext.proposals.apply")
    real = apply_module.write_all

    def failing(pending, **kwargs):
        if any("sources/2026-08-a-thing" in item.path.as_posix() for item in pending):
            # `write_all`'s own docstring: a staging/probe failure is
            # all-or-nothing for that batch -- nothing in it lands, so
            # `written` comes back empty and every pending item is `failed`.
            # This mirrors that real failure mode rather than inventing a new
            # one, and it targets only the source-page write batch. The main
            # `apply_plan` never succeeds, so `apply_suggestions` is never
            # invoked at all -- there is no proposal-write batch in this run,
            # "unaffected" or otherwise.
            failed = tuple(
                WriteFailure(path=item.member, kind="stage-error", error="synthetic failure for test")
                for item in pending
            )
            return ApplyResult(written=(), failed=failed, skipped=())
        return real(pending, **kwargs)

    monkeypatch.setattr(apply_module, "write_all", failing)
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert result.ok is False
    assert result.refusals
    assert result.written == ()
    assert result.proposals == ()
    assert not (layout.bundle_dir / "proposals").exists() or not list((layout.bundle_dir / "proposals").glob("*.md"))
    assert result.proposal_status["proposals"] == 0


async def test_a_suggest_failure_leaves_the_page_written(workspace, monkeypatch):
    layout, repo, material = workspace
    _models(monkeypatch, extractor="not json")
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert result.ok
    assert result.proposals == ()
    assert result.proposal_status["extractor"] == "failed"
    assert "extractor: failed" in (layout.bundle_dir / result.page).read_text(encoding="utf-8")


async def test_a_reasoner_failure_leaves_the_page_written(workspace, monkeypatch):
    layout, repo, material = workspace
    _models(monkeypatch, reasoner="")
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert result.ok
    assert result.proposal_status["reasoner"] == "failed"


async def test_a_successful_suggest_files_a_proposal(workspace, monkeypatch):
    layout, repo, material = workspace
    _models(monkeypatch)
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert [report["target"] for report in result.proposals] == ["explanations/why-the-thing.md"]
    assert result.proposal_status["proposals"] == 1
    assert "proposals" in result.indexes_updated


async def test_the_index_and_the_log_are_reconciled(workspace, monkeypatch):
    layout, repo, material = workspace
    _models(monkeypatch)
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert "sources" in result.indexes_updated
    assert (layout.bundle_dir / "sources" / "index.md").exists()
    log = (layout.bundle_dir / "log.md").read_text(encoding="utf-8")
    assert "2026-08-13" in log
    assert "A Thing" in log


async def test_a_refused_suggestion_is_reported_in_the_log_by_kind(workspace, monkeypatch):
    """A `plan_file` refusal lands in `status["refused"]`, not `unclassified`,
    and `_log_line` names that kind rather than collapsing every drop into one
    "N suggestion(s) dropped" count.

    Seeding an already-decided `Proposal` at the exact target the extractor's
    lone suggestion resolves to (`explanations/blocked.md`) is the same setup
    `test_suggest_pages.test_a_plan_file_refusal_drops_the_suggestion_and_records_the_reason`
    uses -- `plan_propose` refuses a re-proposal against a decided proposal
    with `already-decided`.
    """
    layout, repo, material = workspace
    (layout.bundle_dir / "proposals").mkdir(exist_ok=True)
    (layout.bundle_dir / "proposals" / "blocked.md").write_text(
        "---\n"
        "type: Proposal\n"
        "title: Blocked\n"
        "description: d\n"
        "target: explanations/blocked.md\n"
        "page_status: approved\n"
        "generated:\n"
        "  by: agent:test\n"
        "  at: 2026-08-01T00:00:00+00:00\n"
        "sources: []\n"
        "---\n\n"
        "body\n",
        encoding="utf-8",
    )
    blocked_suggestion = json.dumps(
        {
            "suggestions": [
                {
                    "lane": "explanation",
                    "title": "Blocked",
                    "rationale": "The source argues for it.",
                    "description": "Why.",
                    "rank": 1,
                    "confidence": "high",
                }
            ]
        }
    )
    _models(monkeypatch, extractor=blocked_suggestion)
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert result.ok
    assert result.proposal_status["refused"] == ["Blocked: already-decided"]
    log = (layout.bundle_dir / "log.md").read_text(encoding="utf-8")
    assert "1 refused" in log


async def test_a_raising_suggestion_is_recorded_and_the_next_one_still_files(workspace, monkeypatch):
    """C1: the per-suggestion loop body is not a coincidence anymore. A
    `classify` crash on one suggestion is caught, recorded in
    `proposal_status["errored"]`, and does not stop a later suggestion in the
    same run from filing -- nor does it stop the source page itself from
    landing.
    """
    from doc_wiki_okf.diataxis.classify import classify as real_classify

    layout, repo, material = workspace
    two_suggestions = json.dumps(
        {
            "suggestions": [
                {
                    "lane": "explanation",
                    "title": "Why the thing",
                    "rationale": "The source argues for it.",
                    "description": "Why.",
                    "rank": 1,
                    "confidence": "high",
                },
                {
                    "lane": "explanation",
                    "title": "How the thing works",
                    "rationale": "The source explains it.",
                    "description": "How.",
                    "rank": 2,
                    "confidence": "high",
                },
            ]
        }
    )
    _models(monkeypatch, extractor=two_suggestions)

    def raising_classify(*args, **kwargs):
        if kwargs.get("title") == "Why the thing":
            raise RuntimeError("boom")
        return real_classify(*args, **kwargs)

    monkeypatch.setattr("graph_works_core.ingest.suggest_pages.classify", raising_classify)

    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)

    assert result.ok
    assert (layout.bundle_dir / result.page).exists()
    assert [report["title"] for report in result.proposals] == ["How the thing works"]
    assert result.proposal_status["errored"] == ["Why the thing: RuntimeError"]
    log = (layout.bundle_dir / "log.md").read_text(encoding="utf-8")
    assert "1 errored" in log


async def test_a_non_default_ingest_layout_threads_through_page_copy_index_and_prompt(workspace, monkeypatch):
    """C2: one `IngestLayout`, threaded, rather than four derivations that
    only agree by luck. `page_target`, `plan_ingest`, `plan_document_brief`
    and the reconciled index directories all take the same non-default
    layout, so `IngestResult.page` matches what actually landed, the copy is
    beside it, the new lane's index is the one reconciled -- not `sources/` --
    and the ingestor's own prompt quotes the same path it was told about.
    """
    from doc_wiki_okf.ingest.layout import IngestLayout

    layout, repo, material = workspace
    ingest_layout = IngestLayout(source_page_template="material/{month}-{slug}.md")

    llms: dict[str, FakeLLM] = {}

    def fake_make_llm(role, **kwargs):
        script = {"ingestor": _RESPONSE, "extractor": _SUGGESTIONS, "proposal_reasoner": "analysis"}[role]
        llm = FakeLLM(FakeResponse(script))
        llms[role] = llm
        return llm

    module_paths = {
        "ingest": "graph_works_core.ingest.commands",
        "suggest_pages": "graph_works_core.ingest.suggest_pages",
        "proposal_reasoner": "graph_works_core.ingest.proposal_reasoner",
    }
    for path in module_paths.values():
        monkeypatch.setattr(f"{path}.make_llm", fake_make_llm, raising=False)

    result = await run_ingest_source(
        material, layout=layout, repo=repo, today=TODAY, at=AT, ingest_layout=ingest_layout
    )

    assert result.ok
    assert result.page == "material/2026-08-a-thing.md"
    assert result.copy == "material/references/2026-08-a-thing.md"
    assert (layout.bundle_dir / result.page).exists()
    assert (layout.bundle_dir / result.copy).exists()
    assert (layout.bundle_dir / "material" / "index.md").exists()
    assert not (layout.bundle_dir / "sources" / "index.md").exists()
    assert "material" in result.indexes_updated

    human_message = llms["ingestor"].calls[0][1].content
    assert "material/2026-08-a-thing.md" in human_message


def _script_llms(monkeypatch, *, ingestor=_RESPONSE, extractor=_SUGGESTIONS, reasoner="analysis"):
    """Like `_models`, but keeps a handle to each role's `FakeLLM` so a test
    can inspect what it was actually sent."""
    scripts = {"ingestor": ingestor, "extractor": extractor, "proposal_reasoner": reasoner}
    llms: dict[str, FakeLLM] = {}

    def fake_make_llm(role, **kwargs):
        llm = FakeLLM(FakeResponse(scripts[role]))
        llms[role] = llm
        return llm

    module_paths = {
        "ingest": "graph_works_core.ingest.commands",
        "suggest_pages": "graph_works_core.ingest.suggest_pages",
        "proposal_reasoner": "graph_works_core.ingest.proposal_reasoner",
    }
    for path in module_paths.values():
        monkeypatch.setattr(f"{path}.make_llm", fake_make_llm, raising=False)
    return llms


async def test_html_source_text_reaches_the_reasoner_stripped_and_the_copy_is_byte_exact(workspace, monkeypatch):
    """C3: the reasoner reads the same rendering the ingestor read -- tags
    stripped -- not a raw re-decode of `.html` tag soup. The reference copy on
    disk is unaffected: `plan_ingest` still writes the raw decode.
    """
    layout, repo, _material = workspace
    html_material = repo / "docs" / "thing.html"
    original_bytes = b"<html><body><h1>A Thing</h1><p>Some <b>prose</b> about a thing.</p></body></html>"
    html_material.write_bytes(original_bytes)

    llms = _script_llms(monkeypatch)
    result = await run_ingest_source(html_material, layout=layout, repo=repo, today=TODAY, at=AT)

    assert result.ok
    reasoner_prompt = llms["proposal_reasoner"].calls[0][1].content
    assert "<h1>" not in reasoner_prompt
    assert "<b>" not in reasoner_prompt
    assert "A Thing" in reasoner_prompt
    assert (layout.bundle_dir / result.copy).read_bytes() == original_bytes


async def test_a_long_markdown_source_still_reaches_the_reasoner_whole(workspace, monkeypatch):
    """C3's guard against 'fixed it with `brief.preview`': `preview` is capped
    at `PREVIEW_CHARS` (1200), so a fix that handed the reasoner `brief.preview`
    would truncate it. The reasoner needs the full extract.
    """
    layout, repo, _material = workspace
    long_material = repo / "docs" / "long.md"
    filler = "x " * 700  # > PREVIEW_CHARS
    long_material.write_text(f"# Long\n\n{filler}\n\nFINAL LINE MARKER\n", encoding="utf-8")

    llms = _script_llms(monkeypatch)
    result = await run_ingest_source(long_material, layout=layout, repo=repo, today=TODAY, at=AT)

    assert result.ok
    reasoner_prompt = llms["proposal_reasoner"].calls[0][1].content
    assert "FINAL LINE MARKER" in reasoner_prompt


async def test_a_missing_root_log_is_skipped_not_raised(workspace, monkeypatch):
    """No root `log.md` -- `reconciled.logs.get("")` is `None`, so
    `append_log_entry` is never called. The ingest still completes.
    """
    layout, repo, material = workspace
    (layout.bundle_dir / "log.md").unlink()
    _models(monkeypatch)
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert result.ok
    assert not (layout.bundle_dir / "log.md").exists()


async def test_a_non_text_ingestor_response_is_a_failure(workspace, monkeypatch):
    layout, repo, material = workspace
    _models(monkeypatch, ingestor=[{"type": "text", "text": "not actually a string"}])
    with pytest.raises(RuntimeError, match="non-text content"):
        await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)


async def test_a_response_with_no_frontmatter_still_lands(workspace, monkeypatch):
    layout, repo, material = workspace
    _models(monkeypatch, ingestor="Just prose, no frontmatter at all.\n")
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT, source_kind="article")
    assert result.ok
    assert result.frontmatter_parsed is False
    assert result.source_kind == "article"
    assert "Just prose" in (layout.bundle_dir / result.page).read_text(encoding="utf-8")
    assert result.title == "A Thing"
    assert result.page == "sources/2026-08-a-thing.md"


async def test_a_blank_model_title_falls_back_to_the_material_heading(workspace, monkeypatch):
    """The `.strip() or brief.title` half of the fallback, which the
    no-frontmatter case does not reach: here the frontmatter parses and
    carries a `title`, it is just whitespace.
    """
    layout, repo, material = workspace
    _models(monkeypatch, ingestor=_RESPONSE.replace("title: A Thing", 'title: "   "'))
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert result.ok
    assert result.frontmatter_parsed is True
    assert result.title == "A Thing"
    assert result.page == "sources/2026-08-a-thing.md"


async def test_graph_tools_are_filtered_before_reaching_the_reasoner(workspace, monkeypatch):
    layout, repo, material = workspace
    _models(monkeypatch)
    from langchain_core.tools import tool

    seen: list[set[str]] = []

    @tool
    def cg_find(query: str) -> str:
        """find"""
        return query

    @tool
    def cg_delete(query: str) -> str:
        """delete"""
        return query

    real_bind = FakeLLM.bind_tools

    def spy(self, tools):
        seen.append({each.name for each in tools})
        return real_bind(self, tools)

    monkeypatch.setattr(FakeLLM, "bind_tools", spy)
    await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT, graph_tools=(cg_find, cg_delete))
    assert seen and "cg_find" in seen[0] and "cg_delete" not in seen[0]


async def test_binary_material_lands_with_a_content_blind_brief(workspace, monkeypatch):
    """B-G: the copy is byte-perfect and the page does not describe contents no
    one read. Deleting the refusal without this would land a correct copy beside
    a page composed from replacement characters."""
    layout, repo, _material = workspace
    data = b"%PDF-1.4\n\xff\xfe\x00binary\n"
    pdf = repo / "docs" / "scan.pdf"
    pdf.write_bytes(data)

    llms = _script_llms(monkeypatch)
    result = await run_ingest_source(pdf, layout=layout, repo=repo, today=TODAY, at=AT)

    assert result.ok, result.refusals
    assert result.refusals == ()
    assert (layout.bundle_dir / result.copy).read_bytes() == data
    assert result.copy.endswith(".pdf")

    prompt = llms["ingestor"].calls[0][1].content
    assert "--- Source content ---" not in prompt
    assert "binary" in prompt.lower()
    assert "scan.pdf" in prompt
    assert ".pdf" in prompt
    assert str(len(data)) in prompt
    assert "�" not in prompt


async def test_binary_material_skips_the_suggest_phase(workspace, monkeypatch):
    """P-2, end to end: no reasoner call, so no proposal cites a source nobody
    could read."""
    layout, repo, _material = workspace
    pdf = repo / "docs" / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n\xff\xfe\x00binary\n")

    _models(monkeypatch)
    result = await run_ingest_source(pdf, layout=layout, repo=repo, today=TODAY, at=AT)

    assert result.ok
    assert result.proposal_status["reasoner"] == "skipped"
    assert result.proposal_status["proposals"] == 0
    assert result.proposals == ()


async def test_text_material_still_gets_the_source_content_block(workspace, monkeypatch):
    """The binary branch is a branch, not a replacement."""
    layout, repo, material = workspace
    llms = _script_llms(monkeypatch)
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)

    assert result.ok
    prompt = llms["ingestor"].calls[0][1].content
    assert "--- Source content ---" in prompt
    assert "Some prose about a thing." in prompt


async def test_a_write_failure_in_the_suggest_phase_reaches_the_log(workspace, monkeypatch):
    """The count in `log.md` and in `proposal_status` is what actually landed."""
    layout, repo, material = workspace
    (layout.bundle_dir / "proposals").write_text("not a directory\n", encoding="utf-8")
    _models(monkeypatch)
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert result.ok
    assert result.proposal_status["proposals"] == 0
    assert result.proposal_status["failed"] == ["Why the thing: mkdir-error"]
    log = (layout.bundle_dir / "log.md").read_text(encoding="utf-8")
    assert "0 proposal(s) filed" in log
    assert "1 failed to write" in log


async def test_an_apply_time_proposal_error_reaches_proposal_status_via_the_shared_merge(workspace, monkeypatch):
    """Issue 3: `commands.py`'s own `merge_apply_status` call, not just `suggest_pages.py`'s.

    Forces `apply_suggestions`' `apply_plan` call to raise for the one
    classified suggestion, while the main page's own `apply_plan` call (a
    separate module-level name in `commands.py`) succeeds untouched -- so
    this drives an `errored` apply-time entry through `run_ingest_source`
    end to end, the callsite `test_run_suggest_phase_merges_plan_time_and_apply_time_errored_entries_in_order`
    covers only for `run_suggest_phase`'s own copy of the merge.
    """
    layout, repo, material = workspace
    _models(monkeypatch)

    from okf_ext.proposals import apply as real_apply_plan

    def fake_apply(bundle_arg, plan):
        if plan.target == "explanations/why-the-thing.md":
            raise RuntimeError("boom-apply")
        return real_apply_plan(bundle_arg, plan)

    monkeypatch.setattr("graph_works_core.ingest.suggest_pages.apply_plan", fake_apply)
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert result.ok
    assert result.proposals == ()
    assert result.proposal_status["proposals"] == 0
    assert result.proposal_status["errored"] == ["Why the thing: RuntimeError"]


async def test_orphan_proposals_never_land_when_the_main_plan_is_refused(workspace, monkeypatch):
    """Fix B end to end: a refused main write means `apply_suggestions` is
    never called, so the suggest phase's classifiable suggestion never lands
    as a proposal citing a `resource` for a page that was never created.
    """
    layout, repo, material = workspace
    # Pre-create the page `run_ingest_source` will target, so `plan_ingest`
    # refuses the write on the very first attempt -- same title-collision
    # mechanism `test_a_re_ingest_is_refused_and_lands_neither` uses on its
    # second call, forced here on the first.
    target_page = layout.bundle_dir / "sources" / "2026-08-a-thing.md"
    target_page.parent.mkdir(parents=True, exist_ok=True)
    target_page.write_text("---\ntype: Source\n---\n\n## TL;DR\n", encoding="utf-8")
    _models(monkeypatch)
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert result.ok is False
    assert result.written == ()
    assert not (layout.bundle_dir / "proposals").exists() or not list((layout.bundle_dir / "proposals").glob("*.md"))
    assert result.proposal_status["proposals"] == 0


async def test_a_refusal_with_no_suggestions_still_leaves_written_empty(workspace, monkeypatch):
    """The refused ⇒ `written == ()` behavior is unchanged when the suggest
    phase produces nothing to plan in the first place."""
    layout, repo, material = workspace
    target_page = layout.bundle_dir / "sources" / "2026-08-a-thing.md"
    target_page.parent.mkdir(parents=True, exist_ok=True)
    target_page.write_text("---\ntype: Source\n---\n\n## TL;DR\n", encoding="utf-8")
    _models(monkeypatch, extractor=json.dumps({"suggestions": []}))
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert result.ok is False
    assert result.written == ()
    assert result.proposals == ()


async def test_a_clean_runs_page_carries_no_blank_proposal_status_keys(workspace, monkeypatch):
    """F2: the status mapping is filtered by `plan_ingest`'s own blank rule.

    Asserted on the parsed frontmatter, not on a substring: `error` is a
    common enough word in a source page's body that a `"error" not in text`
    check would pass or fail for the wrong reason.
    """
    from okf_io import parse

    layout, repo, material = workspace
    _models(monkeypatch)
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert result.ok
    page = parse((layout.bundle_dir / result.page).read_text(encoding="utf-8"))
    assert set(page.fm_data()["proposal_status"]) == {"reasoner", "extractor", "proposals"}


async def test_the_reasoner_is_told_the_validated_source_kind_not_the_hint(workspace, monkeypatch):
    """D3 end to end: the prompt carries `validated`, not `brief.source_kind`.

    The unit test over `build_reasoner_prompt` pins the rendering; only this
    one pins the wiring. The caller hints `note` and the model classifies
    `spec`, so a regression threading the hint through `run_ingest_source`
    would still render a well-formed `Source kind:` line -- and would be
    invisible to every other test in this suite.
    """
    layout, repo, material = workspace
    llms = _script_llms(monkeypatch)
    result = await run_ingest_source(
        material,
        layout=layout,
        repo=repo,
        today=TODAY,
        at=AT,
        source_kind="note",
        origin="https://example.invalid/spec",
    )
    assert result.ok
    assert result.source_kind == "spec"
    reasoner_prompt = llms["proposal_reasoner"].calls[0][1].content
    assert "Source kind: spec" in reasoner_prompt
    assert "Source kind: note" not in reasoner_prompt
    assert "Origin: https://example.invalid/spec" in reasoner_prompt


async def test_the_reasoners_origin_falls_back_to_the_material_path(workspace, monkeypatch):
    """D3: `origin`'s default ("") resolves to `str(material)`, and the
    resolved value -- not the blank default -- is what reaches the reasoner.
    """
    layout, repo, material = workspace
    llms = _script_llms(monkeypatch)
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert result.ok
    reasoner_prompt = llms["proposal_reasoner"].calls[0][1].content
    assert f"Origin: {material}" in reasoner_prompt


_RETITLED = _RESPONSE.replace("title: A Thing", "title: The Splicing Writer")


async def test_the_models_title_decides_the_page_and_the_copy(workspace, monkeypatch):
    """D1: the model's title wins over the material's H1.

    No matcher is supplied and the fixture's `graph_dir` holds no `code.db`, so
    the re-run's `_matcher_for` yields `None` -- the degraded half of the
    re-match, which must still produce a page rather than raising.
    """
    layout, repo, material = workspace
    _models(monkeypatch, ingestor=_RETITLED)
    result = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT)
    assert result.ok
    assert result.title == "The Splicing Writer"
    assert result.page == "sources/2026-08-the-splicing-writer.md"
    assert result.copy == "sources/references/2026-08-the-splicing-writer.md"
    assert (layout.bundle_dir / result.page).exists()
    assert (layout.bundle_dir / result.copy).exists()
    assert not (layout.bundle_dir / "sources" / "2026-08-a-thing.md").exists()
    assert result.entity_uri is None


async def test_the_entity_match_re_runs_against_the_models_title(workspace, monkeypatch):
    """The match that matters is the one on the title the page is named for.

    `FakeReader` is keyed on the **model's** name and not the H1's, and the
    path lookup is empty, so the URI can only come from a second match run
    against the resolved title.
    """
    from graph_works_core.ingest.entity_match import entity_matcher
    from okf_ext.schemas import load_schemas

    layout, repo, material = workspace
    _models(monkeypatch, ingestor=_RETITLED)
    # Same schema seed as the forward-link tests above: without
    # `Package.schema.json`, `page_id_for` declines and writes no link.
    (layout.bundle_dir / "_schema" / "Package.schema.json").write_text(
        '{"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",'
        ' "required": ["type"], "properties": {"type": {"const": "Package"}},'
        ' "x-okf-directory": "packages/"}',
        encoding="utf-8",
    )
    reader = FakeReader(by_name={"The Splicing Writer": [("okf-io", "pkg:okf-io", "package")]})
    schema_set = load_schemas(layout.bundle_dir / "_schema")
    result = await run_ingest_source(
        material,
        layout=layout,
        repo=repo,
        today=TODAY,
        at=AT,
        match_entity=entity_matcher(reader, schema_set),
    )
    assert result.entity_uri == "pkg:okf-io"
    assert "[/packages/okf-io.md](/packages/okf-io.md)" in (layout.bundle_dir / result.page).read_text(encoding="utf-8")


async def test_the_entity_match_is_not_re_run_when_the_model_agrees(workspace, monkeypatch):
    """The common path costs nothing. The model agreeing with the H1 is the
    usual case, and re-matching it would be a second graph read for an answer
    already in hand.
    """
    from doc_wiki_okf.ingest.seams import NO_ENTITY

    layout, repo, material = workspace
    seen: list[str] = []

    def counting(repo_path, source, title, /):
        seen.append(title)
        return NO_ENTITY

    _models(monkeypatch)
    first = await run_ingest_source(material, layout=layout, repo=repo, today=TODAY, at=AT, match_entity=counting)
    assert seen == ["A Thing"]

    # The next call exercises the model-title path rather than re-ingest
    # protection, so remove the first page and copy that now intentionally
    # refuse the same predicted identity before a model can run.
    (layout.bundle_dir / first.page).unlink()
    (layout.bundle_dir / first.copy).unlink()
    seen.clear()
    _models(monkeypatch, ingestor=_RETITLED)
    await run_ingest_source(
        material,
        layout=layout,
        repo=repo,
        today=TODAY,
        at=AT,
        match_entity=counting,
        origin="https://example.invalid/retitled",
    )
    assert seen == ["A Thing", "The Splicing Writer"]
