"""Reparent structural budgets; corpus growth cannot widen a fixed nested closure.

Corpora are shared only for planning. Live probes copy them before mutation.
The generator exposes active_items as a count, so selectors inspect actual
generated work-item pages. Like the existing read invariants, both sizes run
without an unregistered slow marker.
"""

from __future__ import annotations

import shutil
import sys
from collections import Counter
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from gw_pipeline import transactions
from gw_work import commands
from markdown_it import MarkdownIt
from okf_ext import body
from okf_io import Document, _md
from work_tracker_okf import mutation

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))

from read_bench_corpus import generate
from read_bench_counters import counting

SIZES = (100, 1_000)
SOURCE = "work/epic-00000/children/feature-00001"
PARENT = "work/epic-00010"
SOURCES = tuple(f"work/epic-00000/children/feature-{i:05d}" for i in range(1, 4))


@contextmanager
def _audited_counting():
    """Cold class-parser audit, retaining objects so identity cannot be recycled."""
    _md.parse_body.cache_clear()
    body._skeleton.cache_clear()
    # An instance method shadow would bypass counting()'s class-level spy.
    assert "parse" not in vars(_md._MD)
    assert "parse" not in vars(body._MD)
    documents, revisions, profiles, table_bodies = [], {}, Counter(), []
    current = []
    with counting() as counts, pytest.MonkeyPatch.context() as patch:
        real_parse, real_markdown, real_md = Document.parse, Document.markdown, MarkdownIt.parse

        def parse(cls, text, *, path=None):
            document = real_parse(text, path=path)
            documents.append(document)
            return document

        def markdown(document):
            current.append(document)
            try:
                return real_markdown(document)
            finally:
                current.pop()

        def md(parser, text, env=None):
            assert "parse" not in vars(parser)
            if parser is _md._MD and current:
                document = current[-1]
                assert text is document.body
                key = (id(document), id(text))
                if key not in revisions:
                    revisions[key] = [document, text, 0]
                revisions[key][2] += 1
                profiles["document"] += 1
            elif "table" in parser.get_active_rules()["block"]:
                profiles["table-render"] += 1
                table_bodies.append(text)
            else:
                profiles["standalone"] += 1
            return real_md(parser, text, env)

        patch.setattr(Document, "parse", classmethod(parse))
        patch.setattr(Document, "markdown", markdown)
        patch.setattr(MarkdownIt, "parse", md)
        yield counts, documents, revisions, profiles, table_bodies
        assert sum(profiles.values()) == counts.markdown_parses
        assert profiles["standalone"] == 0, profiles
        assert all(event[2] == 1 for event in revisions.values()), [
            (document.path, n) for document, _revision, n in revisions.values() if n != 1
        ]


def _copy_layout(corpus, root):
    shutil.copytree(corpus.layout.root, root)
    return replace(
        corpus.layout,
        root=root,
        bundle_dir=root / "okf",
        config_dir=root / ".gw",
        cache_dir=root / ".gw/cache",
        worktrees_dir=root / ".gw/worktrees",
    )


@pytest.fixture(scope="module")
def corpus_for(tmp_path_factory):
    root = tmp_path_factory.mktemp("reparent-count-corpora")
    corpora = {}

    def make(items):
        if items not in corpora:
            corpora[items] = generate(root / str(items), items=items, wiki_pages=100)
        return corpora[items]

    return make


@pytest.mark.parametrize("items", SIZES)
@pytest.mark.parametrize("batch, expected", ((1, 11), (3, 17)))
def test_planning_parses_each_selected_file_and_document_body_once(corpus_for, items, batch, expected):
    corpus = corpus_for(items)
    assert (corpus.layout.bundle_dir / f"{SOURCE}.md").is_file()
    assert (corpus.layout.bundle_dir / f"{PARENT}.md").is_file()
    with _audited_counting() as (counts, documents, revisions, profiles, _tables):
        bundle, plan = commands._plan_reparent_partial(corpus.layout, SOURCES[:batch], PARENT)
    assert plan.ok, plan.refusals
    assert bundle.partial is not None
    assert counts.walks == 1
    assert counts.files_parsed == len(bundle.partial) == expected
    assert counts.markdown_parses == len(revisions) == expected
    assert profiles == {"document": expected}
    assert Counter(doc.path.relative_to(bundle.root).as_posix() for doc in documents) == Counter(bundle.partial)


@pytest.mark.parametrize("batch", (1, 3))
def test_fixed_nested_move_has_exact_equal_counts_at_both_sizes(corpus_for, batch):
    seen = []
    for items in SIZES:
        corpus = corpus_for(items)
        with _audited_counting() as (counts, _documents, _revisions, _profiles, _tables):
            bundle, plan = commands._plan_reparent_partial(corpus.layout, SOURCES[:batch], PARENT)
        assert plan.ok and bundle.partial is not None
        seen.append((bundle.partial, counts.files_parsed, counts.markdown_parses, counts.walks))
    assert seen[0] == seen[1]


