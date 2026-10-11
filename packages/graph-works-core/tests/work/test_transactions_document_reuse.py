"""Fresh anchored reads reuse only the current invocation's matching Documents."""

from collections import Counter
from contextlib import closing

import pytest
from _transaction_helpers import _forced_tier, _plan, _workspace
from graph_works_core.work import commands
from graph_works_core.workspace import bundle as loaders
from graph_works_core.workspace import transactions
from okf_io import Document
from test_reparent_partial import _write
from work_tracker_okf.items import IGNORE


@pytest.mark.parametrize("tier", ["linux", "win32"])
def test_fresh_document_reuse_reads_each_revision_and_retains_only_clean_path_identity(tmp_path, monkeypatch, tier):
    layout = _workspace(tmp_path)
    member = "work/bug-source.md"
    page = layout.bundle_dir / member
    _write(layout, member[:-3])
    seed = Document.load(page)
    documents = transactions._FreshDocuments({member: seed})
    reads, parses = [], []
    real_read, real_parse = transactions._read_bytes_at, transactions.parse

    def read(root, path):
        reads.append(path)
        return real_read(root, path)

    def parse(text, *, path=None):
        document = real_parse(text, path=path)
        parses.append(document)
        return document

    monkeypatch.setattr(transactions, "_read_bytes_at", read)
    monkeypatch.setattr(transactions, "parse", parse)
    with _forced_tier(tier), closing(transactions._open_root(layout.bundle_dir)) as root:
        assert documents.read(root, layout.bundle_dir, member) is seed
        page.write_bytes(seed.raw_text.replace("title:", "title: changed #").encode("utf-8"))
        changed = documents.read(root, layout.bundle_dir, member)
        assert changed is not seed
        assert documents.read(root, layout.bundle_dir, member) is changed
        changed.set_body("changed dirty body\n")
        clean = documents.read(root, layout.bundle_dir, member)
        assert clean is not changed
        destination = "work/bug-destination.md"
        page.rename(layout.bundle_dir / destination)
        documents.members[destination] = clean
        moved = documents.read(root, layout.bundle_dir, destination)
        assert moved is not clean
        assert moved.path == layout.bundle_dir / destination
    assert reads == [member] * 4 + [destination]
    assert parses == [changed, clean, moved]


@pytest.mark.parametrize("tier", ["linux", "win32"])
def test_baseline_conditions_reuse_fresh_bytes_and_observe_concurrent_dependency_edits(tmp_path, monkeypatch, tier):
    layout = _workspace(tmp_path)
    source = "work/bug-source"
    _write(layout, source)
    page = layout.bundle_dir / f"{source}.md"
    seed = Document.load(page)
    documents = transactions._FreshDocuments({f"{source}.md": seed})
    plan = _plan(layout, validate_paths=(source,))
    parses = []
    real = transactions.parse

    def parse(text, *, path=None):
        parses.append(path)
        return real(text, path=path)

    monkeypatch.setattr(transactions, "parse", parse)
    with _forced_tier(tier), closing(transactions._open_root(layout.bundle_dir)) as root:
        assert transactions._scoped_baseline_conditions(plan, root, documents=documents) == Counter()
        page.write_bytes(
            seed.raw_text.replace(
                "---\n", "---\ndepends_on:\n- path: work/missing\n  blocks: execute\n  needs: resolved\n", 1
            ).encode("utf-8")
        )
        changed = transactions._scoped_baseline_conditions(plan, root, documents=documents)
        assert changed == Counter({(source, "dependency-missing"): 1})
        assert transactions._scoped_baseline_conditions(plan, root, documents=documents) == changed
    assert parses == [page]


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
    from _transaction_helpers import _snapshot

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
