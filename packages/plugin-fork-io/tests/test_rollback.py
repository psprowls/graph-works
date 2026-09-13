from pathlib import Path

import pytest
from helpers import write_skill
from plugin_fork_io import Services, SourceSpec, apply_preview, load_ledger, plan_rollback
from plugin_fork_io.acceptance import plan_accept
from plugin_fork_io.updates import plan_update
from test_updates import forked


def test_rollback_restores_preaccept_local_edits_and_tracking(tmp_path):
    roots, variant = forked(tmp_path)
    local = roots.content / "local-review/SKILL.md"
    local.write_bytes(local.read_bytes() + b"\nUncommitted local note.\n")
    before_bytes = local.read_bytes()
    before_ledger = load_ledger(roots.state, variant)
    incoming = tmp_path / "incoming"
    write_skill(incoming, "review", b"Keep local intent.\n")
    update = plan_update(
        roots, variant, SourceSpec(str(incoming), "local"), selection=None, mappings={}, services=Services.local()
    )
    accepted = plan_accept(
        roots, variant, update.preview_id, review=None, intent=(), resolutions=(), services=Services.local()
    )
    assert apply_preview(roots.state, accepted.preview_id, services=Services.local()).applied
    undo = plan_rollback(roots, variant, services=Services.local())
    assert apply_preview(roots.state, undo.preview_id, services=Services.local()).applied
    assert local.read_bytes() == before_bytes
    assert load_ledger(roots.state, variant).base_digest == before_ledger.base_digest
    assert load_ledger(roots.state, variant).intent == before_ledger.intent
    assert load_ledger(roots.state, variant).files == before_ledger.files
    from plugin_fork_io import read_status

    assert "content.modified" in {f.code for f in read_status(roots, variant, services=Services.local()).findings}


def test_rollback_restores_added_deleted_files_and_monotonic_history_without_journals(tmp_path):
    import shutil

    from plugin_fork_io import Roots, read_status
    from test_acceptance import accept

    roots, variant = forked(tmp_path)
    local = roots.content / "local-review"
    (local / "extra").write_bytes(b"uncommitted")
    incoming = tmp_path / "incoming"
    write_skill(incoming, "review", b"changed upstream\n")
    (incoming / "skills/review/added").write_bytes(b"added")
    update = plan_update(
        roots, variant, SourceSpec(str(incoming), "local"), selection=None, mappings={}, services=Services.local()
    )
    candidate = Path(update.data["candidate_path"])
    (candidate / "local-review/extra").unlink()
    accepted = accept(roots, variant, update)
    assert apply_preview(roots.state, accepted.preview_id, services=Services.local()).applied
    assert not (local / "extra").exists() and (local / "added").exists()
    moved = tmp_path / "moved"
    moved.mkdir()
    shutil.move(roots.state, moved / "state")
    shutil.move(roots.content, moved / "content")
    roots = Roots(moved / "content", moved / "state")
    local = roots.content / "local-review"
    shutil.rmtree(roots.state / "transactions")
    shutil.rmtree(roots.state / "previews")
    undo = plan_rollback(roots, variant, services=Services.local())
    assert undo.allowed, undo
    result = apply_preview(roots.state, undo.preview_id, services=Services.local())
    assert result.applied, result
    assert (local / "extra").read_bytes() == b"uncommitted" and not (local / "added").exists()
    assert load_ledger(roots.state, variant).generation == 3
    assert len(load_ledger(roots.state, variant).history) == 3
    assert read_status(roots, variant, services=Services.local()).allowed
    assert not plan_rollback(roots, variant, services=Services.local()).allowed


def test_rollback_refuses_local_edits(tmp_path):
    from test_acceptance import accept, prepared

    roots, variant, update = prepared(tmp_path)
    accepted = accept(roots, variant, update)
    assert apply_preview(roots.state, accepted.preview_id, services=Services.local()).applied
    (roots.content / "local-review/extra").write_bytes(b"new human work")
    assert not plan_rollback(roots, variant, services=Services.local()).allowed


@pytest.mark.parametrize("target", ["SKILL.md", "base.tar.gz", "ledger.json"])
@pytest.mark.parametrize("when", ["before", "after"])
def test_injected_accept_failure_recovers_preaccept_content_and_tracking(tmp_path, target, when):
    from dataclasses import replace

    from plugin_fork_io import read_status
    from test_acceptance import accept, prepared
    from test_transactions import FailingFS

    roots, variant, update = prepared(tmp_path)
    before = (roots.content / "local-review/SKILL.md").read_bytes()
    ledger = load_ledger(roots.state, variant)
    accepted = accept(roots, variant, update)
    failed = apply_preview(
        roots.state, accepted.preview_id, services=replace(Services.local(), filesystem=FailingFS(target, when=when))
    )
    assert not failed.applied, failed
    undo = plan_rollback(roots, variant, services=Services.local())
    assert undo.allowed, undo
    restored = apply_preview(roots.state, undo.preview_id, services=Services.local())
    assert restored.applied, restored
    assert (roots.content / "local-review/SKILL.md").read_bytes() == before
    assert load_ledger(roots.state, variant) == ledger
    assert read_status(roots, variant, services=Services.local()).allowed


