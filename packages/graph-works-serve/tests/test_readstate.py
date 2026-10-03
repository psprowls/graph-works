from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

import pytest
from graph_works_core.events import Change, ChangeEvent, EventKind
from graph_works_core.read_session import ReadSession
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve.readstate import ReadState
from readstate_fakes import FakeOpener, FakeSession, state_for

PAGE = ChangeEvent(EventKind.PAGE, "docs/p", "docs/p.md", Change.MODIFIED)
CONFIG = ChangeEvent(EventKind.CONFIG, "manifest", None, Change.MODIFIED)


def test_first_build_and_memo(workspace: WorkspaceLayout) -> None:
    opener = FakeOpener()
    state = state_for(opener)
    for _ in range(3):
        with state.session(workspace) as (gen, value):
            assert gen == 1 and value is opener.value
    state.advance("index")
    with state.session(workspace) as (gen, _):
        assert gen == 2
    assert opener.calls == [True, False]


@pytest.mark.parametrize(
    ("value", "batch", "reason"),
    [
        (FakeSession(generation=2), (PAGE,), "index"),
        (FakeSession(backend="bundle", fallback="disabled", generation=None), (PAGE,), "fallback"),
        (FakeSession(backend="bundle", fallback="busy", generation=None), (PAGE,), "reconcile-failed"),
        (FakeSession(backend="bundle", fallback="error", generation=None), (PAGE,), "reconcile-failed"),
        (FakeSession(backend="bundle", fallback="unavailable", generation=None), (PAGE,), "reconcile-failed"),
        (FakeSession(), (CONFIG,), "config"),
    ],
)
def test_bump_rules(
    workspace: WorkspaceLayout, value: FakeSession, batch: tuple[ChangeEvent, ...], reason: str
) -> None:
    opener = FakeOpener()
    state = state_for(opener)
    with state.session(workspace):
        pass
    opener.value = value
    assert state.reconcile(workspace, batch) == 2
    assert state.bumps == [(2, reason)]


def test_unchanged_and_write_through(workspace: WorkspaceLayout) -> None:
    opener = FakeOpener()
    state = state_for(opener)
    with state.session(workspace):
        pass
    assert state.reconcile(workspace, (PAGE,)) == 1
    assert state.reconcile(workspace, ()) == 1
    opener.value = FakeSession(generation=5)
    assert state.write_through(workspace) == 2
    assert state.write_through(workspace) == 2
    assert state.bumps == [(2, "write-through")]
    opener.value = FakeSession(backend="bundle", fallback="disabled", generation=None)
    assert state.write_through(workspace) == 3


def test_failed_reconcile_and_disabled_force_build(workspace: WorkspaceLayout) -> None:
    opener = FakeOpener()
    state = state_for(opener)
    with state.session(workspace):
        pass
    opener.fail = OSError("gone")
    assert state.reconcile(workspace, (PAGE,)) == 2
    opener.fail = None
    with state.session(workspace):
        pass
    assert opener.calls[-1] is True
    opener.value = FakeSession(backend="bundle", fallback="disabled", generation=None)
    state.reconcile(workspace, (PAGE, CONFIG))
    with state.session(workspace):
        pass
    assert opener.calls[-1] is True


def test_fallback_build_clears_slot_but_handler_exception_is_not_caught(workspace: WorkspaceLayout) -> None:
    opener = FakeOpener()
    builds = []

    def materializer(s: ReadSession) -> ReadSession:
        builds.append(1)
        if len(builds) == 1:
            raise RuntimeError("build")
        return s

    state = ReadState(opener=opener, materializer=materializer)
    with state.session(workspace) as (gen, s):
        assert gen == 1 and s is opener.value
    with pytest.raises(KeyError), state.session(workspace):
        raise KeyError("handler")
    assert len(builds) == 2 and opener.calls == [True, True, True]
    opener.fail = RuntimeError("all opens fail")
    state.advance("resync")
    with pytest.raises(RuntimeError), state.session(workspace):
        pass


def run_read(
    state: ReadState, layout: WorkspaceLayout, results: list[tuple[int, ReadSession]], errors: list[BaseException]
) -> None:
    try:
        with state.session(layout) as value:
            results.append(value)
    except BaseException as exc:
        errors.append(exc)


def join(*threads: threading.Thread) -> None:
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()


def test_single_flight(workspace: WorkspaceLayout) -> None:
    pinned, release = threading.Event(), threading.Event()
    opener = FakeOpener()

    def materializer(s: ReadSession) -> ReadSession:
        pinned.set()
        assert release.wait(5)
        return s

    state = ReadState(opener=opener, materializer=materializer)
    results, errors = [], []
    threads = [threading.Thread(target=run_read, args=(state, workspace, results, errors)) for _ in range(8)]
    for t in threads:
        t.start()
    assert pinned.wait(5)
    release.set()
    join(*threads)
    assert not errors and len(results) == 8
    assert [g for g, _ in results] == [1] * 8
    assert opener.calls == [True]


