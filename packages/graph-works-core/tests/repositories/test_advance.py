from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from gitrepo import Upstream, git, make_upstream
from graph_works_core.repositories import commands
from graph_works_core.repositories.commands import run_repo_add, run_repo_advance
from graph_works_core.workspace.bundle import load_workspace_bundle
from graph_works_core.workspace.layout import WorkspaceLayout
from okf_ext.proposals import list_proposals
from okf_io import load, parse
from repositories_okf.git import remove_tree
from repositories_okf.pin import read_pin
from workspace_fixture import NOW, bundle_bytes, make_workspace, porcelain

LATER = NOW + timedelta(days=1)


@pytest.fixture
def upstream(tmp_path: Path) -> Upstream:
    (tmp_path / "up").mkdir()
    up = make_upstream(tmp_path / "up")
    up.commit({"README.md": "# demo\n", "src/a.py": "a = 1\n", "src/b.py": "b = 1\n"}, "c1", tag="v1.0.0")
    return up


@pytest.fixture
def layout(tmp_path: Path, upstream: Upstream) -> WorkspaceLayout:
    layout = make_workspace(tmp_path)
    assert run_repo_add(layout, upstream.url, name="demo", now=NOW).ok
    concepts = layout.bundle_dir / "concepts"
    concepts.mkdir(exist_ok=True)
    (concepts / "uses-a.md").write_text(
        "---\ntype: Concept\ntitle: Uses a\n---\n\nSee [a](/repositories/demo/references/git/src/a.py).\n",
        encoding="utf-8",
        newline="",
    )
    (concepts / "uses-readme.md").write_text(
        "---\ntype: Concept\ntitle: Uses readme\n---\n\nSee [r](/repositories/demo/references/git/README.md).\n",
        encoding="utf-8",
        newline="",
    )
    git(layout.root, "add", "-A")
    git(layout.root, "commit", "-q", "-m", "pages")
    return layout


def _clone(layout: WorkspaceLayout) -> Path:
    return layout.bundle_dir / "repositories" / "demo" / "references" / "git"


def _proposals_for(layout: WorkspaceLayout, target: str) -> list[object]:
    return [p for p in list_proposals(load_workspace_bundle(layout)) if p.target == target]


def test_advance_moves_the_pin_and_writes_snapshot_changelog_proposal_and_log(
    layout: WorkspaceLayout, upstream: Upstream
) -> None:
    old = git(_clone(layout), "rev-parse", "HEAD")
    upstream.commit({"src/a.py": "a = 2\n"}, "c2", tag="v1.1.0")
    new = upstream.rename("src/b.py", "src/c.py", "c3")
    result = run_repo_advance(layout, "demo", now=LATER)
    assert result.ok and result.outcome == "advanced", result.refusal
    assert (result.previous, result.commit) == (old, new)
    assert result.range is not None and (result.range.commits, result.range.rewritten, result.range.tags) == (
        2,
        False,
        ("v1.1.0",),
    )
    assert [page.page for page in result.flagged] == ["concepts/uses-a.md"]
    assert git(_clone(layout), "rev-parse", "HEAD") == new
    pin = read_pin(load(layout.bundle_dir / "repositories" / "demo.md"))
    assert pin is not None and (pin.commit, pin.previous, pin.ref) == (new, old, "main")
    snapshot = layout.bundle_dir / "repositories" / "demo" / "snapshots" / f"2026-09-30-{new[:7]}.md"
    text = snapshot.read_text(encoding="utf-8")
    assert "[Uses a](/concepts/uses-a.md)" in text and "`src/b.py` → `src/c.py`" in text
    changelog = (layout.bundle_dir / "repositories" / "demo" / "changelog.md").read_text(encoding="utf-8")
    assert changelog.index(f"2026-09-30-{new[:7]}") < changelog.index(f"2026-09-29-{old[:7]}")
    assert len(_proposals_for(layout, "concepts/uses-a.md")) == 1
    assert not _proposals_for(layout, "concepts/uses-readme.md")
    assert "**repo-advance** demo — " in (layout.bundle_dir / "log.md").read_text(encoding="utf-8")
    assert git(layout.root, "log", "-1", "--format=%s") == f"workspace: advance reference repository demo to {new[:7]}"
    assert porcelain(layout) == ""


