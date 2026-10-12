"""Fresh anchored reads reuse only the current invocation's matching Documents."""

from collections import Counter
from contextlib import closing

import pytest
from gw_kernel.testing.transactions import plan as _plan
from gw_kernel.testing.transactions import workspace as _workspace
from gw_pipeline import transactions
from gw_pipeline.testing import forced_tier as _forced_tier
from okf_io import Document


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


def _write(layout, path, *, kind="Bug", target=None, terminal=False, needs="resolved"):
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    edge = "" if target is None else f"depends_on:\n- path: {target}\n  blocks: execute\n  needs: {needs}\n"
    page.write_text(
        f"---\ntitle: {path}\ndescription: D\ntype: {kind}\nstatus: stable\n"
        f"work_status: {'resolved' if terminal else 'open'}\nphase: execute\neffort: medium\n"
        f"opened: 2026-08-01\nupdated: 2026-08-01\n{edge}---\n\n## Summary\nD\n",
        encoding="utf-8",
        newline="",
    )