@pytest.mark.parametrize("invalidate", ["advance", "stale", "identity"])
def test_late_pinned_build_cannot_clear_new_stale_state(workspace: WorkspaceLayout, invalidate: str) -> None:
    pinned, release = threading.Event(), threading.Event()
    opener = FakeOpener()
    first = True

    def materializer(s: ReadSession) -> ReadSession:
        nonlocal first
        if first:
            first = False
            pinned.set()
            assert release.wait(5)
        return s

    state = ReadState(opener=opener, materializer=materializer)
    results, errors = [], []
    old = threading.Thread(target=run_read, args=(state, workspace, results, errors))
    old.start()
    assert pinned.wait(5)
    layout = workspace
    if invalidate == "identity":
        layout = replace(workspace, bundle_dir=workspace.root / "other")
        new = threading.Thread(target=run_read, args=(state, layout, results, errors))
        new.start()
        # Ensure the new request atomically selected its identity before releasing old I/O.
        assert wait_generation(state, 2)
    else:
        state.mark_stale()
        if invalidate == "advance":
            state.advance("resync")
    release.set()
    join(old)
    if invalidate == "identity":
        join(new)
    else:
        # mark_stale alone invalidates bookkeeping, then advance selects a fresh memo.
        if invalidate == "stale":
            state.advance("resync")
        with state.session(layout):
            pass
    assert not errors
    assert opener.calls == [True, True]
    assert results[0][0] == 1


def wait_generation(state: ReadState, expected: int) -> bool:
    # Condition polling is bounded and yields; never a busy spin.
    for _ in range(500):
        if state.generation == expected:
            return True
        threading.Event().wait(0.01)
    return False


def test_local_reconciles_are_serialized(workspace: WorkspaceLayout) -> None:
    entered, release, second_entered = threading.Event(), threading.Event(), threading.Event()
    calls = []

    @contextmanager
    def opener(layout: WorkspaceLayout, *, reconcile: bool = True) -> Iterator[FakeSession]:
        calls.append(1)
        number = len(calls)
        if number == 1:
            entered.set()
            assert release.wait(5)
        else:
            second_entered.set()
        yield FakeSession(generation=number)

    state = ReadState(opener=opener, materializer=lambda s: s)
    errors = []

    def reconcile() -> None:
        try:
            state.reconcile(workspace, (PAGE,))
        except BaseException as exc:
            errors.append(exc)

    old = threading.Thread(target=reconcile)
    old.start()
    assert entered.wait(5)
    new = threading.Thread(target=reconcile)
    new.start()
    assert not second_entered.wait(0.05)
    release.set()
    join(old, new)
    assert not errors and second_entered.is_set()
    assert state.bumps == [(2, "index"), (3, "index")]


def test_failure_completion_does_not_override_resync(workspace: WorkspaceLayout) -> None:
    entered, release = threading.Event(), threading.Event()

    @contextmanager
    def opener(layout: WorkspaceLayout, *, reconcile: bool = True) -> Iterator[FakeSession]:
        entered.set()
        assert release.wait(5)
        raise OSError("obsolete failure")
        yield

    state = ReadState(opener=opener)
    thread = threading.Thread(target=lambda: state.reconcile(workspace, (PAGE,)))
    thread.start()
    assert entered.wait(5)
    state.mark_stale()
    state.advance("resync")
    release.set()
    join(thread)
    assert state.bumps == [(2, "resync")]


def test_real_disable_edit_enable(workspace: WorkspaceLayout) -> None:
    state = ReadState()
    with state.session(workspace) as (_, session):
        assert session.backend == "index"
        assert session.member("docs/new.md") is None
    manifest = workspace.manifest_path
    original = manifest.read_text(encoding="utf-8")
    manifest.write_text(original + "\nread_index:\n  enabled: false\n", encoding="utf-8", newline="\n")
    state.reconcile(workspace, (CONFIG,))
    page = workspace.bundle_dir / "docs/new.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\ntitle: latest\n---\n", encoding="utf-8", newline="\n")
    state.reconcile(workspace, (PAGE,))
    manifest.write_text(original, encoding="utf-8", newline="\n")
    state.reconcile(workspace, (CONFIG,))
    with state.session(workspace) as (_, session):
        assert session.backend == "index"
        assert session.member("docs/new.md").title == "latest"
    assert state.reconcile(workspace, (PAGE,)) == state.generation


def test_failed_build_fallback_reconcile_is_serialized(workspace: WorkspaceLayout) -> None:
    fallback_entered, release, other_entered = threading.Event(), threading.Event(), threading.Event()
    calls = []

    @contextmanager
    def opener(layout: WorkspaceLayout, *, reconcile: bool = True) -> Iterator[FakeSession]:
        calls.append(reconcile)
        if len(calls) == 2:
            fallback_entered.set()
            assert release.wait(5)
        elif len(calls) == 3:
            other_entered.set()
        yield FakeSession()

    def fail_materialize(session: ReadSession) -> ReadSession:
        raise RuntimeError("materialize failed")

    state = ReadState(opener=opener, materializer=fail_materialize)
    results, errors = [], []
    read = threading.Thread(target=run_read, args=(state, workspace, results, errors))
    read.start()
    assert fallback_entered.wait(5)
    write = threading.Thread(target=lambda: state.write_through(workspace))
    write.start()
    overlapped = other_entered.wait(0.1)
    release.set()
    join(read, write)
    assert not errors
    assert not overlapped
    assert calls == [True, True, True]


