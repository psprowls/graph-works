"""The lint aggregator. The mechanical half is a fixture workspace with one
known violation per topic, asserted as a hand-written code set — okf-io's
`test_catalog.py` idiom, where the hand-written set is what keeps a
regenerated golden honest."""

from __future__ import annotations

from datetime import date

import pytest
from code_wiki_okf.config import load_config
from graph_works_core import apply_init, plan_init
from graph_works_core.lint_drift.lint import LaneReport, LintReport, run_mechanical

TODAY = date(2026, 8, 13)


@pytest.fixture
def workspace(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return apply_init(plan_init(repo / ".works", today=TODAY, topic="Lint"))


def _run(workspace, **kwargs) -> LintReport:
    layout = workspace.layout
    return run_mechanical(layout, load_config(layout.bundle_dir), today=TODAY, repo_root=layout.repo_root, **kwargs)


def _codes(report: LintReport) -> set[str]:
    return {finding.code for lane in report.mechanical for finding in lane.report.findings}


def _topics(report: LintReport) -> set[str]:
    return {code.split(".", 1)[0] for code in _codes(report)}


def test_a_fresh_workspace_reports_one_lane_report_per_lane_in_lane_order(workspace):
    report = _run(workspace)
    assert [lane.name for lane in report.mechanical] == ["wiki", "work"]
    assert all(isinstance(lane, LaneReport) for lane in report.mechanical)


def test_the_mechanical_pass_makes_no_semantic_findings(workspace):
    assert _run(workspace).semantic == ()


def test_a_fresh_workspace_has_no_errors_and_no_open_proposals(workspace):
    report = _run(workspace)
    assert report.errors == ()
    assert report.open_proposals.count == 0


def test_ok_is_true_exactly_when_every_lane_is_ok_and_there_are_no_errors(workspace):
    report = _run(workspace)
    assert report.ok == (all(lane.report.ok for lane in report.mechanical) and not report.errors)


def test_a_page_with_an_undeclared_type_trips_the_schema_topic(workspace):
    bundle = workspace.layout.bundle_dir
    (bundle / "concepts").mkdir(exist_ok=True)
    (bundle / "concepts" / "loose.md").write_text(
        "---\ntype: NotADeclaredType\ntitle: Loose\n---\n\nBody.\n", encoding="utf-8"
    )
    assert "schemas" in _topics(_run(workspace))


def test_a_work_item_with_a_broken_plan_target_trips_the_work_lane(workspace):
    """The work-lane rules self-scope to `work/<slug>.md`, so this fires in the
    work lane's report and nowhere else."""
    work = workspace.layout.bundle_dir / "work"
    work.mkdir(exist_ok=True)
    (work / "2026-08-13-bug-example.md").write_text(
        "---\n"
        "type: Bug\n"
        "title: Example\n"
        "status: accepted\n"
        "---\n\n"
        "## Plan\n\n"
        "| Action | Done when | Rationale |\n"
        "| --- | --- | --- |\n"
        "| Edit packages/nope/does-not-exist.py | it builds | because |\n",
        encoding="utf-8",
    )
    report = _run(workspace)
    by_lane = {lane.name: {f.code for f in lane.report.findings} for lane in report.mechanical}
    assert any(code.startswith("plan.") for code in by_lane["work"])
    assert not any(code.startswith("plan.") for code in by_lane["wiki"])


def test_a_malformed_declaration_becomes_an_error_line_and_the_other_lane_still_reports(workspace):
    (workspace.layout.config_dir / "_tags.yaml").write_text("not: [a mapping\n", encoding="utf-8")
    report = _run(workspace)
    assert len(report.errors) == 1
    assert [lane.name for lane in report.mechanical] == ["work"]
    assert report.ok is False


def test_a_repositories_yaml_edited_to_move_graph_dir_is_reported_as_drift(workspace):
    """`init.py` seeds `_repositories.yaml`'s `graph_dir` from `layout.cache_dir`
    so the two config surfaces agree at birth. Nothing else keeps them that
    way -- this is the check that catches a human moving `graph_dir` in
    `_repositories.yaml` without moving the layout to match."""
    repositories = workspace.layout.bundle_dir / "_repositories.yaml"
    text = repositories.read_text(encoding="utf-8")
    (workspace.layout.bundle_dir / "elsewhere_cache").mkdir()
    drifted = "\n".join(
        line if not line.startswith("graph_dir:") else 'graph_dir: "elsewhere_cache"' for line in text.splitlines()
    )
    repositories.write_text(drifted + "\n", encoding="utf-8")

    report = _run(workspace)
    assert any("graph_dir" in error and "drifted" in error for error in report.errors)
    assert report.ok is False


def test_a_repositories_yaml_edited_to_move_declarations_dir_is_reported_as_drift(workspace):
    repositories = workspace.layout.bundle_dir / "_repositories.yaml"
    text = repositories.read_text(encoding="utf-8")
    (workspace.layout.bundle_dir / "elsewhere_config").mkdir()
    drifted = "\n".join(
        line if not line.startswith("declarations_dir:") else 'declarations_dir: "elsewhere_config"'
        for line in text.splitlines()
    )
    repositories.write_text(drifted + "\n", encoding="utf-8")

    report = _run(workspace)
    assert any("declarations_dir" in error and "drifted" in error for error in report.errors)
    assert report.ok is False


def test_an_unwalkable_lane_root_is_an_error_and_not_a_raise(workspace, monkeypatch):
    from graph_works_core.lint_drift import lint as lint_module

    calls = {"n": 0}

    def _boom(root, *, ignore=()):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk went away")
        return _real_load(root, ignore=ignore)

    _real_load = lint_module.load_bundle
    monkeypatch.setattr(lint_module, "load_bundle", _boom)
    report = _run(workspace)
    assert len(report.errors) == 1
    assert "disk went away" in report.errors[0]
    assert [lane.name for lane in report.mechanical] == ["work"]


def test_a_validate_failure_propagates_rather_than_becoming_an_error_line(workspace, monkeypatch):
    """validate() exceptions are rule bugs or genuine environment problems
    (e.g. a permission-denied path check inside a rule) — okf-io's contract is
    that these propagate loudly, never get silently folded into a lane error."""
    from graph_works_core.lint_drift import lint as lint_module

    def _boom(*a, **k):
        raise RuntimeError("a rule blew up")

    monkeypatch.setattr(lint_module, "validate", _boom)
    with pytest.raises(RuntimeError, match="a rule blew up"):
        _run(workspace)


def test_open_proposals_counts_only_the_proposed_ones(workspace):
    proposals = workspace.layout.bundle_dir / "proposals"
    proposals.mkdir(exist_ok=True)
    for slug, status in (("open-one", "proposed"), ("open-two", "proposed"), ("done", "approved")):
        (proposals / f"{slug}.md").write_text(
            f"---\ntype: Proposal\ntitle: {slug}\ntarget: concepts/{slug}.md\npage_status: {status}\n---\n\nWhy.\n",
            encoding="utf-8",
        )
    assert _run(workspace).open_proposals.count == 2


def _proposal(slug: str, status: str, *, generated: str | None = None) -> str:
    stamp = "" if generated is None else f"generated:\n  at: {generated}\n"
    return (
        f"---\ntype: Proposal\ntitle: {slug}\ntarget: concepts/{slug}.md\npage_status: {status}\n{stamp}---\n\nWhy.\n"
    )


def test_the_backlog_spreads_open_proposals_across_the_four_age_buckets(workspace):
    """D8: a bare count cannot tell twelve proposals filed yesterday from
    twelve that have sat a month because someone already fixed them."""
    from graph_works_core.lint_drift.lint import AGE_BUCKETS

    proposals_dir = workspace.layout.bundle_dir / "proposals"
    proposals_dir.mkdir(exist_ok=True)
    for slug, generated in (
        ("fresh", "2026-08-10T00:00:00Z"),
        ("recent", "2026-08-01T00:00:00Z"),
        ("old", "2026-06-01T00:00:00Z"),
        ("undated", None),
    ):
        (proposals_dir / f"{slug}.md").write_text(_proposal(slug, "proposed", generated=generated), encoding="utf-8")

    backlog = _run(workspace).open_proposals
    assert backlog.count == 4
    assert dict(backlog.ages) == {"<7d": 1, "7-30d": 1, ">30d": 1, "undated": 1}
    assert sum(backlog.ages.values()) == backlog.count
    assert set(backlog.ages) == set(AGE_BUCKETS)
    assert backlog.oldest == date(2026, 6, 1)


def test_a_malformed_page_status_is_counted_rather_than_invisible(workspace):
    """D10's lint half: `list_proposals`' filter matches the coerced value, so
    a malformed proposal was absent from the backlog at the same time it was
    unable to suppress anything."""
    proposals_dir = workspace.layout.bundle_dir / "proposals"
    proposals_dir.mkdir(exist_ok=True)
    (proposals_dir / "good.md").write_text(_proposal("good", "proposed"), encoding="utf-8")
    (proposals_dir / "broken.md").write_text(_proposal("broken", "banana"), encoding="utf-8")

    backlog = _run(workspace).open_proposals
    assert backlog.count == 1
    assert backlog.malformed == 1


def test_a_wiki_lane_that_never_loaded_reports_an_empty_backlog(workspace, monkeypatch):
    """A lane whose bundle is absent says nothing about its backlog, and an
    empty one alongside that lane's error line is less misleading than
    omitting the field."""
    from graph_works_core.lint_drift.lint import ProposalBacklog

    def _boom(root, *, ignore=()):
        raise OSError("disk went away")

    monkeypatch.setattr(lint_module, "load_bundle", _boom)
    assert _run(workspace).open_proposals == ProposalBacklog()


# --------------------------------------------------------------------------
# The semantic pass. Every model is a fake: no network, no credential.
# --------------------------------------------------------------------------

from graph_works_core.lint_drift import lint as lint_module  # noqa: E402
from graph_works_core.lint_drift.lint import run_lint  # noqa: E402
from subagents_io.roles import RoleBinding, RoleSpec  # noqa: E402

SPEC = RoleSpec(model_id="fake.model.v1", max_concurrency=3)


class _Reply:
    def __init__(self, content: str):
        self.content = content
        self.usage_metadata = None


class _FakeLLM:
    """Returns one canned reply for every group, recording what it was asked."""

    def __init__(self, content: str = "finding one\nfinding two"):
        self.content = content
        self.calls: list[list[object]] = []

    async def ainvoke(self, messages: list[object]) -> _Reply:
        self.calls.append(list(messages))
        return _Reply(self.content)


class _BoomLLM:
    async def ainvoke(self, messages: list[object]) -> _Reply:
        raise RuntimeError("the model refused")


def _bind(llm) -> RoleBinding:
    return RoleBinding(spec=SPEC, make_llm=lambda: llm)


@pytest.fixture
def curated(workspace):
    """One ADR, one page with `sources[]`, and one plain concept."""
    bundle = workspace.layout.bundle_dir
    (bundle / "adrs").mkdir(exist_ok=True)
    (bundle / "adrs" / "0001-pick-okf.md").write_text(
        "---\ntype: Explanation\ntitle: Pick OKF\n---\n\nAccepted.\n", encoding="utf-8"
    )
    (bundle / "concepts").mkdir(exist_ok=True)
    (bundle / "concepts" / "cited.md").write_text(
        "---\ntype: Explanation\ntitle: Cited\nsources:\n  - id: src-a\n    resource: /src/a.py\n---\n\nBody.\n",
        encoding="utf-8",
    )
    (bundle / "concepts" / "plain.md").write_text(
        "---\ntype: Explanation\ntitle: Plain\n---\n\nBody.\n", encoding="utf-8"
    )
    return workspace


async def _lint(workspace, **kwargs) -> LintReport:
    layout = workspace.layout
    return await run_lint(layout, load_config(layout.bundle_dir), today=TODAY, repo_root=layout.repo_root, **kwargs)


async def test_the_mechanical_half_is_unchanged_by_the_semantic_pass(curated, monkeypatch):
    monkeypatch.setattr(lint_module, "role_binding", lambda *a, **k: _bind(_FakeLLM()))
    semantic = await _lint(curated)
    mechanical = _run(curated)
    assert [lane.name for lane in semantic.mechanical] == [lane.name for lane in mechanical.mechanical]
    assert _codes(semantic) == _codes(mechanical)


async def test_every_group_that_has_pages_produces_one_finding_per_reply_line(curated, monkeypatch):
    monkeypatch.setattr(lint_module, "role_binding", lambda *a, **k: _bind(_FakeLLM()))
    report = await _lint(curated)
    by_group: dict[str, int] = {}
    for finding in report.semantic:
        by_group[finding.group] = by_group.get(finding.group, 0) + 1
    assert by_group == {"page_quality": 2, "adr_chain": 2, "stale_claims": 2}
    assert {f.model for f in report.semantic} == {"fake.model.v1"}


async def test_an_empty_group_never_invokes_the_model(workspace, monkeypatch):
    """A fresh workspace has no ADRs and no page carrying `sources[]`, so only
    `page_quality` may reach the model — and only if the scaffold left a page."""
    llm = _FakeLLM()
    monkeypatch.setattr(lint_module, "role_binding", lambda *a, **k: _bind(llm))
    report = await _lint(workspace)
    assert {f.group for f in report.semantic} <= {"page_quality"}
    assert len(llm.calls) <= 1


async def test_a_named_page_is_resolved_and_an_unnamed_one_is_not(curated, monkeypatch):
    llm = _FakeLLM("concepts/cited: this claim is stale\nno page named here")
    monkeypatch.setattr(lint_module, "role_binding", lambda *a, **k: _bind(llm))
    report = await _lint(curated)
    pages = {f.page for f in report.semantic}
    assert "concepts/cited" in pages
    assert None in pages
    named = next(f for f in report.semantic if f.page == "concepts/cited")
    assert named.message == "this claim is stale"


async def test_a_group_that_raises_is_captured_and_the_others_still_report(curated, monkeypatch):
    def _binding(role, **kwargs):
        return _bind(_BoomLLM())

    monkeypatch.setattr(lint_module, "role_binding", _binding)
    report = await _lint(curated)
    assert report.semantic == ()
    assert len(report.errors) == 3
    assert all("the model refused" in line for line in report.errors)
    assert [lane.name for lane in report.mechanical] == ["wiki", "work"]


async def test_no_model_configured_is_one_error_line_and_an_empty_semantic_stream(curated, monkeypatch):
    def _refuse(role, **kwargs):
        raise KeyError(f"no model_id for role {role!r}")

    monkeypatch.setattr(lint_module, "role_binding", _refuse)
    report = await _lint(curated)
    assert report.semantic == ()
    assert len(report.errors) == 1
    assert "linter" in report.errors[0]
    assert [lane.name for lane in report.mechanical] == ["wiki", "work"]


async def test_the_adr_group_sees_only_adrs_and_the_stale_group_only_cited_pages(curated, monkeypatch):
    seen: dict[str, str] = {}

    class _Recording(_FakeLLM):
        async def ainvoke(self, messages):
            system = str(messages[0].content)
            human = str(messages[1].content)
            group = (
                "adr_chain"
                if "ADR chain checks" in system
                else ("stale_claims" if "Stale claim checks" in system else "page_quality")
            )
            seen[group] = human
            return _Reply("one finding")

    monkeypatch.setattr(lint_module, "role_binding", lambda *a, **k: _bind(_Recording()))
    await _lint(curated)
    assert "adrs/0001-pick-okf" in seen["adr_chain"]
    assert "concepts/plain" not in seen["adr_chain"]
    assert "concepts/cited" in seen["stale_claims"]
    assert "concepts/plain" not in seen["stale_claims"]


async def test_a_no_issues_sentinel_reply_produces_no_phantom_finding(curated, monkeypatch):
    """The exact sentinel `prompts/linter.py` asks a clean group to emit must
    not become a `SemanticFinding` — empty means clean, same as every other
    report type in this module."""
    llm = _FakeLLM("No page quality issues found.")
    monkeypatch.setattr(lint_module, "role_binding", lambda *a, **k: _bind(llm))
    report = await _lint(curated)
    assert not any(f.group == "page_quality" for f in report.semantic)


async def test_non_string_response_content_becomes_a_captured_error_not_garbage(curated, monkeypatch):
    """A model returning block-structured content (a list, not `str`) must be
    isolated as one `errors` line for that group, the same guard `ingest.py`
    and `suggest_pages.py` already apply — never `str()`-serialized into a
    findings line."""

    class _BlockLLM:
        async def ainvoke(self, messages: list[object]) -> _Reply:
            reply = _Reply("")
            reply.content = [{"type": "text", "text": "not a plain string"}]  # type: ignore[assignment]
            return reply

    monkeypatch.setattr(lint_module, "role_binding", lambda *a, **k: _bind(_BlockLLM()))
    report = await _lint(curated)
    assert any("linter returned non-text content" in line for line in report.errors)
    assert not any("not a plain string" in f.message for f in report.semantic)


async def test_the_page_quality_group_reads_at_most_twenty_pages(workspace, monkeypatch):
    concepts = workspace.layout.bundle_dir / "concepts"
    concepts.mkdir(exist_ok=True)
    for index in range(30):
        (concepts / f"page-{index:02d}.md").write_text(
            f"---\ntype: Explanation\ntitle: Page {index}\n---\n\nBody.\n", encoding="utf-8"
        )
    captured: list[str] = []

    class _Capturing(_FakeLLM):
        async def ainvoke(self, messages):
            if "Semantic check categories" in str(messages[0].content):
                captured.append(str(messages[1].content))
            return _Reply("one finding")

    monkeypatch.setattr(lint_module, "role_binding", lambda *a, **k: _bind(_Capturing()))
    await _lint(workspace)
    assert captured
    assert captured[0].count("--- Page: ") == 20


async def test_a_wiki_lane_that_never_loaded_says_the_semantic_pass_did_not_run(workspace, monkeypatch):
    """D4: the walk error was reported, but nothing said the semantic half was
    skipped. Every other refusal in this module contributes its own line."""

    def _boom(root, *, ignore=()):
        raise OSError("disk went away")

    monkeypatch.setattr(lint_module, "load_bundle", _boom)
    report = await _lint(workspace)
    assert report.semantic == ()
    assert any("disk went away" in line for line in report.errors)
    assert any("semantic pass did not run" in line for line in report.errors)


# --------------------------------------------------------------------------
# G1 — the page-quality window. A link neighbourhood around a seed that
# rotates one page per day, not the alphabetical prefix it replaced.
# --------------------------------------------------------------------------

from graph_works_core.lint_drift.lint import PAGE_QUALITY_WINDOW, _group_pages  # noqa: E402
from okf_io import load_bundle  # noqa: E402


def _write(workspace, relative: str, *, body: str = "Body.\n") -> None:
    path = workspace.layout.bundle_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: Explanation\ntitle: {relative}\n---\n\n{body}", encoding="utf-8")


def _seed_corpus(workspace, *, count: int = 25) -> None:
    """`count` ADRs and `count` concepts, each ADR linking to its own concept.

    `adrs/` sorts ahead of every other lane directory, so this is exactly the
    corpus shape the old fixed prefix could not see past.
    """
    for index in range(count):
        _write(workspace, f"adrs/{index:04d}-decision.md", body=f"See [page](/concepts/page-{index:02d}.md).\n")
        _write(workspace, f"concepts/page-{index:02d}.md")


def _window(bundle, *, today=TODAY) -> tuple[str, ...]:
    return tuple(concept_id for concept_id, _document in _group_pages(bundle, today=today)["page_quality"])


def _today_seeding(ordered: tuple[str, ...], concept_id: str) -> date:
    """The first date on or after `TODAY` whose seed is *concept_id*.

    The seed is `today.toordinal() % len(ordered)`, so a test that needs a
    known seed solves for the day rather than hard-coding one.
    """
    target = ordered.index(concept_id)
    base = TODAY.toordinal()
    return date.fromordinal(base + (target - base) % len(ordered))


def test_the_window_is_not_the_alphabetical_prefix_it_replaced(workspace):
    """The spec's §2 measurement as a named regression, and the one test that
    fails against the old code: `sorted()` puts `adrs/` first on any vault this
    tool generates, so the prefix was the twenty lowest-numbered ADRs — a
    strict subset of the `adr_chain` group's own page set."""
    _seed_corpus(workspace)
    bundle = load_bundle(workspace.layout.bundle_dir)
    ordered = tuple(sorted(bundle.concepts))
    today = _today_seeding(ordered, "adrs/0000-decision")

    window = _window(bundle, today=today)
    assert len(window) == PAGE_QUALITY_WINDOW
    assert window != ordered[:PAGE_QUALITY_WINDOW]
    assert any(concept_id.startswith("concepts/") for concept_id in window)


def test_every_page_is_the_seed_on_some_day_so_none_is_unreachable(workspace):
    """Rotation is what converts *structurally unreachable* into *eventually
    reached*. A seed is always the first member of its own window, so every
    page is examined within `len(ordered)` days."""
    _seed_corpus(workspace)
    bundle = load_bundle(workspace.layout.bundle_dir)
    ordered = tuple(sorted(bundle.concepts))

    reached: set[str] = set()
    for offset in range(len(ordered)):
        reached.update(_window(bundle, today=date.fromordinal(TODAY.toordinal() + offset)))
    assert reached == set(ordered)


def test_a_page_linked_to_the_seed_is_in_the_window_and_an_unlinked_peer_is_not(workspace):
    """The relatedness claim, stated as behaviour."""
    _write(workspace, "adrs/0000-seed.md", body="See [linked](/concepts/zzz-linked.md).\n")
    for index in range(1, 41):
        _write(workspace, f"adrs/{index:04d}-filler.md")
    _write(workspace, "concepts/zzz-linked.md")
    _write(workspace, "concepts/zzz-unlinked.md")

    bundle = load_bundle(workspace.layout.bundle_dir)
    ordered = tuple(sorted(bundle.concepts))
    window = _window(bundle, today=_today_seeding(ordered, "adrs/0000-seed"))

    assert "concepts/zzz-linked" in window
    assert "concepts/zzz-unlinked" not in window


def test_external_image_and_dangling_links_are_skipped_rather_than_followed(workspace):
    """`Link.target` is `None` for an external destination, a `.md` target that
    names no concept is a broken link, and an image embeds rather than cites.
    None of the three is a neighbour, and none of them costs a window slot."""
    _write(
        workspace,
        "adrs/0000-seed.md",
        body=(
            "[ext](https://example.com/page.md)\n"
            "[dangling](/concepts/missing.md)\n"
            "![embedded](/concepts/bbb-image-only.md)\n"
            "[real](/concepts/aaa-real.md)\n"
        ),
    )
    for index in range(1, 41):
        _write(workspace, f"adrs/{index:04d}-filler.md")
    _write(workspace, "concepts/aaa-real.md")
    _write(workspace, "concepts/bbb-image-only.md")

    bundle = load_bundle(workspace.layout.bundle_dir)
    ordered = tuple(sorted(bundle.concepts))
    window = _window(bundle, today=_today_seeding(ordered, "adrs/0000-seed"))

    assert "concepts/aaa-real" in window
    assert "concepts/bbb-image-only" not in window
    assert len(window) == PAGE_QUALITY_WINDOW


def test_a_bundle_with_no_links_tops_up_to_exactly_the_window_size(workspace):
    """The cap always fills, so "twenty pages" stays a true statement rather
    than an upper bound the reader has to discount."""
    for index in range(40):
        _write(workspace, f"concepts/page-{index:02d}.md")
    window = _window(load_bundle(workspace.layout.bundle_dir))
    assert len(window) == PAGE_QUALITY_WINDOW
    assert len(set(window)) == PAGE_QUALITY_WINDOW


def test_a_bundle_smaller_than_the_window_yields_every_page(workspace):
    bundle = load_bundle(workspace.layout.bundle_dir)
    ordered = tuple(sorted(bundle.concepts))
    window = _window(bundle)
    assert len(window) == min(PAGE_QUALITY_WINDOW, len(ordered))
    assert len(set(window)) == len(window)
    if len(ordered) <= PAGE_QUALITY_WINDOW:
        assert set(window) == set(ordered)


def test_a_bundle_with_no_concepts_yields_an_empty_window(tmp_path):
    """`today.toordinal() % 0` is the trap this guards."""
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _group_pages(load_bundle(empty), today=TODAY)["page_quality"] == ()


def test_the_same_day_and_bundle_produce_an_identical_window_twice(workspace):
    """Guards the `hash()` trap — Python salts string hashing per process, so a
    hash-derived seed picks a different window on every run — and any
    set-iteration order that creeps into the neighbourhood walk."""
    _seed_corpus(workspace)
    first = _window(load_bundle(workspace.layout.bundle_dir))
    second = _window(load_bundle(workspace.layout.bundle_dir))
    assert first == second


# --------------------------------------------------------------------------
# G2 — `LintReport.render()`. Pure text from the report's own fields, the
# `WorkspaceInit.diff()` / `IndexUpdate.diff()` idiom this workspace speaks.
# --------------------------------------------------------------------------

from types import MappingProxyType  # noqa: E402

from graph_works_core.lint_drift.lint import ProposalBacklog, SemanticFinding  # noqa: E402
from okf_io import Finding, Report  # noqa: E402


def _finding(
    *,
    code: str = "links.broken",
    severity: str = "error",
    message: str = "target does not resolve",
    spec: str = "§6.1",
    path: str | None = "how-tos/deploy.md",
    line: int | None = 12,
) -> Finding:
    return Finding(code=code, severity=severity, message=message, spec=spec, path=path, line=line)  # type: ignore[arg-type]


def _lane(name: str, *findings: Finding) -> LaneReport:
    return LaneReport(name=name, report=Report(findings=tuple(findings)))


def _semantic(group: str, message: str, *, page: str | None = None, model: str = "fake.model.v1") -> SemanticFinding:
    return SemanticFinding(group=group, message=message, page=page, model=model)


def test_render_puts_errors_above_the_mechanical_section():
    """An error means a lane did not walk or did not compose, so the findings
    below it are incomplete. A reader needs that first, not last."""
    report = LintReport(mechanical=(_lane("wiki", _finding()),), errors=("wiki lane: disk went away",))
    rendered = report.render()
    assert "! wiki lane: disk went away" in rendered
    assert rendered.index("## Errors") < rendered.index("## Mechanical")


def test_render_keeps_semantic_findings_in_their_own_section_after_the_mechanical_one():
    """The entire reason `SemanticFinding` is not a `Finding` is that one is
    reproducible and cites a spec section and the other is a judge's opinion.
    Interleaving them visually undoes that split."""
    report = LintReport(
        mechanical=(_lane("wiki", _finding()),),
        semantic=(_semantic("page_quality", "contradicts adrs/0011-lanes", page="adrs/0004-lanes"),),
    )
    rendered = report.render()
    assert rendered.index("## Mechanical") < rendered.index("## Semantic")
    mechanical_block = rendered[rendered.index("## Mechanical") : rendered.index("## Semantic")]
    assert "contradicts adrs/0011-lanes" not in mechanical_block
    assert "adrs/0004-lanes: contradicts adrs/0011-lanes" in rendered


def test_render_names_the_models_that_produced_each_semantic_group():
    """`SemanticFinding.model` exists so a verdict can be read against the
    model that produced it, which only works if a reader sees it. It is derived
    from the findings present, so a run that mixes models says two names."""
    one = LintReport(semantic=(_semantic("page_quality", "a claim"),))
    assert "### page_quality — fake.model.v1" in one.render()

    mixed = LintReport(
        semantic=(
            _semantic("page_quality", "a claim", model="model-b"),
            _semantic("page_quality", "another", model="model-a"),
        )
    )
    assert "### page_quality — model-a, model-b" in mixed.render()


def test_render_orders_semantic_groups_by_semantic_groups_and_keeps_arrival_order_within_one():
    report = LintReport(
        semantic=(
            _semantic("adr_chain", "second group, first line"),
            _semantic("page_quality", "first group, first line"),
            _semantic("page_quality", "first group, second line"),
        )
    )
    rendered = report.render()
    assert rendered.index("### page_quality") < rendered.index("### adr_chain")
    assert rendered.index("first group, first line") < rendered.index("first group, second line")


def test_render_marks_a_lane_with_no_findings_as_clean():
    """A lane that ran and found nothing is not the same as a lane that is
    missing, so it keeps its heading."""
    rendered = LintReport(mechanical=(_lane("wiki", _finding()), _lane("work"))).render()
    assert "### work\n(clean)" in rendered


def test_render_keeps_the_message_of_a_finding_that_names_no_path_or_no_line():
    """`Finding.path` is optional — a bundle-level finding names no member —
    and so is `Finding.line`. Neither may cost the finding its message, and a
    finding with no line must not render a bare trailing colon."""
    report = LintReport(
        mechanical=(
            _lane(
                "wiki",
                _finding(code="bundle.missing-index", message="no index.md", path=None, line=None),
                _finding(
                    code="provenance.stale",
                    message="sources[] older than the anchor",
                    path="references/api.md",
                    line=None,
                ),
            ),
        )
    )
    rendered = report.render()
    assert "no index.md" in rendered
    assert "sources[] older than the anchor" in rendered
    assert "references/api.md" in rendered
    assert "references/api.md:" not in rendered


def test_render_flattens_a_multi_line_finding_message_onto_one_line():
    """`frontmatter.unparseable` carries `str(exc)` off a ruamel `YAMLError`,
    which is multi-line. Laid out raw it would emit continuation lines carrying
    no severity, code or path, and strand the spec citation on the last of
    them."""
    report = LintReport(
        mechanical=(
            _lane(
                "wiki",
                _finding(
                    code="frontmatter.unparseable",
                    message='mapping values are not allowed here\n  in "<file>", line 3, column 6:\n      bad: indent',
                    spec="§11",
                ),
            ),
        )
    )
    rendered = report.render()
    lines = [line for line in rendered.splitlines() if "frontmatter.unparseable" in line]
    assert len(lines) == 1
    assert lines[0].endswith("(§11)")
    assert "mapping values are not allowed here" in lines[0]
    assert "bad: indent" in lines[0]


def test_render_carries_the_whole_proposal_backlog_on_one_footer_line():
    """`open_proposals` is a field on the report, and a renderer that skips
    part of the value it renders is a smaller version of the defect this
    method exists to fix."""
    backlog = ProposalBacklog(
        count=12,
        oldest=date(2026, 6, 30),
        malformed=1,
        ages=MappingProxyType({"<7d": 2, "7-30d": 4, ">30d": 6, "undated": 0}),
    )
    rendered = LintReport(open_proposals=backlog).render()
    assert "proposals: 12 open, oldest 2026-06-30, 1 malformed" in rendered
    for bucket, count in (("<7d", 2), ("7-30d", 4), (">30d", 6), ("undated", 0)):
        assert f"{bucket} {count}" in rendered

    undated_only = LintReport(open_proposals=ProposalBacklog(count=1, oldest=None))
    assert "oldest" not in undated_only.render()


def test_a_report_with_nothing_to_say_renders_exactly_no_findings():
    """One line, not the empty string: empty is right for `diff()`, where
    `changed` is the guard a caller checks first. There is no such guard here,
    and an empty string reads as a broken command."""
    assert LintReport().render() == "No findings."
    assert LintReport(mechanical=(_lane("wiki"), _lane("work"))).render() == "No findings."


def test_render_returns_text_and_writes_nothing(tmp_path, monkeypatch):
    """The `diff()` contract: these types render on demand from their own
    fields and never touch the filesystem."""
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())
    report = LintReport(
        mechanical=(_lane("wiki", _finding()),),
        semantic=(_semantic("page_quality", "no page named here"),),
        errors=("wiki lane: disk went away",),
    )
    rendered = report.render()
    assert isinstance(rendered, str)
    assert set(tmp_path.iterdir()) == before
