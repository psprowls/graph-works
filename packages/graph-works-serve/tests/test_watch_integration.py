"""Exercise the real watchfiles backend over a temporary workspace."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from graph_works_core.events import ChangeEvent, EventKind
from graph_works_core.workspace.dispatch_projection import write_dispatch_projection
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve import watch
from graph_works_serve.hub import Changes, Hub, Resync, Subscription

DEADLINE = 10.0


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
async def running(workspace: WorkspaceLayout) -> AsyncIterator[tuple[Hub, Subscription]]:
    hub = Hub()
    sub = hub.subscribe()
    stop = asyncio.Event()
    task = asyncio.create_task(watch.watch_workspace(workspace.root, hub, stop))
    await asyncio.sleep(0.5)  # let the native watchers arm; not a correctness wait
    try:
        yield hub, sub
    finally:
        stop.set()
        await asyncio.wait_for(task, DEADLINE)


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


async def test_editing_a_work_item_emits_one_event_quickly(
    workspace: WorkspaceLayout, running: tuple[Hub, Subscription]
) -> None:
    _, sub = running
    loop = asyncio.get_running_loop()
    start = loop.time()
    _write(workspace.bundle_dir / "work" / "feature-a.md", "---\ntitle: A\n---\n")
    _, events = await _collect_until(sub, lambda es: any(e.path == "work/feature-a" for e in es), deadline=2.0)
    assert loop.time() - start < 2.0
    assert any(e.kind is EventKind.WORK_ITEM for e in events)


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
    workspace: WorkspaceLayout, running: tuple[Hub, Subscription]
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
    await asyncio.sleep(0.5)  # re-arm
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