def test_a_second_advance_merges_into_the_open_proposal(layout: WorkspaceLayout, upstream: Upstream) -> None:
    upstream.commit({"src/a.py": "a = 2\n"}, "c2")
    assert run_repo_advance(layout, "demo", now=LATER).ok
    upstream.commit({"src/a.py": "a = 3\n"}, "c3")
    assert run_repo_advance(layout, "demo", now=LATER + timedelta(days=1)).ok
    proposals = _proposals_for(layout, "concepts/uses-a.md")
    assert len(proposals) == 1
    assert len(proposals[0].sources) == 2  # type: ignore[attr-defined]


def test_a_decided_proposal_is_skipped_not_fatal(layout: WorkspaceLayout, upstream: Upstream) -> None:
    upstream.commit({"src/a.py": "a = 2\n"}, "c2")
    first = run_repo_advance(layout, "demo", now=LATER)
    proposal_path = layout.bundle_dir / first.proposals[0]
    proposal_path.write_text(
        proposal_path.read_text(encoding="utf-8").replace("page_status: proposed", "page_status: rejected"),
        encoding="utf-8",
        newline="",
    )
    git(layout.root, "commit", "-qam", "reject")
    upstream.commit({"src/a.py": "a = 3\n"}, "c3")
    second = run_repo_advance(layout, "demo", now=LATER + timedelta(days=1))
    assert second.ok and second.proposals == ()
    assert len(second.skipped) == 1 and second.skipped[0].startswith("concepts/uses-a.md: ")


def test_up_to_date_writes_nothing(layout: WorkspaceLayout) -> None:
    before = bundle_bytes(layout)
    head = git(layout.root, "rev-parse", "HEAD")
    result = run_repo_advance(layout, "demo", now=LATER)
    assert result.ok and result.outcome == "up-to-date"
    assert bundle_bytes(layout) == before and git(layout.root, "rev-parse", "HEAD") == head


def test_dry_run_reports_range_flags_and_paths_and_changes_nothing(layout: WorkspaceLayout, upstream: Upstream) -> None:
    old = git(_clone(layout), "rev-parse", "HEAD")
    new = upstream.commit({"src/a.py": "a = 2\n"}, "c2")
    before = bundle_bytes(layout)
    result = run_repo_advance(layout, "demo", now=LATER, dry_run=True)
    assert result.ok and result.dry_run and result.commit == new
    assert [page.page for page in result.flagged] == ["concepts/uses-a.md"]
    assert any(path.startswith("proposals/") for path in result.paths)
    assert f"repositories/demo/snapshots/2026-09-30-{new[:7]}.md" in result.paths
    assert bundle_bytes(layout) == before
    assert git(_clone(layout), "rev-parse", "HEAD") == old


def test_a_rewritten_upstream_proceeds_from_the_merge_base(layout: WorkspaceLayout, upstream: Upstream) -> None:
    upstream.commit({"src/a.py": "a = 2\n"}, "c2")
    assert run_repo_advance(layout, "demo", now=LATER).ok
    new = upstream.rewrite({"src/a.py": "a = 3\n"}, "c2-rewritten")
    result = run_repo_advance(layout, "demo", now=LATER + timedelta(days=1))
    assert result.ok and result.range is not None and result.range.rewritten
    snapshot = layout.bundle_dir / "repositories" / "demo" / "snapshots" / f"2026-10-01-{new[:7]}.md"
    assert parse(snapshot.read_text(encoding="utf-8")).fm_data(dates="iso")["rewritten"] is True


def test_to_a_tag(layout: WorkspaceLayout, upstream: Upstream) -> None:
    tagged = upstream.commit({"src/a.py": "a = 2\n"}, "c2", tag="v1.1.0")
    upstream.commit({"src/a.py": "a = 3\n"}, "c3")
    result = run_repo_advance(layout, "demo", to="v1.1.0", now=LATER)
    assert result.ok and result.commit == tagged
    pin = read_pin(load(layout.bundle_dir / "repositories" / "demo.md"))
    assert pin is not None and (pin.ref, pin.describe) == ("v1.1.0", "v1.1.0")


