"""ReadSession.content: verified page content with zero warm parses (epic D-005, D-007)."""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import pytest
from gw_kernel.read_session import BundleSession, PageContent, ReadSession, materialize, open_read_session
from gw_kernel.workspace.layout import WorkspaceLayout
from okf_io import Document, Unreadable

MARKDOWN = ("concept", "index", "log")
UNREADABLE = ("docs/explanations/bad.md", "work/epic-a/references/undecodable.md")


@pytest.fixture
def parses(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    calls: list[str] = []
    real = Document.__dict__["parse"]

    def parse(cls: type[Document], text: str, *, path: Path | None = None) -> Document:
        calls.append(text)
        return real.__func__(cls, text, path=path)

    monkeypatch.setattr(Document, "parse", classmethod(parse))
    yield calls


def _expected(root: Path, member_id: str) -> PageContent:
    doc = Document.load(root.joinpath(*member_id.split("/")))
    return PageContent(member_id, doc.fm_data(dates="iso"), doc.body, doc.parse_error, doc.fm.coercion_failures)


def _markdown_ids(session: ReadSession) -> list[str]:
    return [row.id for row in session.members() if row.kind in MARKDOWN]


def _write(workspace: WorkspaceLayout, rel: str, text: str) -> None:
    path = workspace.bundle_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    future = 1_900_000_000_000_000_000
    os.utime(path, ns=(future, future))


def _disable_index(workspace: WorkspaceLayout) -> None:
    workspace.local_manifest_path.write_text("read_index:\n  enabled: false\n", encoding="utf-8", newline="\n")


def test_index_content_equals_the_document_projection(workspace) -> None:
    with open_read_session(workspace) as session:
        assert session.backend == "index"
        ids = _markdown_ids(session)
        assert {"index.md", "log.md", "docs/explanations/yaml.md"} <= set(ids)
        for member_id in ids:
            assert session.content(member_id) == _expected(workspace.bundle_dir, member_id)


def test_index_content_warm_parses_nothing(workspace, parses) -> None:
    with open_read_session(workspace) as session:
        ids = _markdown_ids(session)
        parses.clear()
        for member_id in ids:
            assert isinstance(session.content(member_id), PageContent)
    assert parses == []


def test_index_content_after_one_edit_parses_once(index_session, workspace, parses) -> None:
    _write(workspace, "docs/explanations/p.md", "---\ntype: Explanation\ntitle: Edited\n---\nnew body\n")
    parses.clear()
    result = index_session.content("docs/explanations/p.md")
    assert len(parses) == 1
    assert isinstance(result, PageContent)
    assert (result.frontmatter["title"], result.body) == ("Edited", "new body\n")


def test_index_content_deleted_after_reconcile_is_unreadable(index_session, workspace) -> None:
    (workspace.bundle_dir / "docs/explanations/p.md").unlink()
    result = index_session.content("docs/explanations/p.md")
    assert isinstance(result, Unreadable)
    assert result.reason.startswith("could not be read: ")


def test_index_content_unreadable_and_non_markdown(index_session) -> None:
    for member_id in UNREADABLE:
        result = index_session.content(member_id)
        assert isinstance(result, Unreadable) and result.reason.startswith("not valid UTF-8: ")
    for member_id in ("pic.png", "work/epic-a/.DS_Store", "absent.md", "repositories/clone/references/git/README.md"):
        assert index_session.content(member_id) is None


def test_index_content_takes_exact_ids(index_session) -> None:
    composed = "docs/explanations/café.md"
    decomposed = unicodedata.normalize("NFD", composed)
    assert isinstance(index_session.content(composed), PageContent)
    assert index_session.content(decomposed) is None


def test_index_content_inexact_concept_parses_once(workspace, parses) -> None:
    _write(workspace, "docs/explanations/nan.md", "---\ntitle: N\nratio: .nan\n---\nbody\n")
    with open_read_session(workspace) as session:
        assert session.backend == "index"
        parses.clear()
        result = session.content("docs/explanations/nan.md")
    assert len(parses) == 1
    assert isinstance(result, PageContent)
    assert result.frontmatter["ratio"] != result.frontmatter["ratio"]


def test_index_session_after_fallback_delegates_content(index_session, workspace, monkeypatch, caplog) -> None:
    import sqlite3

    def broken(member_id: str) -> None:
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(index_session._view, "stored_content", broken)
    with caplog.at_level("WARNING"):
        result = index_session.content("docs/explanations/p.md")
    assert index_session.backend == "bundle"
    assert "serving the full bundle load" in caplog.text
    assert result == _expected(workspace.bundle_dir, "docs/explanations/p.md")


def test_bundle_content_equals_the_document_projection_with_no_extra_parse(workspace, parses) -> None:
    _disable_index(workspace)
    with open_read_session(workspace) as session:
        assert session.backend == "bundle"
        ids = _markdown_ids(session)
        parses.clear()
        results = {member_id: session.content(member_id) for member_id in ids}
        assert parses == []
        for member_id in UNREADABLE:
            assert isinstance(session.content(member_id), Unreadable)
        assert session.content("pic.png") is None
        assert session.content("absent.md") is None
    for member_id, result in results.items():
        assert result == _expected(workspace.bundle_dir, member_id)


def test_bundle_and_index_content_agree(workspace) -> None:
    bundle = BundleSession(workspace.bundle_dir, fallback="disabled")
    with open_read_session(workspace) as session:
        for member_id in _markdown_ids(session) + list(UNREADABLE):
            assert session.content(member_id) == bundle.content(member_id)


def test_sessions_expose_their_content_root(workspace, index_session) -> None:
    assert index_session.content_root == workspace.bundle_dir
    assert index_session.stored_content("index.md") is not None
    bundle = BundleSession(workspace.bundle_dir, fallback="disabled")
    assert bundle.content_root == workspace.bundle_dir
    assert bundle.stored_content("index.md") is None


def test_snapshot_of_index_serves_content_after_close(workspace, parses) -> None:
    with open_read_session(workspace) as session:
        snap = materialize(session)
        ids = _markdown_ids(session)
    parses.clear()
    for member_id in ids:
        assert snap.content(member_id) == _expected(workspace.bundle_dir, member_id)
    assert len(parses) == len(ids)  # only the expectations parsed
    parses.clear()
    _write(workspace, "docs/explanations/p.md", "---\ntype: Explanation\ntitle: Edited\n---\nnew\n")
    result = snap.content("docs/explanations/p.md")
    assert len(parses) == 1
    assert isinstance(result, PageContent) and result.body == "new\n"
    for member_id in UNREADABLE:
        assert isinstance(snap.content(member_id), Unreadable)
    assert snap.content("pic.png") is None
    assert snap.content("absent.md") is None


def test_snapshot_of_bundle_parses_once_per_call(workspace, parses) -> None:
    source = BundleSession(workspace.bundle_dir, fallback="disabled")
    snap = materialize(source)
    parses.clear()
    assert snap.content("index.md") == source.content("index.md")
    assert len(parses) == 1
    assert isinstance(snap.content(UNREADABLE[0]), Unreadable)


def test_snapshot_content_nested_mutations_do_not_contaminate_later_reads(workspace, parses) -> None:
    member_id = "docs/explanations/p.md"
    _write(workspace, member_id, "---\ntitle: P\ntags: [original]\nmeta: {owner: original}\n---\nbody\n")
    path = workspace.bundle_dir / member_id
    original_bytes = path.read_bytes()
    expected = _expected(workspace.bundle_dir, member_id)
    with open_read_session(workspace) as session:
        assert session.backend == "index"
        snap = materialize(session)
    parses.clear()

    first = snap.content(member_id)
    assert first == expected
    assert isinstance(first, PageContent)
    tags = first.frontmatter["tags"]
    meta = first.frontmatter["meta"]
    assert isinstance(tags, list) and isinstance(meta, dict)
    tags.append("injected")
    meta["owner"] = "injected"

    second = snap.content(member_id)
    assert parses == []
    assert path.read_bytes() == original_bytes
    assert second == expected


def test_snapshot_without_content_root_reports_unreadable(workspace) -> None:
    class Bare:
        """A custom session lacking the optional content accessors."""

        def __init__(self, inner: BundleSession) -> None:
            self._inner = inner

        def __getattr__(self, name: str) -> object:
            if name in ("content_root", "stored_content"):
                raise AttributeError(name)
            return getattr(self._inner, name)

    snap = materialize(Bare(BundleSession(workspace.bundle_dir, fallback="disabled")))  # type: ignore[arg-type]
    result = snap.content("index.md")
    assert isinstance(result, Unreadable) and result.reason == "content root was not captured"
    assert snap.content("absent.md") is None
