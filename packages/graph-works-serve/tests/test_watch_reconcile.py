from __future__ import annotations

import asyncio

import watchfiles
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve import watch
from graph_works_serve.hub import Hub
from graph_works_serve.readstate import ReadState
from readstate_fakes import FakeOpener, state_for


async def test_get_after_frame_sees_edit(workspace: WorkspaceLayout) -> None:
    state = ReadState()
    with state.session(workspace) as (before, session):
        assert session.member("docs/new.md") is None
    page = workspace.bundle_dir / "docs/new.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\ntitle: new\n---\n", encoding="utf-8", newline="\n")
    hub = Hub(lambda: state.generation)
    sub = hub.subscribe()
    await watch.flush(
        {(watchfiles.Change.added, str(page))}, workspace, watch.watch_sets(workspace, None), hub, state, asyncio.Lock()
    )
    frame = await sub.get()
    assert frame.generation > before
    with state.session(workspace) as (after, session):
        assert after >= frame.generation
        assert session.member("docs/new.md").title == "new"


async def test_empty_batch_does_no_index_work(workspace: WorkspaceLayout) -> None:
    opener = FakeOpener()
    hub = Hub()
    assert (
        await watch.flush(set(), workspace, watch.watch_sets(workspace, None), hub, state_for(opener), asyncio.Lock())
        == ()
    )
    assert opener.calls == [] and hub.seq == 0


async def test_two_loops_publish_nondecreasing_generations(workspace: WorkspaceLayout) -> None:
    state = state_for(FakeOpener())
    hub = Hub(lambda: state.generation)
    sub = hub.subscribe()
    lock = asyncio.Lock()
    sets = watch.watch_sets(workspace, None)
    raws = [
        {(watchfiles.Change.modified, str(path))}
        for path in (workspace.manifest_path, workspace.bundle_dir / "docs/p.md")
    ]
    await asyncio.gather(
        *(watch.flush(raw, workspace, sets, hub, state, lock, exists=lambda _: True) for raw in raws * 3)
    )
    generations = [(await sub.get()).generation for _ in range(6)]
    assert generations == sorted(generations)
    assert generations[-1] > generations[0]


async def test_queued_old_layout_flush_cannot_replace_new_identity(workspace: WorkspaceLayout) -> None:
    old_sets = watch.watch_sets(workspace, None)
    new_bundle = workspace.root / "new-bundle"
    new_bundle.mkdir()
    (new_bundle / "latest.md").write_text("---\ntitle: latest\n---\n", encoding="utf-8", newline="\n")
    state = ReadState()
    with state.session(workspace):
        pass
    hub = Hub(lambda: state.generation)
    sub = hub.subscribe()
    lock = asyncio.Lock()
    await lock.acquire()
    queued = asyncio.create_task(
        watch.flush(
            {(watchfiles.Change.modified, str(workspace.bundle_dir / "docs/p.md"))},
            workspace,
            old_sets,
            hub,
            state,
            lock,
            exists=lambda _: True,
        )
    )
    await asyncio.sleep(0)
    # Resolve has observed a valid manifest move before the queued flush resumes.
    manifest = workspace.manifest_path
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "\nlayout:\n  bundle_dir: new-bundle\n  cache_dir: new-cache\n",
        encoding="utf-8",
        newline="\n",
    )
    new = watch.resolve(workspace=workspace.root)
    assert new.bundle_dir == new_bundle and new.cache_dir == workspace.root / "new-cache"
    with state.session(new) as (new_generation, session):
        assert session.member("latest.md").title == "latest"
    lock.release()
    await asyncio.wait_for(queued, 5)
    frame = await sub.get()
    assert frame.generation == new_generation
    with state.session(new) as (generation, session):
        assert generation == new_generation and session.member("latest.md").title == "latest"
    assert [reason for _, reason in state.bumps] == ["identity"]