def test_successful_obsolete_reconcile_does_not_publish_baseline(workspace: WorkspaceLayout) -> None:
    entered, release = threading.Event(), threading.Event()
    opener = FakeOpener(FakeSession(generation=2))
    original = opener.__call__

    @contextmanager
    def blocked(layout: WorkspaceLayout, *, reconcile: bool = True) -> Iterator[FakeSession]:
        with original(layout, reconcile=reconcile) as session:
            if len(opener.calls) == 1:
                entered.set()
                assert release.wait(5)
            yield session

    state = ReadState(opener=blocked, materializer=lambda s: s)
    old = threading.Thread(target=lambda: state.reconcile(workspace, (PAGE,)))
    old.start()
    assert entered.wait(5)
    state.mark_stale()
    state.advance("resync")
    release.set()
    join(old)
    assert state.bumps == [(2, "resync")]
    # The old completion cannot suppress publication of the same index generation.
    assert state.write_through(workspace) == 3
    assert state.bumps[-1] == (3, "write-through")


def test_warm_build_and_unchanged_reconcile_parse_nothing(
    workspace: WorkspaceLayout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    sync = importlib.import_module("okf_ext.readindex.sync")
    state = ReadState()
    with state.session(workspace):
        pass
    parsed = []
    original = sync.read_member

    def counted(*args: object, **kwargs: object) -> object:
        parsed.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(sync, "read_member", counted)
    state.advance("index")
    with state.session(workspace) as (generation, session):
        assert session.backend == "index"
    assert state.reconcile(workspace, (PAGE,)) == generation
    assert parsed == []


def test_old_identity_failure_preserves_new_future(workspace: WorkspaceLayout) -> None:
    entered, release = threading.Event(), threading.Event()
    moved = replace(workspace, bundle_dir=workspace.root / "other")
    calls = []

    @contextmanager
    def opener(layout: WorkspaceLayout, *, reconcile: bool = True) -> Iterator[FakeSession]:
        calls.append(layout.bundle_dir)
        yield FakeSession(generation=10 if layout.bundle_dir == workspace.bundle_dir else 20)

    def materializer(session: ReadSession) -> ReadSession:
        if session.generation == 10:
            entered.set()
            assert release.wait(5)
            raise RuntimeError("old identity build")
        return session

    state = ReadState(opener=opener, materializer=materializer)
    old_results, new_results, errors = [], [], []
    old = threading.Thread(target=run_read, args=(state, workspace, old_results, errors))
    old.start()
    assert entered.wait(5)
    new = threading.Thread(target=run_read, args=(state, moved, new_results, errors))
    new.start()
    assert wait_generation(state, 2)
    release.set()
    join(old, new)
    assert not errors
    assert old_results[0][0] == 1 and old_results[0][1].generation == 10
    assert new_results[0][0] == 2 and new_results[0][1].generation == 20
    with state.session(moved) as (generation, session):
        assert generation == 2 and session.generation == 20
    assert calls.count(moved.bundle_dir) == 1
    assert state.reconcile(moved, (PAGE,)) == 2


@pytest.mark.parametrize("operation", ["watcher", "write-through"])
@pytest.mark.parametrize("damage", ["deleted", "corrupt"])
@pytest.mark.parametrize("independent_rebuild", [False, True])
def test_replaced_index_with_same_numeric_generation_invalidates_memo(
    workspace: WorkspaceLayout,
    operation: str,
    damage: str,
    independent_rebuild: bool,
) -> None:
    from graph_works_core.read_session import database_path, open_read_session

    page = workspace.bundle_dir / "docs/replaced.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\ntitle: before\n---\n", encoding="utf-8", newline="\n")
    state = ReadState()
    with state.session(workspace) as (before, session):
        old_index_generation = session.generation
        assert session.member("docs/replaced.md").title == "before"
    db = database_path(workspace)
    if damage == "deleted":
        db.unlink()
    else:
        db.write_bytes(b"not a sqlite database")
    page.write_text("---\ntitle: after replacement\n---\n", encoding="utf-8", newline="\n")
    if independent_rebuild:
        # A separate core reader rebuilt before the watcher/apply arrives.
        # Persisted epoch is essential: this sidecar opener has no rebuild flag.
        with open_read_session(workspace) as rebuilt:
            assert rebuilt.generation == old_index_generation
            assert rebuilt.member("docs/replaced.md").title == "after replacement"
    generation = state.write_through(workspace) if operation == "write-through" else state.reconcile(workspace, (PAGE,))
    assert generation > before
    assert state.bumps[-1][1] == ("write-through" if operation == "write-through" else "index")
    with state.session(workspace) as (after, session):
        assert after == generation
        assert session.generation == old_index_generation
        assert session.member("docs/replaced.md").title == "after replacement"
    assert state.reconcile(workspace, (PAGE,)) == generation
    assert state.write_through(workspace) == generation
