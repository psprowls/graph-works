"""The drift propagator. Every LLM path takes an injected fake model, and the
git and graph collaborators are stubs — the unit under test is the
orchestration, not `git diff`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from code_wiki_okf.config import Config, RepoConfig, StateGateConfig
from graph_works_core.lint_drift import propagate_drift as pd
from okf_io import build_link_graph, load_bundle

TODAY = date(2026, 8, 13)
AT = datetime(2026, 8, 13, tzinfo=UTC)


@dataclass(frozen=True)
class _Node:
    kind: str
    name: str
    path: str | None
    line: int | None
    attrs: dict[str, object]


class _Reader:
    """The two questions the propagator asks a graph."""

    def __init__(self, nodes: list[_Node]):
        self._nodes = nodes

    def list_packages(self) -> list[_Node]:
        return [n for n in self._nodes if n.kind == "package"]

    def list_apps(self) -> list[_Node]:
        return [n for n in self._nodes if n.kind == "app"]

    def list_test_suites(self) -> list[_Node]:
        return [n for n in self._nodes if n.kind == "test_suite"]

    def list_agent_plugins(self) -> list[_Node]:
        return [n for n in self._nodes if n.kind == "agent_plugin"]


def _write(root: Path, member: str, text: str) -> None:
    path = root / member
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _entity(title: str, resource: str, sha: str, *, narrative: str = "It does a thing.") -> str:
    body = f"\n## Narrative\n\n{narrative}\n" if narrative else "\n"
    return f"---\ntype: Package\ntitle: {title}\nresource: {resource}\nlast_updated_commit: {sha}\n---\n{body}"


@pytest.fixture
def bundle_root(tmp_path):
    root = tmp_path / "okf"
    root.mkdir()
    _write(root, "packages/okf-io.md", _entity("okf-io", "package:okf-io", "aaa1111"))
    _write(root, "packages/quiet.md", _entity("quiet", "package:quiet", "bbb2222"))
    _write(
        root,
        "concepts/byte-fidelity.md",
        "---\ntype: Explanation\ntitle: Byte fidelity\n---\n\nSee [okf-io](/packages/okf-io.md).\n",
    )
    _write(
        root,
        "adrs/0001-two-layer.md",
        "---\ntype: Explanation\ntitle: Two layers\n---\n\nPer [okf-io](/packages/okf-io.md).\n",
    )
    return root


@pytest.fixture
def config(tmp_path):
    return Config(
        graph_dir=tmp_path / "cache",
        declarations_dir=tmp_path / "config",
        repos=(RepoConfig(name="rebuild", path=tmp_path / "repo", ignore=()),),
        state_gate=StateGateConfig(enabled=False, branches=("main",)),
    )


@pytest.fixture
def reader():
    return _Reader(
        [
            _Node("package", "okf-io", "packages/okf-io", None, {"uri": "package:okf-io"}),
            _Node("package", "quiet", "packages/quiet", None, {"uri": "package:quiet"}),
        ]
    )


def _candidates(bundle_root, reader, config, anchors, monkeypatch, changed=("packages/okf-io/src/x.py",)):
    monkeypatch.setattr(pd, "changed_files_since", lambda repo, since, paths=(): list(changed))
    bundle = load_bundle(bundle_root)
    return pd.propagation_candidates(bundle, reader, anchors, config=config, repo_root=config.repos[0].path)


def test_an_entity_whose_anchor_matches_its_commit_is_not_a_candidate(bundle_root, reader, config, monkeypatch):
    anchors = {"packages/okf-io": "aaa1111", "packages/quiet": "bbb2222"}
    assert _candidates(bundle_root, reader, config, anchors, monkeypatch) == ()


def test_an_entity_with_no_anchor_is_a_candidate(bundle_root, reader, config, monkeypatch):
    found = _candidates(bundle_root, reader, config, {"packages/quiet": "bbb2222"}, monkeypatch)
    assert [c.concept_id for c in found] == ["packages/okf-io"]
    assert found[0].last_updated_commit == "aaa1111"
    assert found[0].anchor is None
    assert found[0].changed_files == ("packages/okf-io/src/x.py",)


def test_a_stale_anchor_is_a_candidate(bundle_root, reader, config, monkeypatch):
    found = _candidates(bundle_root, reader, config, {"packages/okf-io": "old0000"}, monkeypatch)
    assert [c.concept_id for c in found] == ["packages/okf-io", "packages/quiet"]
    assert found[0].anchor == "old0000"


def test_a_node_with_no_path_is_not_a_candidate(bundle_root, config, monkeypatch):
    reader = _Reader([_Node("package", "okf-io", None, None, {"uri": "package:okf-io"})])
    found = _candidates(bundle_root, reader, config, {}, monkeypatch)
    assert [c.concept_id for c in found] == []


def test_an_entity_with_no_narrative_is_not_a_candidate(bundle_root, reader, config, monkeypatch):
    _write(bundle_root, "packages/okf-io.md", _entity("okf-io", "package:okf-io", "aaa1111", narrative=""))
    found = _candidates(bundle_root, reader, config, {}, monkeypatch)
    assert "packages/okf-io" not in [c.concept_id for c in found]


def test_an_entity_with_a_blank_narrative_is_not_a_candidate(bundle_root, reader, config, monkeypatch):
    _write(bundle_root, "packages/okf-io.md", _entity("okf-io", "package:okf-io", "aaa1111", narrative="   "))
    found = _candidates(bundle_root, reader, config, {}, monkeypatch)
    assert "packages/okf-io" not in [c.concept_id for c in found]


def test_a_page_with_no_resource_is_never_a_candidate(bundle_root, reader, config, monkeypatch):
    found = _candidates(bundle_root, reader, config, {}, monkeypatch)
    assert "concepts/byte-fidelity" not in [c.concept_id for c in found]


def test_git_returning_none_yields_no_changed_files(bundle_root, reader, config, monkeypatch):
    monkeypatch.setattr(pd, "changed_files_since", lambda repo, since, paths=(): None)
    bundle = load_bundle(bundle_root)
    found = pd.propagation_candidates(bundle, reader, {}, config=config, repo_root=config.repos[0].path)
    assert all(c.changed_files == () for c in found)


def test_the_diff_runs_against_the_repo_that_owns_the_node_path(bundle_root, reader, tmp_path, monkeypatch):
    owned = tmp_path / "other"
    config = Config(
        graph_dir=tmp_path / "cache",
        declarations_dir=tmp_path / "config",
        repos=(
            RepoConfig(name="other", path=owned, ignore=()),
            RepoConfig(name="rebuild", path=tmp_path / "repo", ignore=()),
        ),
        state_gate=StateGateConfig(enabled=False, branches=("main",)),
    )
    (owned / "packages" / "okf-io").mkdir(parents=True)
    seen: list[Path] = []

    def _diff(repo, since, paths=()):
        seen.append(repo)
        return []

    monkeypatch.setattr(pd, "changed_files_since", _diff)
    bundle = load_bundle(bundle_root)
    pd.propagation_candidates(bundle, reader, {}, config=config, repo_root=tmp_path / "repo")
    assert owned in seen


def test_targets_are_curated_backlinkers_with_entities_and_proposals_excluded(bundle_root, reader, config, monkeypatch):
    _write(
        bundle_root,
        "proposals/p.md",
        "---\ntype: Proposal\ntitle: P\ntarget: concepts/byte-fidelity.md\npage_status: proposed\n---\n\n"
        "About [okf-io](/packages/okf-io.md).\n",
    )
    _write(
        bundle_root,
        "packages/neighbour.md",
        "---\ntype: Package\ntitle: n\nresource: package:n\n---\n\nSee [okf-io](/packages/okf-io.md).\n",
    )
    bundle = load_bundle(bundle_root)
    monkeypatch.setattr(pd, "changed_files_since", lambda repo, since, paths=(): [])
    candidates = pd.propagation_candidates(bundle, reader, {}, config=config, repo_root=config.repos[0].path)
    targets = pd.drift_targets(candidates, bundle, build_link_graph(bundle))
    assert {t.concept_id for t in targets} == {"concepts/byte-fidelity", "adrs/0001-two-layer"}


def test_a_target_in_the_adr_lane_judges_as_an_adr(bundle_root, reader, config, monkeypatch):
    bundle = load_bundle(bundle_root)
    monkeypatch.setattr(pd, "changed_files_since", lambda repo, since, paths=(): [])
    candidates = pd.propagation_candidates(bundle, reader, {}, config=config, repo_root=config.repos[0].path)
    kinds = {t.concept_id: t.kind for t in pd.drift_targets(candidates, bundle, build_link_graph(bundle))}
    assert kinds["adrs/0001-two-layer"] == "adr"
    assert kinds["concepts/byte-fidelity"] == "concept"


def test_two_entities_backlinking_one_page_produce_one_target_with_two_candidates(
    bundle_root, reader, config, monkeypatch
):
    _write(
        bundle_root,
        "concepts/byte-fidelity.md",
        "---\ntype: Explanation\ntitle: Byte fidelity\n---\n\n"
        "See [okf-io](/packages/okf-io.md) and [quiet](/packages/quiet.md).\n",
    )
    bundle = load_bundle(bundle_root)
    monkeypatch.setattr(pd, "changed_files_since", lambda repo, since, paths=(): [])
    candidates = pd.propagation_candidates(bundle, reader, {}, config=config, repo_root=config.repos[0].path)
    targets = {t.concept_id: t for t in pd.drift_targets(candidates, bundle, build_link_graph(bundle))}
    assert len(targets["concepts/byte-fidelity"].candidates) == 2


# --------------------------------------------------------------------------
# The judge and the writer. No network, no credential.
# --------------------------------------------------------------------------

import json  # noqa: E402

from graph_works_core.lint_drift.drift_anchor import read_anchors, write_anchors  # noqa: E402
from graph_works_core.workspace.layout import layout_for  # noqa: E402
from subagents_io.roles import RoleBinding, RoleSpec  # noqa: E402

SPEC = RoleSpec(model_id="fake.judge.v1", max_concurrency=3)
STALE = json.dumps(
    {"stale": True, "findings": [{"entity_stem": "packages/okf-io", "stale_claim": "c", "rationale": "moved"}]}
)


class _Reply:
    def __init__(self, content: str):
        self.content = content
        self.usage_metadata = None


class _Judge:
    def __init__(self, content: str = STALE):
        self.content = content
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return _Reply(self.content)


class _BoomJudge:
    async def ainvoke(self, messages):
        raise RuntimeError("the judge refused")


class _BlockJudge:
    async def ainvoke(self, messages):
        reply = _Reply("")
        reply.content = [{"type": "text", "text": "not a plain string"}]  # type: ignore[assignment]
        return reply


class _TruncatedJudge:
    """A reply that looks well-formed but was cut off by the token cap: the
    provider reports it via `response_metadata`."""

    async def ainvoke(self, messages):
        reply = _Reply('{"stale": true, "findings": [{"entity_stem": "packages/okf-io"')
        reply.response_metadata = {"stop_reason": "max_tokens"}
        return reply


@pytest.fixture
def layout(tmp_path, bundle_root):
    return layout_for(tmp_path, bundle_dir=str(bundle_root), cache_dir="cache", repo_root=None)


async def _run(layout, config, reader, monkeypatch, *, judge=None, spec=SPEC, **kwargs):
    monkeypatch.setattr(pd, "changed_files_since", lambda repo, since, paths=(): ["packages/okf-io/src/x.py"])
    monkeypatch.setattr(pd, "role_binding", lambda *a, **k: RoleBinding(spec=spec, make_llm=lambda: judge or _Judge()))
    return await pd.run_propagate_drift(layout, config, reader, at=AT, repo_root=config.repos[0].path, **kwargs)


async def test_a_dry_run_writes_nothing(layout, config, reader, monkeypatch):
    result = await _run(layout, config, reader, monkeypatch)
    assert result.dry_run is True
    assert result.pages_stale == 2
    assert list(layout.bundle_dir.glob("proposals/*.md")) == []
    assert read_anchors(layout.cache_dir) == {}


async def test_a_live_run_files_one_proposal_per_stale_target_and_stamps(layout, config, reader, monkeypatch):
    result = await _run(layout, config, reader, monkeypatch, dry_run=False)
    filed = sorted(p.name for p in layout.bundle_dir.glob("proposals/*.md"))
    assert len(filed) == 2
    assert result.pages_stale == 2
    assert read_anchors(layout.cache_dir)["packages/okf-io"] == "aaa1111"


async def test_the_source_shape_is_the_one_the_spec_fixes(layout, config, reader, monkeypatch):
    await _run(layout, config, reader, monkeypatch, dry_run=False)
    text = next(layout.bundle_dir.glob("proposals/*.md")).read_text(encoding="utf-8")
    assert "drift-packages/okf-io-aaa1111" in text
    assert "/packages/okf-io.md" in text
    assert "moved" in text


async def test_a_second_run_with_unchanged_anchors_judges_nothing(layout, config, reader, monkeypatch):
    await _run(layout, config, reader, monkeypatch, dry_run=False)
    second = await _run(layout, config, reader, monkeypatch, dry_run=False)
    assert second.pages_judged == 0
    assert second.entities_considered == 0


async def test_a_settled_proposal_pre_filters_its_target(layout, config, reader, monkeypatch):
    _write(
        layout.bundle_dir,
        "proposals/settled.md",
        "---\ntype: Proposal\ntitle: S\ntarget: concepts/byte-fidelity.md\npage_status: rejected\n---\n\nNo.\n",
    )
    result = await _run(layout, config, reader, monkeypatch)
    assert result.pages_skipped_settled == 1
    assert result.pages_judged == 1


async def test_only_an_entity_narrows_the_candidates_and_still_stamps(layout, config, reader, monkeypatch):
    result = await _run(layout, config, reader, monkeypatch, dry_run=False, only="packages/okf-io")
    assert result.entities_considered == 1
    assert read_anchors(layout.cache_dir) == {"packages/okf-io": "aaa1111"}


async def test_only_a_page_narrows_the_targets_and_does_not_stamp(layout, config, reader, monkeypatch):
    result = await _run(layout, config, reader, monkeypatch, dry_run=False, only="concepts/byte-fidelity")
    assert result.pages_judged == 1
    assert read_anchors(layout.cache_dir) == {}


async def test_a_malformed_verdict_fails_safe_to_not_stale(layout, config, reader, monkeypatch):
    result = await _run(layout, config, reader, monkeypatch, dry_run=False, judge=_Judge("I am not sure."))
    assert result.pages_stale == 0
    assert result.errors == ()
    assert list(layout.bundle_dir.glob("proposals/*.md")) == []


async def test_a_verdict_naming_an_entity_not_in_the_batch_contributes_no_source(layout, config, reader, monkeypatch):
    hallucinated = json.dumps(
        {"stale": True, "findings": [{"entity_stem": "packages/nope", "stale_claim": "c", "rationale": "r"}]}
    )
    result = await _run(layout, config, reader, monkeypatch, dry_run=False, judge=_Judge(hallucinated))
    assert result.pages_stale == 0
    assert list(layout.bundle_dir.glob("proposals/*.md")) == []


async def test_a_verdict_naming_one_real_and_one_hallucinated_entity_keeps_only_the_real_one(
    layout, config, reader, monkeypatch
):
    """Byte fidelity backlinks two real candidates (okf-io, quiet); the verdict
    names one of them plus a hallucinated stem. Only the real one survives."""
    _write(
        layout.bundle_dir,
        "concepts/byte-fidelity.md",
        "---\ntype: Explanation\ntitle: Byte fidelity\n---\n\n"
        "See [okf-io](/packages/okf-io.md) and [quiet](/packages/quiet.md).\n",
    )
    mixed = json.dumps(
        {
            "stale": True,
            "findings": [
                {"entity_stem": "packages/okf-io", "stale_claim": "c", "rationale": "moved"},
                {"entity_stem": "packages/nope", "stale_claim": "c", "rationale": "r"},
            ],
        }
    )
    result = await _run(layout, config, reader, monkeypatch, dry_run=False, judge=_Judge(mixed))
    byte_fidelity_findings = [f for f in result.findings if f.target == "concepts/byte-fidelity.md"]
    assert len(byte_fidelity_findings) == 1
    assert byte_fidelity_findings[0].entity_id == "packages/okf-io"


async def test_a_judge_that_raises_is_captured_per_item(layout, config, reader, monkeypatch):
    result = await _run(layout, config, reader, monkeypatch, judge=_BoomJudge())
    assert len(result.errors) == 2
    assert all("the judge refused" in line for line in result.errors)
    assert result.pages_stale == 0


async def test_no_model_configured_is_one_error_line_and_stamps_nothing(layout, config, reader, monkeypatch):
    monkeypatch.setattr(pd, "changed_files_since", lambda repo, since, paths=(): [])

    def _refuse(role, **kwargs):
        raise KeyError(f"no model_id for role {role!r}")

    monkeypatch.setattr(pd, "role_binding", _refuse)
    result = await pd.run_propagate_drift(layout, config, reader, at=AT, repo_root=config.repos[0].path, dry_run=False)
    assert result.pages_judged == 0
    assert len(result.errors) == 1
    assert read_anchors(layout.cache_dir) == {}


async def test_an_entity_whose_targets_were_all_pre_filtered_is_still_stamped(layout, config, reader, monkeypatch):
    """§3.5's rule, kept: a candidate whose backlinkers were all considered is
    processed, even when every one of them was dropped by the ledger."""
    for slug, target in (("a", "concepts/byte-fidelity.md"), ("b", "adrs/0001-two-layer.md")):
        _write(
            layout.bundle_dir,
            f"proposals/settled-{slug}.md",
            f"---\ntype: Proposal\ntitle: S\ntarget: {target}\npage_status: approved\n---\n\nDone.\n",
        )
    result = await _run(layout, config, reader, monkeypatch, dry_run=False)
    assert result.pages_judged == 0
    assert result.pages_skipped_settled == 2
    assert read_anchors(layout.cache_dir)["packages/okf-io"] == "aaa1111"


async def test_an_existing_anchor_for_another_entity_survives_a_run(layout, config, reader, monkeypatch):
    write_anchors(layout.cache_dir, {"packages/gone": "zzz9999"})
    await _run(layout, config, reader, monkeypatch, dry_run=False)
    assert read_anchors(layout.cache_dir)["packages/gone"] == "zzz9999"


async def test_non_string_response_content_becomes_a_captured_error_not_garbage(layout, config, reader, monkeypatch):
    """A model returning block-structured content (a list, not `str`) must be
    isolated as one `errors` line for that target, the same guard `lint.py`'s
    semantic pass already applies — never `str()`-serialized into a finding."""
    result = await _run(layout, config, reader, monkeypatch, dry_run=False, judge=_BlockJudge())
    assert any("drift propagator returned non-text content" in line for line in result.errors)
    assert result.pages_stale == 0
    assert list(layout.bundle_dir.glob("proposals/*.md")) == []


def test_a_truncated_verdict_is_reported_rather_than_read_as_not_stale():
    # The parser's fail-safe was written for garbled replies. A reply cut off
    # by `max_tokens` is a real verdict that ran out of room, and reading it as
    # not-stale reports a stale page as clean with nothing logged anywhere.
    class _Response:
        def __init__(self, metadata):
            self.response_metadata = metadata

    assert pd._stopped_on_token_cap(_Response({"stop_reason": "max_tokens"}))
    assert pd._stopped_on_token_cap(_Response({"stopReason": "max_tokens"}))
    assert pd._stopped_on_token_cap(_Response({"finish_reason": "length"}))
    assert not pd._stopped_on_token_cap(_Response({"stop_reason": "end_turn"}))
    assert not pd._stopped_on_token_cap(_Response({}))
    assert not pd._stopped_on_token_cap(_Response(None))


async def test_a_response_truncated_by_the_token_cap_is_reported_not_absorbed(layout, config, reader, monkeypatch):
    """D-token-cap: a reply cut off by `max_tokens` looks like well-formed JSON
    up to the cut. Left to `parse_drift_propagator_verdict`'s fail-safe it would
    read as a clean not-stale verdict with nothing logged — a genuinely stale
    page reported as fine. The provider's `response_metadata` says why
    generation stopped, so the judge raises on it instead of parsing it, and
    the failure must reach `PropagateResult.errors` through the same fan-out
    capture every other judge failure already uses."""
    capped_spec = RoleSpec(model_id="fake.judge.v1", max_concurrency=3, max_tokens=2048)
    result = await _run(layout, config, reader, monkeypatch, dry_run=False, judge=_TruncatedJudge(), spec=capped_spec)
    assert result.errors != ()
    assert all("token cap" in line for line in result.errors)
    assert any("2048" in line for line in result.errors)
    assert result.pages_stale == 0
    assert list(layout.bundle_dir.glob("proposals/*.md")) == []


def _refusing_writer(monkeypatch):
    """Every plan comes back refused. The refusal kind is arbitrary: what is
    under test is that a refusal reaches a reader at all."""
    from okf_ext.proposals import ProposalPlan, Refusal

    real = pd.write_propagation_findings

    def _refusing(bundle, findings, **kwargs):
        return tuple(
            ProposalPlan(
                root=plan.root,
                target=plan.target,
                proposal=plan.proposal,
                writes=(),
                refusals=(Refusal(path=plan.proposal, kind="target-exists", detail="already there"),),
            )
            for plan in real(bundle, findings, **kwargs)
        )

    monkeypatch.setattr(pd, "write_propagation_findings", _refusing)


async def test_a_malformed_proposal_pre_filters_its_target_and_buys_no_judging_call(
    layout, config, reader, monkeypatch
):
    """D10: `page_status: banana` coerces to `None`, so the proposal satisfied
    neither `HUMAN_DECIDED` nor the backlog filter — the target was judged (a
    paid call) only for `plan_propose` to refuse it."""
    _write(
        layout.bundle_dir,
        "proposals/broken.md",
        "---\ntype: Proposal\ntitle: B\ntarget: concepts/byte-fidelity.md\npage_status: banana\n---\n\nHm.\n",
    )
    judge = _Judge()
    result = await _run(layout, config, reader, monkeypatch, judge=judge)
    assert result.pages_skipped_settled == 1
    assert result.pages_judged == 1
    assert judge.calls == 1


async def test_every_plan_refusal_becomes_an_errors_line(layout, config, reader, monkeypatch):
    """A refused plan lands in `PropagateResult.plans`, where nothing inspected
    it — so the run reported a stale page it did not and could not file."""
    _refusing_writer(monkeypatch)
    result = await _run(layout, config, reader, monkeypatch, dry_run=False)
    assert result.pages_stale == 2
    assert len([line for line in result.errors if "target-exists" in line]) == 2
    assert list(layout.bundle_dir.glob("proposals/*.md")) == []


class _PartialJudge:
    """Raises for the target whose human message contains *boom_on*, answers
    for every other one."""

    def __init__(self, boom_on: str):
        self.boom_on = boom_on

    async def ainvoke(self, messages):
        if self.boom_on in str(messages[1].content):
            raise RuntimeError("the judge refused")
        return _Reply(STALE)


async def test_a_live_run_whose_judge_raises_withholds_the_judged_targets_anchor(layout, config, reader, monkeypatch):
    """D1's regression. The existing capture test runs at the default
    `dry_run=True`, which is exactly why the stamping branch shipped
    unexercised with a failure. `packages/okf-io` is the only candidate either
    target names, and both targets raise, so it is the only anchor this run
    could have written for a *judged* entity — and it doesn't. (`packages/quiet`
    backlinks nothing; per the mixed-run test below, a candidate no target
    names is vacuously fully-judged and stamps regardless of what failed
    elsewhere in the same run.)"""
    result = await _run(layout, config, reader, monkeypatch, dry_run=False, judge=_BoomJudge())
    assert len(result.errors) == 2
    assert "packages/okf-io" not in read_anchors(layout.cache_dir)


async def test_a_mixed_run_stamps_only_the_candidates_whose_targets_all_succeeded(layout, config, reader, monkeypatch):
    """`packages/okf-io` backlinks both targets and the ADR one raises, so its
    anchor is withheld. `packages/quiet` backlinks nothing, so nothing failed
    for it and it stamps."""
    result = await _run(layout, config, reader, monkeypatch, dry_run=False, judge=_PartialJudge("Two layers"))
    assert len(result.errors) == 1
    anchors = read_anchors(layout.cache_dir)
    assert "packages/okf-io" not in anchors
    assert anchors["packages/quiet"] == "bbb2222"


async def test_a_candidate_whose_plan_was_refused_is_withheld_from_the_stamp(layout, config, reader, monkeypatch):
    """Same rule, second path: a finding that reached no proposal is as
    unrecorded as one that reached no verdict."""
    _refusing_writer(monkeypatch)
    result = await _run(layout, config, reader, monkeypatch, dry_run=False)
    assert result.pages_stale == 2
    assert "packages/okf-io" not in read_anchors(layout.cache_dir)


async def test_an_only_that_names_nothing_is_one_errors_line_not_a_clean_zero(layout, config, reader, monkeypatch):
    """D3: today a typo is byte-identical to "nothing drifted"."""
    result = await _run(layout, config, reader, monkeypatch, only="concepts/tpyo")
    assert result.entities_considered == 0
    assert result.pages_judged == 0
    assert len(result.errors) == 1
    assert "concepts/tpyo" in result.errors[0]


async def test_only_a_page_accepts_the_md_suffix_the_ledger_writes(layout, config, reader, monkeypatch):
    """`<concept_id>.md` is the proposal identity, so it is the form a human
    copies out of a filed proposal — and E7's CLI is where they type it."""
    with_suffix = await _run(layout, config, reader, monkeypatch, only="concepts/byte-fidelity.md")
    without = await _run(layout, config, reader, monkeypatch, only="concepts/byte-fidelity")
    assert with_suffix.pages_judged == without.pages_judged == 1
    assert with_suffix.errors == ()


async def test_only_a_real_page_that_did_not_drift_is_a_legitimate_zero(layout, config, reader, monkeypatch):
    """Refusing this would make `--only` unusable for its main job."""
    _write(
        layout.bundle_dir,
        "concepts/unlinked.md",
        "---\ntype: Explanation\ntitle: Unlinked\n---\n\nNothing links here.\n",
    )
    result = await _run(layout, config, reader, monkeypatch, only="concepts/unlinked")
    assert result.pages_judged == 0
    assert result.errors == ()


def test_plan_drift_brief_returns_targets_with_no_judge_call(layout, config, reader, monkeypatch):
    """The claude_code brief must never construct a role LLM."""

    def fail_role_binding(*args: object, **kwargs: object) -> object:
        raise AssertionError("plan_drift_brief must not call role_binding")

    monkeypatch.setattr(pd, "role_binding", fail_role_binding)

    brief = pd.plan_drift_brief(layout, config, reader, repo_root=None)

    assert isinstance(brief, pd.DriftBrief)
    for target in brief.targets:
        assert target.candidates