def test_preconditions_are_refusals(layout: WorkspaceLayout, upstream: Upstream) -> None:
    assert run_repo_advance(layout, "nope", now=LATER).refusal.code == "not-found"  # type: ignore[union-attr]
    lane = layout.bundle_dir / "repositories"
    (lane / "managed.md").write_text(
        f"---\ntype: ManagedRepository\ntitle: m\ndescription: d\nurl: {upstream.url}\n---\n\n## Summary\n\nx\n",
        encoding="utf-8",
        newline="",
    )
    assert run_repo_advance(layout, "managed", now=LATER).refusal.code == "no-pin"  # type: ignore[union-attr]
    (lane / "unpinned.md").write_text(
        f"---\ntype: ReferenceRepository\ntitle: u\ndescription: d\nurl: {upstream.url}\n---\n\n## Summary\n\nx\n",
        encoding="utf-8",
        newline="",
    )
    assert run_repo_advance(layout, "unpinned", now=LATER).refusal.code == "no-pin"  # type: ignore[union-attr]
    assert run_repo_advance(layout, "demo", to="no-such-ref", now=LATER).refusal.code == "ref-not-found"  # type: ignore[union-attr]
    (_clone(layout) / "scratch.txt").write_text("x\n", encoding="utf-8", newline="")
    assert run_repo_advance(layout, "demo", now=LATER).refusal.code == "clone-dirty"  # type: ignore[union-attr]
    (_clone(layout) / "scratch.txt").unlink()
    git(_clone(layout), "remote", "set-url", "origin", "https://example.com/elsewhere.git")
    assert run_repo_advance(layout, "demo", now=LATER).refusal.code == "url-mismatch"  # type: ignore[union-attr]
    remove_tree(_clone(layout))
    assert run_repo_advance(layout, "demo", now=LATER).refusal.code == "clone-missing"  # type: ignore[union-attr]