@pytest.mark.parametrize("items", SIZES)
@pytest.mark.parametrize(
    "batch, planning_count, post_count, document_md, render_md",
    ((1, 11, 5, 15, 4), (3, 17, 11, 24, 7)),
)
def test_cold_live_move_has_one_plan_apply_and_locked_reload_with_exact_profiles(
    corpus_for, tmp_path, monkeypatch, items, batch, planning_count, post_count, document_md, render_md
):
    layout = _copy_layout(corpus_for(items), tmp_path / "live")
    calls, planned, reloaded = Counter(), [], []
    held = set()
    real_plan, real_apply = mutation.plan_move_many, commands.apply_mutation
    real_partial, real_load = commands._plan_reparent_partial, transactions._load_bundle_through
    real_bundle_lock, real_executor_lock = transactions._bundle_root_lock, transactions._executor_lock

    def plan(*args, **kwargs):
        calls["plan"] += 1
        return real_plan(*args, **kwargs)

    def partial(*args, **kwargs):
        result = real_partial(*args, **kwargs)
        planned.append(result[0])
        return result

    def apply(*args, **kwargs):
        calls["apply"] += 1
        assert kwargs["baseline_bundle"].partial is not None
        return real_apply(*args, **kwargs)

    @contextmanager
    def bundle_lock(*args, **kwargs):
        with real_bundle_lock(*args, **kwargs):
            held.add("bundle")
            try:
                yield
            finally:
                held.remove("bundle")

    @contextmanager
    def executor_lock(*args, **kwargs):
        with real_executor_lock(*args, **kwargs):
            held.add("executor")
            try:
                yield
            finally:
                held.remove("executor")

    def load(*args, **kwargs):
        assert held == {"bundle", "executor"}
        assert kwargs["only"] is not None
        calls["locked-reload"] += 1
        result = real_load(*args, **kwargs)
        reloaded.append(result)
        return result

    monkeypatch.setattr(mutation, "plan_move_many", plan)
    monkeypatch.setattr(commands, "_plan_reparent_partial", partial)
    monkeypatch.setattr(commands, "apply_mutation", apply)
    monkeypatch.setattr(transactions, "_load_bundle_through", load)
    monkeypatch.setattr(transactions, "_bundle_root_lock", bundle_lock)
    monkeypatch.setattr(transactions, "_executor_lock", executor_lock)
    with _audited_counting() as (counts, documents, revisions, profiles, table_bodies):
        result = commands.run_reparent(layout, SOURCES[:batch], PARENT, dry_run=False)
    assert result.application is not None and result.application.ok, result.application
    assert not result.application.failures and not result.application.rolled_back
    assert calls == {"plan": 1, "apply": 1, "locked-reload": 1}
    assert len(planned) == len(reloaded) == 1
    assert counts.walks == 2  # one inventory; one independent post-effect walk under both locks
    assert counts.files_parsed == planning_count + post_count
    assert counts.link_graph_builds == 3
    assert profiles == {"document": document_md, "table-render": render_md}
    assert counts.markdown_parses == document_md + render_md
    assert len(revisions) == document_md
    assert all(doc.path is not None for doc in documents)  # no synthetic rendered-page reparse

    before, after = planned[0], reloaded[0]
    before_members, after_members = transactions._document_members(before), transactions._document_members(after)
    assert before.partial is not None and len(before.partial) == planning_count
    assert after.partial is not None  # ordinary path did not complete an unrelated corpus
    written = {write.member for write in result.plan.writes}
    parsed = Counter(doc.path.relative_to(layout.bundle_dir).as_posix() for doc in documents)
    expected = Counter(before.partial)
    fresh_members = set()
    for member, document in after_members.items():
        original = before_members.get(member)
        if original is not None and member not in written:
            assert document is original
        else:
            assert document is not original
            fresh_members.add(member)
            expected[member] += 1
    assert len(fresh_members) == post_count
    assert parsed == expected
    for source in SOURCES[:batch]:
        destination = f"{PARENT}/children/{source.rsplit('/', 1)[1]}.md"
        assert after_members[destination] is not before_members[f"{source}.md"]
        assert after_members[destination].path == layout.bundle_dir / destination
        for artifact in ("01-design.md", "02-plan.md"):
            member = f"{source}/references/{artifact}"
            assert parsed[member] == 1
            assert not any(
                doc.path == layout.bundle_dir / f"{destination[:-3]}/references/{artifact}" for doc in documents
            )

    # Known UNMET full-live budget: table-enabled render validation parses the
    # same body again with a distinct profile. Keep it explicit, never xfail or
    # count it as Document parse-once acceptance. The base profile remains once.
    assert len(table_bodies) == render_md
    assert all(any(revision is text for _doc, revision, _n in revisions.values()) for text in table_bodies)


def test_fresh_reads_reuse_identity_and_parse_changed_document_and_body_revision_once(corpus_for, tmp_path):
    layout = _copy_layout(corpus_for(100), tmp_path / "revisions")
    member = f"{SOURCE}.md"
    page = layout.bundle_dir / member
    seed = Document.load(page)
    fresh = transactions._FreshDocuments({member: seed})
    with (
        _audited_counting() as (counts, documents, revisions, profiles, _tables),
        closing(transactions._open_root(layout.bundle_dir)) as root,
    ):
        assert fresh.read(root, layout.bundle_dir, member) is seed
        seed.body_index()
        seed.body_index()
        page.write_bytes((seed.raw_text + "\nChanged physical revision.\n").encode("utf-8"))
        changed = fresh.read(root, layout.bundle_dir, member)
        assert changed is not seed
        assert fresh.read(root, layout.bundle_dir, member) is changed
        changed.body_index()
        changed.body_index()
        changed.set_body(changed.body + "\nChanged in-memory body revision.\n")
        changed.body_index()
        changed.body_index()
    assert documents == [changed]
    assert counts.files_parsed == 1
    assert counts.markdown_parses == len(revisions) == 3
    assert profiles == {"document": 3}
    assert counts.walks == 0
