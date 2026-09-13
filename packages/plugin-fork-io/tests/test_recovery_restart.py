from pathlib import Path

import pytest
from plugin_fork_io import Services, SourceSpec, apply_preview, load_ledger, plan_rollback
from plugin_fork_io.updates import plan_update
from test_updates import forked


@pytest.mark.parametrize("kind", ["file-to-directory", "directory-to-file", "directory-delete"])
@pytest.mark.parametrize(
    "interruption",
    ["journal-before", "journal-after", "restore-before", "restore-after", "prepare-before", "prepare-after"],
)
def test_recovery_can_resume_its_own_kind_transitions(tmp_path, kind, interruption):
    from dataclasses import replace

    from plugin_fork_io.store import inventory
    from test_transactions import FailingFS

    roots, variant, before_ledger, before, undo, tracking_before = interrupted_transition(tmp_path, kind)

    class RecoveryFailure(FailingFS):
        def __getattr__(self, name):
            original = super().__getattr__(name)
            if name != "replace" or not interruption.startswith("journal"):
                return original

            def replace_file(source, destination, **kwargs):
                # Fail the final phase write, not the preparatory identity record.
                if destination.name == "journal.json" and b'"rolled_back"' in source.read_bytes():
                    if interruption.endswith("after"):
                        original(source, destination, **kwargs)
                    raise OSError("Interrupted final recovery persistence")
                return original(source, destination, **kwargs)

            return replace_file

    if interruption.startswith("journal"):
        fault = RecoveryFailure(".never.")
    elif interruption.startswith("prepare"):
        fault = FailingFS("journal.json", when=interruption.split("-")[1])
    else:
        fault = FailingFS(
            "item",
            method="replace" if kind == "file-to-directory" else "rename_directory",
            when=interruption.split("-")[1],
        )
    result = apply_preview(roots.state, undo.preview_id, services=replace(Services.local(), filesystem=fault))
    if (
        interruption.startswith("prepare") and kind == "file-to-directory" and interruption.endswith("after")
    ) or interruption == "journal-after":
        assert not result.applied
    else:
        assert not result.applied, result
        retry = plan_rollback(roots, variant, services=Services.local())
        assert retry.allowed, retry
        restored = apply_preview(roots.state, retry.preview_id, services=Services.local())
        assert restored.applied, restored
    from plugin_fork_io.transactions import incomplete

    assert not incomplete(roots.state)
    assert load_ledger(roots.state, variant) == before_ledger
    assert {
        p.relative_to(roots.state / "forks"): p.read_bytes() for p in (roots.state / "forks").rglob("*") if p.is_file()
    } == tracking_before
    assert inventory(roots.content, ("local-review",), services=Services.local()).entries == before


def interrupted_transition(tmp_path, kind):
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
    before_ledger = load_ledger(roots.state, variant)
    tracking_before = {
        p.relative_to(roots.state / "forks"): p.read_bytes() for p in (roots.state / "forks").rglob("*") if p.is_file()
    }
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
        roots.state,
        accepted.preview_id,
        services=replace(Services.local(), filesystem=FailingFS("ledger.json", when="after")),
    )
    assert not result.applied, result
    undo = plan_rollback(roots, variant, services=Services.local())
    assert undo.allowed, undo
    return roots, variant, before_ledger, before, undo, tracking_before


@pytest.mark.parametrize("place", ["staged", "restored"])
def test_recovery_refuses_identical_foreign_directory_replacement(tmp_path, place):
    from dataclasses import replace

    from test_transactions import FailingFS

    roots, variant, _, _, undo, _ = interrupted_transition(tmp_path, "directory-to-file")
    fault = FailingFS("item", method="rename_directory", when="before" if place == "staged" else "after")
    result = apply_preview(roots.state, undo.preview_id, services=replace(Services.local(), filesystem=fault))
    assert not result.applied and fault.failed
    target = (
        next(roots.content.glob(".plugin-fork-recovery-*.tmp"))
        if place == "staged"
        else roots.content / "local-review/item"
    )
    target.rename(target.with_name(target.name + ".original"))
    target.mkdir()
    foreign_identity = Services.local().filesystem.identity(target)
    retry = plan_rollback(roots, variant, services=Services.local())
    assert not retry.allowed
    assert any(f.code == "recovery.ambiguous" for f in retry.findings)
    assert Services.local().filesystem.identity(target) == foreign_identity


def test_recovery_preserves_unknown_child_added_during_directory_promotion(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.machine import LocalFileSystem

    roots, variant, _, _, undo, _ = interrupted_transition(tmp_path, "directory-to-file")

    class EditingFS(LocalFileSystem):
        def rename_directory(self, source, destination):
            super().rename_directory(source, destination)
            (destination / "human-note").write_bytes(b"human work")

    result = apply_preview(roots.state, undo.preview_id, services=replace(Services.local(), filesystem=EditingFS()))
    assert not result.applied
    assert (roots.content / "local-review/item/human-note").read_bytes() == b"human work"
    assert not plan_rollback(roots, variant, services=Services.local()).allowed


@pytest.mark.parametrize("when", ["before", "after"])
def test_recovery_creation_durability_failure_can_retry(tmp_path, when):
    from dataclasses import replace

    from test_transactions import FailingFS

    roots, variant, before_ledger, _, undo, _ = interrupted_transition(tmp_path, "directory-delete")
    fault = FailingFS(".plugin-fork-recovery-", method="sync_directory", when=when)
    result = apply_preview(roots.state, undo.preview_id, services=replace(Services.local(), filesystem=fault))
    assert not result.applied and fault.failed
    retry = plan_rollback(roots, variant, services=Services.local())
    assert retry.allowed, retry
    assert apply_preview(roots.state, retry.preview_id, services=Services.local()).applied
    assert load_ledger(roots.state, variant) == before_ledger
    assert (roots.content / "local-review/item/child").read_bytes() == b"original"