def test_a_write_failure_restores_pages_and_the_old_pin(
    layout: WorkspaceLayout, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = git(_clone(layout), "rev-parse", "HEAD")
    upstream.commit({"src/a.py": "a = 2\n"}, "c2")
    before = bundle_bytes(layout)

    def boom(*_args: object, **_kwargs: object) -> str | None:
        raise OSError("disk full")

    monkeypatch.setattr(commands, "_append_log", boom)
    result = run_repo_advance(layout, "demo", now=LATER)
    assert result.refusal is not None and result.refusal.code == "write-failed"
    assert bundle_bytes(layout) == before
    assert git(_clone(layout), "rev-parse", "HEAD") == old
    assert porcelain(layout) == ""


def test_revisiting_a_pin_preserves_the_snapshot_and_authored_doc_impact(
    layout: WorkspaceLayout, upstream: Upstream
) -> None:
    visited = upstream.commit({"src/a.py": "a = 2\n"}, "c2", tag="v1.1.0")
    assert run_repo_advance(layout, "demo", now=LATER).ok
    snapshot = layout.bundle_dir / "repositories" / "demo" / "snapshots" / f"2026-09-30-{visited[:7]}.md"
    snapshot.write_text(
        snapshot.read_text(encoding="utf-8") + "\nHuman review: update the explanation.\n", encoding="utf-8", newline=""
    )
    authored = snapshot.read_bytes()
    git(layout.root, "commit", "-qam", "review snapshot")
    upstream.commit({"src/a.py": "a = 3\n"}, "c3")
    assert run_repo_advance(layout, "demo", now=LATER).ok
    result = run_repo_advance(layout, "demo", to="v1.1.0", now=LATER)
    assert result.ok and result.commit == visited
    assert snapshot.read_bytes() == authored
    assert porcelain(layout) == ""


def test_advance_uses_pages_and_snapshots_added_during_fetch(
    layout: WorkspaceLayout, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repositories_okf.snapshots import Snapshot, render_snapshot

    original = commands.fetch
    old = git(_clone(layout), "rev-parse", "HEAD")
    new = upstream.commit({"src/a.py": "a = 2\n"}, "c2")
    extra = Snapshot("demo", old, "2026-09-28T20:40:00Z", None, "2026-09-28T20:40:00Z")

    def fetch(*args: object, **kwargs: object) -> object:
        page = layout.bundle_dir / "concepts/during-fetch.md"
        page.write_text(
            "---\ntype: Concept\ntitle: During fetch\n---\n\nSee [a](/repositories/demo/references/git/src/a.py).\n",
            encoding="utf-8",
            newline="",
        )
        target = layout.bundle_dir / extra.path
        target.write_text(render_snapshot(extra), encoding="utf-8", newline="")
        return original(*args, **kwargs)

    monkeypatch.setattr(commands, "fetch", fetch)
    result = run_repo_advance(layout, "demo", now=LATER)
    assert result.ok and result.commit == new
    assert "concepts/during-fetch.md" in [page.page for page in result.flagged]
    changelog = (layout.bundle_dir / "repositories/demo/changelog.md").read_text(encoding="utf-8")
    assert extra.path in changelog
    assert _proposals_for(layout, "concepts/during-fetch.md")


def test_advance_refuses_a_pin_changed_during_fetch(
    layout: WorkspaceLayout, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from repositories_okf.pin import write_pin

    original = commands.fetch
    new = upstream.commit({"src/a.py": "a = 2\n"}, "c2")
    after: dict[str, bytes] = {}

    def fetch(*args: object, **kwargs: object) -> object:
        nonlocal after
        result = original(*args, **kwargs)
        path = layout.bundle_dir / "repositories/demo.md"
        document = load(path)
        pin = read_pin(document)
        assert pin is not None
        write_pin(document, replace(pin, commit=new))
        path.write_text(document.serialize(), encoding="utf-8", newline="")
        after = bundle_bytes(layout)
        return result

    monkeypatch.setattr(commands, "fetch", fetch)
    result = run_repo_advance(layout, "demo", now=LATER)
    assert result.refusal is not None and result.refusal.code == "git-failed"
    assert bundle_bytes(layout) == after
    assert git(_clone(layout), "rev-parse", "HEAD") == new


@pytest.mark.parametrize("competitor", ["advance", "restore"])
def test_repository_operations_wait_until_failed_advance_finishes_head_rollback(
    layout: WorkspaceLayout, upstream: Upstream, monkeypatch: pytest.MonkeyPatch, competitor: str
) -> None:
    from contextlib import contextmanager
    from threading import Event, Thread, current_thread

    from graph_works_core.repositories.commands import run_repo_restore
    from okf_ext.locking import locked

    old = git(_clone(layout), "rev-parse", "HEAD")
    upstream.commit({"src/a.py": "a = 2\n"}, "c2", tag="v1.1.0")
    new = upstream.commit({"src/a.py": "a = 3\n"}, "c3")
    rollback_waiting = Event()
    release_rollback = Event()
    competitor_waiting = Event()
    competitor_done = Event()
    original_detach = commands.detach
    original_log = commands._append_log
    outcomes: dict[str, object] = {}

    def detach(*args, **kwargs):
        if current_thread().name == "advance-A" and args[2] == old:
            rollback_waiting.set()
            assert release_rollback.wait(10)
        return original_detach(*args, **kwargs)

    def append_log(*args, **kwargs):
        if current_thread().name == "advance-A":
            raise OSError("injected write failure")
        return original_log(*args, **kwargs)

    @contextmanager
    def operation_lock(path, **kwargs):
        if current_thread().name == "operation-B":
            competitor_waiting.set()
        with locked(path, **kwargs):
            yield

    def first() -> None:
        outcomes["first"] = run_repo_advance(layout, "demo", to="v1.1.0", now=LATER)

    def second() -> None:
        outcomes["second"] = (
            run_repo_advance(layout, "demo", now=LATER)
            if competitor == "advance"
            else run_repo_restore(layout, ["demo"])
        )
        competitor_done.set()

    monkeypatch.setattr(commands, "detach", detach)
    monkeypatch.setattr(commands, "_append_log", append_log)
    monkeypatch.setattr(commands, "locked", operation_lock)
    a = Thread(target=first, name="advance-A")
    b = Thread(target=second, name="operation-B")
    a.start()
    try:
        assert rollback_waiting.wait(10)
        b.start()
        assert competitor_waiting.wait(10)
        # A has already released its bundle lock, but still owns the repository lock.
        with pytest.raises(OSError), locked(commands._operation_lock_path(layout, "demo"), blocking=False):
            pytest.fail("failed advance released repository ownership before HEAD rollback")
        assert not competitor_done.is_set()
    finally:
        release_rollback.set()
        a.join(10)
        if b.ident is not None:
            b.join(10)
    assert not a.is_alive() and not b.is_alive()
    first_result = outcomes["first"]
    assert first_result.refusal is not None and first_result.refusal.code == "write-failed"
    assert outcomes["second"].ok
    pin = read_pin(load(layout.bundle_dir / "repositories/demo.md"))
    assert pin is not None
    assert git(_clone(layout), "rev-parse", "HEAD") == pin.commit == (new if competitor == "advance" else old)
    assert porcelain(layout) == ""
