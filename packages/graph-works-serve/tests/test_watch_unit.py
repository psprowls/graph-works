from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
import watchfiles
from graph_works_core.events import Change, ChangeEvent, EventKind
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve import watch
from graph_works_serve.hub import Changes, Hub, Resync

# `workspace` is C5's initialized-workspace fixture (S6).


def test_watch_sets_cover_the_five_config_paths(workspace: WorkspaceLayout) -> None:
    shared, local = workspace.root / "dispatch.yaml", workspace.root / "dispatch.local.yaml"
    sets = watch.watch_sets(workspace, (shared, local))
    assert sets.bundle_dir == workspace.bundle_dir
    assert sets.config_paths == frozenset(
        {workspace.cache_dir / "config.json", workspace.manifest_path, workspace.local_manifest_path, shared, local}
    )
    assert sets.config_dirs == tuple(sorted({workspace.cache_dir, workspace.root}))
    assert workspace.worktrees_dir not in sets.config_dirs


def test_watch_sets_without_dispatch_documents(workspace: WorkspaceLayout) -> None:
    sets = watch.watch_sets(workspace, None)
    assert len(sets.config_paths) == 3 and sets.dispatch_documents is None


def test_bundle_filter_rejects_dot_segments_below_the_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / ".hidden-parent" / "okf"  # a dot *above* the bundle is fine
    keep = watch.bundle_filter(bundle)
    assert keep(watchfiles.Change.modified, str(bundle / "work" / "a.md"))
    assert not keep(watchfiles.Change.modified, str(bundle / ".git" / "HEAD"))
    assert not keep(watchfiles.Change.added, str(bundle / "work" / ".a.md.swp"))
    assert not keep(watchfiles.Change.added, str(tmp_path / "elsewhere.md"))


def test_config_filter_is_exact_membership(workspace: WorkspaceLayout) -> None:
    keep = watch.config_filter(frozenset({workspace.manifest_path}))
    assert keep(watchfiles.Change.modified, str(workspace.manifest_path))
    assert not keep(watchfiles.Change.added, str(workspace.cache_dir / ".config.json.abc.tmp"))


def test_path_exists_treats_oserror_as_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "f").write_text("x", encoding="utf-8", newline="\n")
    assert watch.path_exists(str(tmp_path / "f"))
    assert not watch.path_exists(str(tmp_path / "missing"))

    def boom(self: Path, *args: object, **kwargs: object) -> bool:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "exists", boom)
    assert not watch.path_exists(str(tmp_path / "f"))


async def test_flush_publishes_one_classified_batch(workspace: WorkspaceLayout) -> None:
    hub = Hub()
    sub = hub.subscribe()
    sets = watch.watch_sets(workspace, None)
    item = workspace.bundle_dir / "work" / "a.md"
    raw = {(watchfiles.Change.added, str(item)), (watchfiles.Change.modified, str(item))}
    batch = watch.flush(raw, workspace, sets, hub, exists=lambda _: True)
    expected = (ChangeEvent(EventKind.WORK_ITEM, "work/a", "work/a.md", Change.MODIFIED),)
    assert batch == expected
    assert await sub.get() == Changes(1, expected)


async def test_flush_of_noise_publishes_nothing(workspace: WorkspaceLayout) -> None:
    hub = Hub()
    raw = {(watchfiles.Change.modified, str(workspace.bundle_dir / "not-markdown.bin"))}
    assert watch.flush(raw, workspace, watch.watch_sets(workspace, None), hub, exists=lambda _: True) == ()
    assert hub.seq == 0


def test_needs_rewatch_only_for_manifests() -> None:
    def config(token: str) -> ChangeEvent:
        return ChangeEvent(EventKind.CONFIG, token, None, Change.MODIFIED)

    assert watch.needs_rewatch([config("manifest")])
    assert watch.needs_rewatch([config("manifest-local")])
    assert not watch.needs_rewatch([config("projection"), config("dispatch")])
    assert not watch.needs_rewatch([ChangeEvent(EventKind.LOG, "log.md", "log.md", Change.MODIFIED)])


class FakeAwatch:
    """Scripted awatch, one script per loop (keyed on `recursive`): each call pops one step --
    an exception to raise, or batches to yield -- then waits for `stop_event`. Keying on the
    loop keeps the scripts aligned however the two tasks interleave."""

    Step = list[set[tuple[watchfiles.Change, str]]] | Exception

    def __init__(self, *, bundle: list[Step] | None = None, config: list[Step] | None = None) -> None:
        self.scripts: dict[bool, list[FakeAwatch.Step]] = {True: list(bundle or []), False: list(config or [])}
        self.calls: list[dict[str, object]] = []

    def __call__(self, *paths: Path, **kwargs: object) -> AsyncIterator[set[tuple[watchfiles.Change, str]]]:
        self.calls.append({"paths": paths, **kwargs})
        script = self.scripts[cast(bool, kwargs["recursive"])]
        step = script.pop(0) if script else []
        return self._run(step, cast(asyncio.Event, kwargs["stop_event"]))

    async def _run(self, step: Step, stop: asyncio.Event) -> AsyncIterator[set[tuple[watchfiles.Change, str]]]:
        if isinstance(step, Exception):
            raise step
        for batch in step:
            yield batch
        await stop.wait()