@pytest.mark.parametrize("kind", ["file-to-directory", "directory-to-file", "directory-delete", "directory-mode"])
def test_accept_and_rollback_restore_kinds_and_directory_modes(tmp_path, kind):
    from plugin_fork_io.store import inventory
    from test_acceptance import accept

    roots, variant = forked(tmp_path)
    local = roots.content / "local-review/item"
    if kind == "file-to-directory":
        local.write_bytes(b"original")
    else:
        local.mkdir()
        (local / "child").write_bytes(b"original")
    before = inventory(roots.content, ("local-review",), services=Services.local()).entries
    update = plan_update(
        roots,
        variant,
        SourceSpec(str(tmp_path / "source"), "local"),
        selection=None,
        mappings={},
        services=Services.local(),
    )
    staged = Path(update.data["candidate_path"]) / "local-review/item"
    if kind == "file-to-directory":
        staged.unlink()
        staged.mkdir()
        (staged / "child").write_bytes(b"new")
    elif kind == "directory-mode":
        Services.local().filesystem.chmod(staged, 0o700)
    else:
        (staged / "child").unlink()
        staged.rmdir()
        if kind == "directory-to-file":
            staged.write_bytes(b"replacement")
    accepted = accept(roots, variant, update)
    assert accepted.allowed, accepted
    result = apply_preview(roots.state, accepted.preview_id, services=Services.local())
    assert result.applied, result
    undo = plan_rollback(roots, variant, services=Services.local())
    assert undo.allowed, undo
    result = apply_preview(roots.state, undo.preview_id, services=Services.local())
    assert result.applied, result
    assert inventory(roots.content, ("local-review",), services=Services.local()).entries == before


@pytest.mark.parametrize("kind", ["file-to-directory", "directory-to-file", "directory-delete"])
@pytest.mark.parametrize("target", ["SKILL.md", "base.tar.gz", "ledger.json"])
def test_kind_changes_recover_after_late_failure(tmp_path, kind, target):
    from dataclasses import replace

    from plugin_fork_io.store import inventory
    from test_acceptance import accept
    from test_transactions import FailingFS

    roots, variant = forked(tmp_path)
    local = roots.content / "local-review/item"
    if kind == "file-to-directory":
        local.write_bytes(b"original")
    else:
        local.mkdir()
        (local / "child").write_bytes(b"original")
    before = inventory(roots.content, ("local-review",), services=Services.local()).entries
    update = plan_update(
        roots,
        variant,
        SourceSpec(str(tmp_path / "source"), "local"),
        selection=None,
        mappings={},
        services=Services.local(),
    )
    staged = Path(update.data["candidate_path"]) / "local-review/item"
    if kind == "file-to-directory":
        staged.unlink()
        staged.mkdir()
        (staged / "child").write_bytes(b"new")
    else:
        (staged / "child").unlink()
        staged.rmdir()
        if kind == "directory-to-file":
            staged.write_bytes(b"replacement")
    accepted = accept(roots, variant, update)
    assert accepted.allowed, accepted
    result = apply_preview(
        roots.state, accepted.preview_id, services=replace(Services.local(), filesystem=FailingFS(target, when="after"))
    )
    assert not result.applied, result
    undo = plan_rollback(roots, variant, services=Services.local())
    assert undo.allowed, undo
    result = apply_preview(roots.state, undo.preview_id, services=Services.local())
    assert result.applied, result
    assert inventory(roots.content, ("local-review",), services=Services.local()).entries == before


def test_rollback_restores_preaccept_local_link(tmp_path):
    from test_acceptance import accept

    roots, variant = forked(tmp_path)
    local = roots.content / "local-review/link"
    Services.local().filesystem.link("SKILL.md", local)
    update = plan_update(
        roots,
        variant,
        SourceSpec(str(tmp_path / "source"), "local"),
        selection=None,
        mappings={},
        services=Services.local(),
    )
    (Path(update.data["candidate_path"]) / "local-review/link").unlink()
    accepted = accept(roots, variant, update)
    assert accepted.allowed, accepted
    assert apply_preview(roots.state, accepted.preview_id, services=Services.local()).applied
    undo = plan_rollback(roots, variant, services=Services.local())
    assert undo.allowed, undo
    result = apply_preview(roots.state, undo.preview_id, services=Services.local())
    assert result.applied, result
    assert Services.local().filesystem.readlink(local) == "SKILL.md"


def test_recovery_preserves_replaced_directory_during_mode_change(tmp_path):
    import shutil
    from dataclasses import replace

    from plugin_fork_io.machine import LocalFileSystem
    from test_acceptance import accept

    roots, variant = forked(tmp_path)
    folder = roots.content / "local-review/item"
    folder.mkdir()
    (folder / "child").write_bytes(b"before")
    update = plan_update(
        roots,
        variant,
        SourceSpec(str(tmp_path / "source"), "local"),
        selection=None,
        mappings={},
        services=Services.local(),
    )
    Services.local().filesystem.chmod(Path(update.data["candidate_path"]) / "local-review/item", 0o700)
    accepted = accept(roots, variant, update)

    class ReplacingFS(LocalFileSystem):
        def replace(self, source, destination, **kwargs):
            super().replace(source, destination, **kwargs)
            if destination.name == "SKILL.md":
                replacement = folder.with_name("replaced")
                shutil.copytree(folder, replacement)
                shutil.rmtree(folder)
                replacement.rename(folder)

    result = apply_preview(
        roots.state, accepted.preview_id, services=replace(Services.local(), filesystem=ReplacingFS())
    )
    assert not result.applied
    undo = plan_rollback(roots, variant, services=Services.local())
    assert not undo.allowed
