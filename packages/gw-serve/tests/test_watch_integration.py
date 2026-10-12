"""Exercise the real watchfiles polling backend over a temporary workspace."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import pytest
from config_io import PROJECTION_FILENAME
from gw_kernel.events import ChangeEvent, EventKind
from gw_kernel.workspace.dispatch_projection import write_dispatch_projection
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_serve import watch
from gw_serve.hub import Changes, Hub, Resync, Subscription
from gw_serve.readstate import ReadState
from watcher_helpers import PollingBackend
from watcher_helpers import write as _write

# Polling is deterministic but the gate runs many suites at once; these are
# failure bounds, not latency promises, and passing runs return early.
DEADLINE = 30.0
READINESS_DEADLINE = 60.0


async def _ready(sub: Subscription, backend: PollingBackend) -> None:
    await asyncio.wait_for(backend.wait_ready(), READINESS_DEADLINE)
    # Both backend iterators have processed startup batches and completed an
    # idle timeout. Drain only these setup messages before test edits begin.
    while sub.queue_size:
        message = await sub.get()
        assert isinstance(message, Changes), message


@asynccontextmanager
async def _running(
    workspace: WorkspaceLayout, backend: PollingBackend | None = None
) -> AsyncIterator[tuple[Hub, Subscription]]:
    # Give atomic replacement a distinct polling timestamp without touching
    # the replacement afterward: that test must observe the replace itself.
    projection = workspace.cache_dir / PROJECTION_FILENAME
    stat = projection.stat()
    os.utime(projection, ns=(stat.st_atime_ns, stat.st_mtime_ns - 2_000_000_000))
    hub = Hub()
    sub = hub.subscribe()
    stop = asyncio.Event()
    backend = PollingBackend() if backend is None else backend
    task = asyncio.create_task(watch.watch_workspace(workspace.root, hub, ReadState(), stop, awatch_fn=backend.awatch))
    try:
        await _ready(sub, backend)
        yield hub, sub
    finally:
        stop.set()
        await asyncio.wait_for(task, DEADLINE)


@pytest.fixture
def polling_backend() -> PollingBackend:
    return PollingBackend()


@pytest.fixture
async def running(
    workspace: WorkspaceLayout, polling_backend: PollingBackend
) -> AsyncIterator[tuple[Hub, Subscription]]:
    async with _running(workspace, polling_backend) as pair:
        yield pair


async def _collect_until(
    sub: Subscription, done: Callable[[list[ChangeEvent]], bool], *, deadline: float = DEADLINE
) -> tuple[list[Changes | Resync], list[ChangeEvent]]:
    messages: list[Changes | Resync] = []
    events: list[ChangeEvent] = []

    async def pump() -> None:
        while not done(events):
            message = await sub.get()
            assert message is not None
            messages.append(message)
            if isinstance(message, Changes):
                events.extend(message.events)

    await asyncio.wait_for(pump(), deadline)
    return messages, events


async def test_editing_a_work_item_emits_one_event(
    workspace: WorkspaceLayout, running: tuple[Hub, Subscription]
) -> None:
    # Delivery latency is the backend's (300ms poll plus the 800ms debounce, and
    # whatever load the machine is under), so this asserts the single event, not
    # a wall-clock bound.
    _, sub = running
    _write(workspace.bundle_dir / "work" / "feature-a.md", "---\ntitle: A\n---\n")
    _, events = await _collect_until(sub, lambda es: any(e.path == "work/feature-a" for e in es))
    assert [(e.kind, e.path) for e in events if e.path.startswith("work/")] == [(EventKind.WORK_ITEM, "work/feature-a")]


async def test_a_burst_coalesces_into_at_most_two_batches(
    workspace: WorkspaceLayout, running: tuple[Hub, Subscription]
) -> None:
    _, sub = running
    names = ["feature-x", "feature-y", "feature-z"]
    for i in range(50):
        _write(workspace.bundle_dir / "work" / f"{names[i % 3]}.md", f"---\ntitle: {i}\n---\n")
    messages, _events = await _collect_until(sub, lambda es: {f"work/{n}" for n in names} <= {e.path for e in es})
    assert len([m for m in messages if isinstance(m, Changes)]) <= 2


async def test_config_projection_replace_is_a_config_event(
    workspace: WorkspaceLayout, running: tuple[Hub, Subscription]
) -> None:
    _, sub = running
    write_dispatch_projection(workspace)
    _, events = await _collect_until(
        sub, lambda es: any(e.kind is EventKind.CONFIG and e.path == "projection" for e in es)
    )
    assert all(e.member is None for e in events if e.kind is EventKind.CONFIG)


async def test_hand_edit_of_workspace_yaml_is_a_manifest_event(
    workspace: WorkspaceLayout, running: tuple[Hub, Subscription]
) -> None:
    _, sub = running
    text = workspace.manifest_path.read_text(encoding="utf-8")
    _write(workspace.manifest_path, text + "\n# touched\n")
    await _collect_until(sub, lambda es: any(e.kind is EventKind.CONFIG and e.path == "manifest" for e in es))


async def test_traces_and_worktrees_never_emit(workspace: WorkspaceLayout, running: tuple[Hub, Subscription]) -> None:
    _, sub = running
    _write(workspace.cache_dir / "traces" / "t.json", "{}")
    _write(workspace.worktrees_dir / "gw" / "x" / "README.md", "x")
    _write(workspace.bundle_dir / "log.md", "# log\n")  # a sentinel that must arrive
    _messages, events = await _collect_until(sub, lambda es: any(e.kind is EventKind.LOG for e in es))
    assert all(e.kind is EventKind.LOG for e in events), events


async def test_moving_dispatch_rules_rewatches_and_the_new_file_classifies(
    workspace: WorkspaceLayout, running: tuple[Hub, Subscription], polling_backend: PollingBackend
) -> None:
    _, sub = running
    old = workspace.root / "dispatch.yaml"
    new = workspace.root / "rules" / "dispatch.yaml"
    _write(new, old.read_text(encoding="utf-8"))
    manifest = workspace.manifest_path.read_text(encoding="utf-8").replace(
        "dispatch_rules: dispatch.yaml", "dispatch_rules: rules/dispatch.yaml"
    )
    _write(workspace.manifest_path, manifest)

    async def until_rewatch() -> None:
        while True:
            message = await sub.get()
            if isinstance(message, Resync) and message.reason == "rewatch":
                return

    await asyncio.wait_for(until_rewatch(), DEADLINE)
    await _ready(sub, polling_backend)
    _write(new, new.read_text(encoding="utf-8") + "\n# edited\n")
    await _collect_until(sub, lambda es: any(e.kind is EventKind.CONFIG and e.path == "dispatch" for e in es))


@pytest.mark.parametrize(
    ("filename", "token"),
    [
        ("workspace.local.yaml", "manifest-local"),
        ("dispatch.yaml", "dispatch"),
        ("dispatch.local.yaml", "dispatch-local"),
    ],
)
async def test_local_manifest_and_dispatch_edits_emit_config(
    workspace: WorkspaceLayout, running: tuple[Hub, Subscription], filename: str, token: str
) -> None:
    _, sub = running
    path = workspace.root / filename
    text = path.read_text(encoding="utf-8") if path.exists() else "{}\n"
    _write(path, text + "\n# edited\n")
    await _collect_until(sub, lambda es: any(e.kind is EventKind.CONFIG and e.path == token for e in es))


async def test_existing_clone_edits_and_deletions_never_emit(workspace: WorkspaceLayout) -> None:
    clone = workspace.bundle_dir / "repositories/example/references/git"
    readme = clone / "README.md"
    code = clone / "src/code.py"
    _write(readme, "before")
    _write(code, "before")
    async with _running(workspace) as (hub, sub):
        _write(readme, "after")
        _write(code, "after")
        _write(workspace.bundle_dir / "repositories/example/references/notes.md", "notes")
        _write(workspace.bundle_dir / "log.md", "sentinel")
        _, events = await _collect_until(
            sub,
            lambda es: {"log.md", "repositories/example/references/notes.md"} <= {e.path for e in es},
        )

        # Drain late batches after the positive sentinel, not just its first batch.
        async def drain() -> None:
            while True:
                message = await sub.get()
                assert isinstance(message, Changes)
                events.extend(message.events)

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(drain(), 1.2)
        assert {e.path for e in events} == {"log.md", "repositories/example/references/notes.md"}
        before = hub.seq
        readme.unlink()
        code.unlink()
        (clone / "src").rmdir()
        clone.rmdir()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(sub.get(), 1.2)
        assert hub.seq == before