async def test_supervisor_passes_the_documented_awatch_arguments(workspace: WorkspaceLayout) -> None:
    fake = FakeAwatch()
    hub = Hub()
    stop = asyncio.Event()
    task = asyncio.create_task(watch.watch_workspace(workspace.root, hub, stop, awatch_fn=fake))
    while len(fake.calls) < 2:
        await asyncio.sleep(0.005)
    stop.set()
    await asyncio.wait_for(task, 5)
    bundle, config = sorted(fake.calls, key=lambda call: call["recursive"], reverse=True)
    assert bundle["paths"] == (workspace.bundle_dir,) and bundle["recursive"] is True
    assert config["recursive"] is False
    assert workspace.worktrees_dir not in config["paths"]
    for call in (bundle, config):
        assert (call["step"], call["debounce"]) == (watch.STEP_MS, watch.DEBOUNCE_MS)


async def test_watcher_error_resyncs_and_restarts_with_backoff(
    workspace: WorkspaceLayout, capsys: pytest.CaptureFixture[str]
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    item = str(workspace.bundle_dir / "work" / "a.md")
    fake = FakeAwatch(
        bundle=[RuntimeError("inotify limit"), RuntimeError("again"), [{(watchfiles.Change.modified, item)}]]
    )
    hub = Hub()
    sub = hub.subscribe()
    stop = asyncio.Event()
    task = asyncio.create_task(watch.watch_workspace(workspace.root, hub, stop, awatch_fn=fake, sleep=fake_sleep))
    messages = [await asyncio.wait_for(sub.get(), 5) for _ in range(3)]
    stop.set()
    await asyncio.wait_for(task, 5)
    assert [type(m).__name__ for m in messages] == ["Resync", "Resync", "Changes"]
    assert all(isinstance(m, Resync) and m.reason == "watcher-error" for m in messages[:2])
    assert sleeps == [1.0, 2.0]
    assert "inotify limit" in capsys.readouterr().err


async def test_manifest_change_that_moves_dispatch_rules_rewatches(
    workspace: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    states = iter(
        [
            (
                workspace,
                watch.watch_sets(workspace, (workspace.root / "dispatch.yaml", workspace.root / "dispatch.local.yaml")),
            ),
            (
                workspace,
                watch.watch_sets(workspace, (workspace.root / "rules.yaml", workspace.root / "rules.local.yaml")),
            ),
        ]
    )
    monkeypatch.setattr(watch, "load_state", lambda root: next(states))
    fake = FakeAwatch(config=[[{(watchfiles.Change.modified, str(workspace.manifest_path))}]])
    hub = Hub()
    sub = hub.subscribe()
    stop = asyncio.Event()
    task = asyncio.create_task(watch.watch_workspace(workspace.root, hub, stop, awatch_fn=fake))
    first = await asyncio.wait_for(sub.get(), 5)
    second = await asyncio.wait_for(sub.get(), 5)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert isinstance(first, Changes) and first.events[0].path == "manifest"
    assert second == Resync(2, "rewatch")
    assert len(fake.calls) >= 4  # both loops were started twice


async def test_unloadable_manifest_keeps_the_old_sets(
    workspace: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from graph_works_core.workspace.errors import WorkspaceError

    initial = (workspace, watch.watch_sets(workspace, None))
    calls = iter([initial])

    def load(root: Path) -> tuple[WorkspaceLayout, watch.WatchSets]:
        try:
            return next(calls)
        except StopIteration:
            raise WorkspaceError("bad manifest") from None

    monkeypatch.setattr(watch, "load_state", load)
    fake = FakeAwatch(config=[[{(watchfiles.Change.modified, str(workspace.manifest_path))}]])
    hub = Hub()
    sub = hub.subscribe()
    stop = asyncio.Event()
    task = asyncio.create_task(watch.watch_workspace(workspace.root, hub, stop, awatch_fn=fake))
    message = await asyncio.wait_for(sub.get(), 5)
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert isinstance(message, Changes) and message.events[0].path == "manifest"
    assert sub.queue_size == 0  # no resync
    assert "bad manifest" in capsys.readouterr().err


async def test_missing_config_dir_is_skipped_with_one_line(
    workspace: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ghost = workspace.root / "nowhere"
    sets = watch.watch_sets(workspace, (ghost / "d.yaml", ghost / "d.local.yaml"))
    monkeypatch.setattr(watch, "load_state", lambda root: (workspace, sets))
    fake = FakeAwatch()
    hub = Hub()
    stop = asyncio.Event()
    task = asyncio.create_task(watch.watch_workspace(workspace.root, hub, stop, awatch_fn=fake))
    while len(fake.calls) < 2:
        await asyncio.sleep(0.005)
    stop.set()
    await asyncio.wait_for(task, 5)
    config = next(call for call in fake.calls if call["recursive"] is False)
    assert ghost not in config["paths"]
    assert capsys.readouterr().err.count(f"not watching {ghost}") == 1


async def test_invalid_dispatch_reference_keeps_previous_watch_sets(
    workspace: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    from graph_works_core.workspace.errors import WorkspaceError

    original = watch.load_dispatch_config
    calls = 0

    def load(layout: WorkspaceLayout) -> object:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise WorkspaceError("invalid dispatch reference")
        return original(layout)

    monkeypatch.setattr(watch, "load_dispatch_config", load)
    fake = FakeAwatch(
        config=[
            [
                {(watchfiles.Change.modified, str(workspace.manifest_path))},
                {(watchfiles.Change.modified, str(workspace.root / "dispatch.yaml"))},
            ]
        ]
    )
    hub = Hub()
    sub = hub.subscribe()
    stop = asyncio.Event()
    task = asyncio.create_task(watch.watch_workspace(workspace.root, hub, stop, awatch_fn=fake))
    try:
        messages = [await asyncio.wait_for(sub.get(), 2) for _ in range(2)]
        assert all(isinstance(message, Changes) for message in messages)
        assert [event.path for message in messages if isinstance(message, Changes) for event in message.events] == [
            "manifest",
            "dispatch",
        ]
    finally:
        stop.set()
        await asyncio.wait_for(task, 2)


async def test_error_backoff_caps_and_resets_after_clean_batch(workspace: WorkspaceLayout) -> None:
    raw = {(watchfiles.Change.modified, str(workspace.bundle_dir / "log.md"))}
    fake = FakeAwatch(bundle=[RuntimeError("retry") for _ in range(7)] + [[raw], RuntimeError("after batch")])
    # End the successful batch's loop with an error to start another iteration.
    original_run = fake._run

    async def run(step: FakeAwatch.Step, stop: asyncio.Event) -> AsyncIterator[watch.RawBatch]:
        if isinstance(step, list) and step:
            for batch in step:
                yield batch
            raise RuntimeError("after clean batch")
        async for batch in original_run(step, stop):
            yield batch

    fake._run = run
    sleeps: list[float] = []
    stop = asyncio.Event()

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 8:
            stop.set()

    hub = Hub()
    sub = hub.subscribe()
    await asyncio.wait_for(watch.watch_workspace(workspace.root, hub, stop, awatch_fn=fake, sleep=sleep), 3)
    assert sleeps == [1, 2, 4, 8, 16, 30, 30, 1]
    messages = [await sub.get() for _ in range(sub.queue_size)]
    assert sum(isinstance(message, Resync) for message in messages) == 8
    assert sum(isinstance(message, Changes) for message in messages) == 1


async def test_initial_bad_dispatch_still_watches_bundle_and_manifest(
    workspace: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    from graph_works_core.workspace.errors import WorkspaceError

    def bad(layout: WorkspaceLayout) -> object:
        raise WorkspaceError("broken dispatch")

    monkeypatch.setattr(watch, "load_dispatch_config", bad)
    fake = FakeAwatch(bundle=[[{(watchfiles.Change.modified, str(workspace.bundle_dir / "log.md"))}]])
    hub = Hub()
    sub = hub.subscribe()
    stop = asyncio.Event()
    task = asyncio.create_task(watch.watch_workspace(workspace.root, hub, stop, awatch_fn=fake))
    try:
        message = await asyncio.wait_for(sub.get(), 2)
        assert isinstance(message, Changes) and message.events[0].kind is EventKind.LOG
        config = next(call for call in fake.calls if call["recursive"] is False)
        assert config["paths"] == (workspace.cache_dir, workspace.root) or set(config["paths"]) == {
            workspace.cache_dir,
            workspace.root,
        }
    finally:
        stop.set()
        await asyncio.wait_for(task, 2)


async def test_real_backoff_stops_promptly(workspace: WorkspaceLayout) -> None:
    fake = FakeAwatch(bundle=[RuntimeError("broken watcher")])
    hub = Hub()
    sub = hub.subscribe()
    stop = asyncio.Event()
    task = asyncio.create_task(watch.watch_workspace(workspace.root, hub, stop, awatch_fn=fake))
    assert await asyncio.wait_for(sub.get(), 2) == Resync(1, "watcher-error")
    stop.set()
    await asyncio.wait_for(task, 0.5)
