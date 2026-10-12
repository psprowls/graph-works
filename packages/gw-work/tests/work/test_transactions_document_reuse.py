"""Fresh anchored reads reuse only the current invocation's matching Documents."""

from collections import Counter
from contextlib import closing

import pytest
from gw_kernel.testing.transactions import workspace as _workspace
from gw_kernel.workspace import bundle as loaders
from gw_pipeline import transactions
from gw_pipeline.testing import forced_tier as _forced_tier
from gw_work import commands
from okf_io import Document
from test_reparent_partial import _write
from work_tracker_okf.items import IGNORE


@pytest.mark.parametrize("tier", ["linux", "win32"])
def test_live_partial_apply_parses_only_fresh_moved_or_written_members(tmp_path, monkeypatch, tier):
    layout = _workspace(tmp_path)
    _write(layout, "work/left", kind="Epic")
    _write(layout, "work/destination", kind="Epic")
    source = "work/left/children/bug-source"
    _write(layout, source)
    bundle, plan = commands._plan_reparent_partial(layout, [source], "work/destination")
    baseline = loaders.reclassify_ignored(bundle, IGNORE)
    calls = []
    original = Document.parse

    def parse(cls, text, *, path=None):
        calls.append(path.relative_to(layout.bundle_dir).as_posix() if path else None)
        return original(text, path=path)

    monkeypatch.setattr(Document, "parse", classmethod(parse))
    with _forced_tier(tier):
        application = transactions.apply_mutation(layout, plan, baseline_bundle=baseline, commit=None)
    assert application.ok, application.failures
    expected = {write.member for write in plan.writes} | {f"{dest}.md" for dest in plan.path_mapping.values()}
    assert Counter(calls) == Counter({member: 1 for member in expected})


@pytest.mark.parametrize("tier", ["linux", "win32"])
def test_preflight_reuses_clean_and_changed_frontmatter_but_refuses_a_fresh_stale_body(tmp_path, monkeypatch, tier):
    from dataclasses import replace

    layout = _workspace(tmp_path)
    _write(layout, "work/destination", kind="Epic")
    source = "work/bug-source"
    _write(layout, source)
    bundle, plan = commands._plan_reparent_partial(layout, [source], "work/destination")
    # Exercise body preconditions independently of whole-byte write preconditions.
    plan = replace(plan, moves=(), writes=(), mkdirs=(), directory_preconditions=())
    documents = transactions._FreshDocuments(transactions._document_members(bundle))
    seed = bundle.concepts[source]
    page = layout.bundle_dir / f"{source}.md"
    calls = []
    original = transactions.parse

    def parse(text, *, path=None):
        calls.append(path)
        return original(text, path=path)

    monkeypatch.setattr(transactions, "parse", parse)
    with _forced_tier(tier), closing(transactions._open_root(layout.bundle_dir)) as root:
        transactions._preflight_anchored(plan, root, tmp_path / "scratch", documents=documents)
        page.write_bytes(seed.raw_text.replace("title:", "title: concurrent #").encode("utf-8"))
        transactions._preflight_anchored(plan, root, tmp_path / "scratch", documents=documents)
        transactions._preflight_anchored(plan, root, tmp_path / "scratch", documents=documents)
        page.write_bytes(page.read_bytes() + b"\nstale body\n")
        with pytest.raises(ValueError, match="changed since planning; re-plan"):
            transactions._preflight_anchored(plan, root, tmp_path / "scratch", documents=documents)
    assert calls == [page, page]


@pytest.mark.parametrize("tier", ["linux", "win32"])
def test_live_reference_artifacts_use_planning_seed_without_entering_validation_corpus(tmp_path, monkeypatch, tier):
    layout = _workspace(tmp_path)
    source = "work/bug-source"
    _write(layout, source)
    _write(layout, "work/destination", kind="Epic")
    member = f"{source}/references/01-design.md"
    page = layout.bundle_dir / member
    page.parent.mkdir(parents=True)
    content = b"# Design\n\nOriginal artifact bytes.\n"
    page.write_bytes(content)
    calls, scopes = [], []
    real_parse, real_validate = Document.parse, transactions.validate

    def parse(cls, text, *, path=None):
        calls.append(path)
        return real_parse(text, path=path)

    def validate(bundle, **kwargs):
        scopes.append(transactions._document_members(bundle))
        return real_validate(bundle, **kwargs)

    monkeypatch.setattr(Document, "parse", classmethod(parse))
    monkeypatch.setattr(transactions, "validate", validate)
    with _forced_tier(tier):
        result = commands.run_reparent(layout, [source], "work/destination", dry_run=False)
    assert result.application.ok, result.application.failures
    assert calls.count(page) == 1  # planning only; both preflights still read it
    assert len(scopes) == 2
    assert all(not any(path.endswith("/references/01-design.md") for path in scope) for scope in scopes)
    destination = layout.bundle_dir / "work/destination/children/bug-source/references/01-design.md"
    assert destination.read_bytes() == content


@pytest.mark.parametrize("tier", ["linux", "win32"])
def test_stale_reference_artifact_is_refused_even_with_the_original_planning_seed(tmp_path, monkeypatch, tier):
    from gw_kernel.testing.transactions import snapshot as _snapshot

    layout = _workspace(tmp_path)
    source = "work/bug-source"
    _write(layout, source)
    _write(layout, "work/destination", kind="Epic")
    member = f"{source}/references/01-design.md"
    page = layout.bundle_dir / member
    page.parent.mkdir(parents=True)
    page.write_bytes(b"# Design\n\nPlanned body.\n")
    bundle, plan = commands._plan_reparent_partial(layout, [source], "work/destination")
    baseline = loaders.reclassify_ignored(bundle, IGNORE)
    page.write_bytes(b"# Design\n\nConcurrent body.\n")
    before = _snapshot(layout.bundle_dir)
    calls = []
    original = transactions.parse

    def parse(text, *, path=None):
        calls.append(path)
        return original(text, path=path)

    monkeypatch.setattr(transactions, "parse", parse)
    with _forced_tier(tier):
        application = transactions.apply_mutation(
            layout, plan, baseline_bundle=baseline, parse_reuse=transactions._document_members(bundle), commit=None
        )
    assert not application.ok
    assert not application.rolled_back
    assert application.written == application.moved == ()
    assert "changed since planning" in application.failures[0]
    assert calls == []  # whole-byte/directory preconditions reject before body parsing
    assert _snapshot(layout.bundle_dir) == before
