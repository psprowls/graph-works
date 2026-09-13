import pytest
from helpers import codes, selection, write_skill
from plugin_fork_io import Roots, Services, SourceSpec, plan_fork
from plugin_fork_io.transactions import apply_preview


def test_fork_apply_refuses_new_destination_without_losing_it(tmp_path):
    source = tmp_path / "source"
    write_skill(source, "review")
    roots = Roots(tmp_path / "content", tmp_path / "state")
    prepared = plan_fork(
        SourceSpec(str(source), "local"), selection("review"), roots, intent=(), services=Services.local()
    )
    target = roots.content / "review/SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"human work")
    result = apply_preview(roots.state, prepared.preview_id, services=Services.local())
    assert "preview.stale" in codes(result)
    assert target.read_bytes() == b"human work"
    assert not result.applied


def prepared_fork(tmp_path, body=b"Keep local intent.\n"):
    source = tmp_path / "source"
    write_skill(source, "review", body)
    roots = Roots(tmp_path / "content", tmp_path / "state")
    prepared = plan_fork(
        SourceSpec(str(source), "local"),
        selection("review"),
        roots,
        intent=("Retain my workflow",),
        services=Services.local(),
    )
    return roots, prepared


def test_apply_persists_original_base_and_typed_ledger_and_consumes(tmp_path):
    from plugin_fork_io.snapshots import read_snapshot
    from plugin_fork_io.store import load_ledger

    roots, prepared = prepared_fork(tmp_path)
    result = apply_preview(roots.state, prepared.preview_id, services=Services.local())
    assert result.applied, result
    ledger = load_ledger(roots.state, prepared.variant_id)
    assert ledger.generation == 1
    assert ledger.intent == ("Retain my workflow",)
    assert read_snapshot(roots.state / "forks" / prepared.variant_id / "base.tar.gz").digest == ledger.base_digest
    assert "preview.consumed" in codes(apply_preview(roots.state, prepared.preview_id, services=Services.local()))


def test_candidate_and_metadata_tampering_refused(tmp_path):
    roots, prepared = prepared_fork(tmp_path)
    candidate = roots.state / "previews" / prepared.preview_id / "candidate/review/SKILL.md"
    candidate.write_bytes(b"tampered")
    assert "preview.invalid" in codes(apply_preview(roots.state, prepared.preview_id, services=Services.local()))
    assert not roots.content.exists()


def test_metadata_tampering_refused(tmp_path):
    roots, prepared = prepared_fork(tmp_path)
    path = roots.state / "previews" / prepared.preview_id / "preview.json"
    path.write_bytes(path.read_bytes().replace(b"Retain my workflow", b"Changed intent"))
    assert "preview.invalid" in codes(apply_preview(roots.state, prepared.preview_id, services=Services.local()))


def test_stale_lock_is_not_stolen(tmp_path):
    roots, prepared = prepared_fork(tmp_path)
    lock = roots.state / "locks/ownership.lock"
    lock.parent.mkdir()
    lock.write_bytes(b"stale holder")
    assert "lock.present" in codes(apply_preview(roots.state, prepared.preview_id, services=Services.local()))
    assert lock.read_bytes() == b"stale holder"
    assert not roots.content.exists()


def test_ancestor_replacement_refused(tmp_path):
    content = tmp_path / "content"
    content.mkdir()
    roots, prepared = prepared_fork(tmp_path)
    content.rename(tmp_path / "old-content")
    content.mkdir()
    result = apply_preview(roots.state, prepared.preview_id, services=Services.local())
    assert "preview.stale" in codes(result)


def test_nested_unexpected_link_preserved(tmp_path):
    roots, prepared = prepared_fork(tmp_path)
    (roots.content / "review").mkdir(parents=True)
    link = roots.content / "review/elsewhere"
    link.symlink_to(tmp_path / "source")
    result = apply_preview(roots.state, prepared.preview_id, services=Services.local())
    assert "preview.stale" in codes(result)
    assert link.is_symlink()


def test_blocked_candidate_stays_refused(tmp_path):
    roots, prepared = prepared_fork(tmp_path, b"Requires $absent.\n")
    assert not prepared.allowed
    result = apply_preview(roots.state, prepared.preview_id, services=Services.local())
    assert "preview.blocked" in codes(result)
    assert not roots.content.exists()


class FailingFS:
    def __init__(self, target, method="replace", when="before"):
        from plugin_fork_io.machine import LocalFileSystem

        self.inner = LocalFileSystem()
        self.target = target
        self.method = method
        self.when = when
        self.failed = False

    def __getattr__(self, name):
        operation = getattr(self.inner, name)

        def call(*args, **kwargs):
            hit = name == self.method and not self.failed and any(self.target in str(a) for a in args)
            if hit and self.when == "before":
                self.failed = True
                raise OSError("injected failure")
            value = operation(*args, **kwargs)
            if hit and self.when == "after":
                self.failed = True
                raise OSError("injected failure")
            return value

        return call


def test_crash_after_replace_before_progress_recovers_and_retains_evidence(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.recovery import plan_rollback

    roots, prepared = prepared_fork(tmp_path)
    fs = FailingFS("SKILL.md", when="after")
    result = apply_preview(roots.state, prepared.preview_id, services=replace(Services.local(), filesystem=fs))
    assert "transaction.io" in codes(result)
    assert (roots.content / "review/SKILL.md").exists()
    assert "transaction.incomplete" in codes(apply_preview(roots.state, prepared.preview_id, services=Services.local()))
    recovery = plan_rollback(roots, prepared.variant_id, services=Services.local())
    assert recovery.allowed, recovery
    result = apply_preview(roots.state, recovery.preview_id, services=Services.local())
    assert result.applied, result
    assert not roots.content.exists()
    assert (roots.state / "transactions" / prepared.preview_id / "journal.json").exists()


def test_recovery_preserves_unknown_edits(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.recovery import plan_rollback

    roots, prepared = prepared_fork(tmp_path)
    apply_preview(
        roots.state,
        prepared.preview_id,
        services=replace(Services.local(), filesystem=FailingFS("SKILL.md", when="after")),
    )
    target = roots.content / "review/SKILL.md"
    target.write_bytes(b"human edit after failure")
    result = plan_rollback(roots, prepared.variant_id, services=Services.local())
    assert "recovery.ambiguous" in codes(result)
    assert target.read_bytes() == b"human edit after failure"


def test_recovery_preserves_new_local_files(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.recovery import plan_rollback

    roots, prepared = prepared_fork(tmp_path)
    apply_preview(
        roots.state,
        prepared.preview_id,
        services=replace(Services.local(), filesystem=FailingFS("SKILL.md", when="after")),
    )
    target = roots.content / "review/human.txt"
    target.write_bytes(b"human new file")
    assert "recovery.ambiguous" in codes(plan_rollback(roots, prepared.variant_id, services=Services.local()))
    assert target.exists()


def test_race_at_replacement_does_not_overwrite_new_file(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.machine import LocalFileSystem

    class RacingFS(LocalFileSystem):
        def replace(self, source, destination, **kwargs):
            if destination.name == "SKILL.md":
                destination.write_bytes(b"concurrent human work")
            return super().replace(source, destination, **kwargs)

    roots, prepared = prepared_fork(tmp_path)
    result = apply_preview(roots.state, prepared.preview_id, services=replace(Services.local(), filesystem=RacingFS()))
    assert not result.applied
    assert (roots.content / "review/SKILL.md").read_bytes() == b"concurrent human work"


def test_new_symlink_at_tracking_ancestor_refused(tmp_path):
    roots, prepared = prepared_fork(tmp_path)
    old = roots.state.with_name("original-state")
    roots.state.rename(old)
    roots.state.symlink_to(old, target_is_directory=True)
    result = apply_preview(roots.state, prepared.preview_id, services=Services.local())
    assert not result.applied
    assert not roots.content.exists()


@pytest.mark.parametrize("boundary", ["SKILL.md", "base.tar.gz", "ledger.json", "journal.json"])
@pytest.mark.parametrize("method", ["write_exclusive", "replace", "sync_file"])
@pytest.mark.parametrize("when", ["before", "after"])
def test_fault_boundaries_recover_without_loss(tmp_path, boundary, method, when):
    from dataclasses import replace

    from plugin_fork_io.recovery import plan_rollback
    from plugin_fork_io.transactions import incomplete, journal_path, load_journal

    roots, prepared = prepared_fork(tmp_path)
    fs = FailingFS(boundary, method, when)
    result = apply_preview(roots.state, prepared.preview_id, services=replace(Services.local(), filesystem=fs))
    if not fs.failed:
        assert result.applied
        return
    assert not result.applied
    path = journal_path(roots.state, prepared.preview_id)
    if not path.exists():
        assert not roots.content.exists()
        return
    if load_journal(roots.state, prepared.preview_id).phase == "complete":
        assert not incomplete(roots.state)
        return
    recovery = plan_rollback(roots, prepared.variant_id, services=Services.local())
    assert recovery.allowed, recovery
    recovered = apply_preview(roots.state, recovery.preview_id, services=Services.local())
    assert recovered.applied, recovered
    assert not (roots.content / "review").exists()
    assert not (roots.state / "forks" / prepared.variant_id).exists()


def test_concurrent_attempts_use_exclusive_ownership_lock(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from threading import Event

    from plugin_fork_io.machine import LocalFileSystem

    locked, release = Event(), Event()

    class PausingFS(LocalFileSystem):
        def write_exclusive(self, path, content):
            super().write_exclusive(path, content)
            if path.name == "ownership.lock":
                locked.set()
                assert release.wait(10)

    roots, prepared = prepared_fork(tmp_path)
    with ThreadPoolExecutor() as pool:
        first = pool.submit(
            apply_preview, roots.state, prepared.preview_id, services=replace(Services.local(), filesystem=PausingFS())
        )
        assert locked.wait(10)
        second = apply_preview(roots.state, prepared.preview_id, services=Services.local())
        release.set()
        assert "lock.present" in codes(second)
        assert first.result().applied


def test_revalidation_occurs_after_lock_acquisition(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.machine import LocalFileSystem

    roots, prepared = prepared_fork(tmp_path)

    class EditingFS(LocalFileSystem):
        def write_exclusive(self, path, content):
            super().write_exclusive(path, content)
            if path.name == "ownership.lock":
                (roots.content / "review").mkdir(parents=True)
                (roots.content / "review/human").write_bytes(b"new")

    result = apply_preview(roots.state, prepared.preview_id, services=replace(Services.local(), filesystem=EditingFS()))
    assert "preview.stale" in codes(result)


def test_recovery_preview_stales_on_new_edits(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.recovery import plan_rollback

    roots, prepared = prepared_fork(tmp_path)
    apply_preview(
        roots.state,
        prepared.preview_id,
        services=replace(Services.local(), filesystem=FailingFS("SKILL.md", when="after")),
    )
    recovery = plan_rollback(roots, prepared.variant_id, services=Services.local())
    (roots.content / "review/SKILL.md").write_bytes(b"new edit")
    result = apply_preview(roots.state, recovery.preview_id, services=Services.local())
    assert "recovery.ambiguous" in codes(result)


def test_links_and_absent_promotion_use_injected_machine(tmp_path):
    from plugin_fork_io.machine import LocalFileSystem
    from plugin_fork_io.records import Change, SnapshotEntry
    from plugin_fork_io.transactions import promote

    fs = LocalFileSystem()
    destination = tmp_path / "link"
    after = SnapshotEntry(str(destination), b"target", "symlink", 0o777)
    promote(Change(str(destination), None, after), None, after, services=Services.local())
    assert fs.readlink(destination) == "target"
    from plugin_fork_io.store import observe

    actual = observe(destination, services=Services.local())
    promote(Change(str(destination), None, after), actual, None, services=Services.local())
    assert not destination.is_symlink()
    first, second = tmp_path / "first", tmp_path / "second"
    fs.write_exclusive(first, b"new")
    fs.write_exclusive(second, b"old")
    with pytest.raises(FileExistsError):
        fs.replace(first, second, absent=True)
    assert second.read_bytes() == b"old"
    fs.replace(first, second)
    assert second.read_bytes() == b"new"


def test_separately_mapped_resources_and_licenses_are_owned(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.records import SourceMapping
    from plugin_fork_io.store import load_ledger

    source = tmp_path / "source"
    write_skill(source, "review")
    (source / "LICENSE").write_bytes(b"Attribution")
    (source / "shared").mkdir()
    (source / "shared/data").write_bytes(b"resource")
    roots = Roots(tmp_path / "content", tmp_path / "state")
    chosen = replace(selection("review"), resources=(SourceMapping("shared/data", "resources/data"),))
    prepared = plan_fork(SourceSpec(str(source), "local"), chosen, roots, intent=(), services=Services.local())
    result = apply_preview(roots.state, prepared.preview_id, services=Services.local())
    assert result.applied, result
    ledger = load_ledger(roots.state, prepared.variant_id)
    assert {m.destination for m in ledger.mappings} == {"review", "resources/data", "LICENSE"}
    assert (roots.content / "resources/data").read_bytes() == b"resource"
    from plugin_fork_io.status import read_status

    assert not read_status(roots, prepared.variant_id, services=Services.local()).findings


def test_fork_apply_and_status_cli(tmp_path):
    import json

    from plugin_fork_io.cli import app
    from typer.testing import CliRunner

    roots, prepared = prepared_fork(tmp_path)
    runner = CliRunner()
    applied = runner.invoke(app, ["fork", "--apply", prepared.preview_id, "--state-dir", str(roots.state), "--json"])
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["applied"]
    status = runner.invoke(
        app,
        ["status", prepared.variant_id, "--state-dir", str(roots.state), "--content-dir", str(roots.content), "--json"],
    )
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["allowed"]
    invalid = runner.invoke(app, ["fork", "source", "--apply", prepared.preview_id])
    assert invalid.exit_code == 2
    assert runner.invoke(app, ["fork"]).exit_code == 2
    assert runner.invoke(app, ["rollback"]).exit_code == 2
    assert runner.invoke(app, ["rollback", "variant", "--apply", "id"]).exit_code == 2


def test_recovery_cli_prepares_then_applies(tmp_path):
    import json
    from dataclasses import replace

    from plugin_fork_io.cli import app
    from typer.testing import CliRunner

    roots, prepared = prepared_fork(tmp_path)
    apply_preview(
        roots.state,
        prepared.preview_id,
        services=replace(Services.local(), filesystem=FailingFS("SKILL.md", when="after")),
    )
    runner = CliRunner()
    planned = runner.invoke(
        app,
        [
            "rollback",
            prepared.variant_id,
            "--content-dir",
            str(roots.content),
            "--state-dir",
            str(roots.state),
            "--json",
        ],
    )
    assert planned.exit_code == 0, planned.output
    preview_id = json.loads(planned.output)["preview_id"]
    applied = runner.invoke(app, ["rollback", "--apply", preview_id, "--state-dir", str(roots.state), "--json"])
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["applied"]


def test_link_creation_failure_cleans_only_its_staging(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.records import Change, SnapshotEntry
    from plugin_fork_io.transactions import promote

    destination = tmp_path / "link"
    after = SnapshotEntry(str(destination), b"target", "symlink", 0o777)
    with pytest.raises(OSError, match="injected"):
        promote(
            Change(str(destination), None, after),
            None,
            after,
            services=replace(Services.local(), filesystem=FailingFS(".plugin-fork-", "link", "after")),
        )
    assert list(tmp_path.iterdir()) == []


def test_staging_replacement_stays_on_destination_filesystem(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.machine import LocalFileSystem

    class FilesystemSeparated(LocalFileSystem):
        def replace(self, source, destination, **kwargs):
            assert source.parent == destination.parent, "replacement must not cross a volume boundary"
            return super().replace(source, destination, **kwargs)

    roots, prepared = prepared_fork(tmp_path)
    assert apply_preview(
        roots.state, prepared.preview_id, services=replace(Services.local(), filesystem=FilesystemSeparated())
    ).applied


def test_known_owner_prevents_recreation_of_deleted_owned_path(tmp_path):
    import shutil

    roots, prepared = prepared_fork(tmp_path)
    assert apply_preview(roots.state, prepared.preview_id, services=Services.local()).applied
    shutil.rmtree(roots.content / "review")
    second = plan_fork(
        SourceSpec(str(tmp_path / "source"), "local"), selection("review"), roots, intent=(), services=Services.local()
    )
    result = apply_preview(roots.state, second.preview_id, services=Services.local())
    assert "ownership.overlap" in codes(result)


def test_other_mutation_refused_until_recovery(tmp_path):
    from dataclasses import replace

    roots, prepared = prepared_fork(tmp_path)
    apply_preview(
        roots.state,
        prepared.preview_id,
        services=replace(Services.local(), filesystem=FailingFS("SKILL.md", when="after")),
    )
    source = tmp_path / "another"
    write_skill(source, "second")
    second = plan_fork(
        SourceSpec(str(source), "local"), selection("second"), roots, intent=(), services=Services.local()
    )
    result = apply_preview(roots.state, second.preview_id, services=Services.local())
    assert "transaction.incomplete" in codes(result)


@pytest.mark.parametrize("index", range(1, 13))
@pytest.mark.parametrize("when", ["before", "after"])
def test_each_journal_transition_failure_retains_recoverable_intent(tmp_path, index, when):
    from dataclasses import replace

    from plugin_fork_io.machine import LocalFileSystem
    from plugin_fork_io.recovery import plan_rollback
    from plugin_fork_io.transactions import load_journal

    class JournalFailure(LocalFileSystem):
        count = 0

        def replace(self, source, destination, **kwargs):
            hit = False
            if destination.name == "journal.json":
                object.__setattr__(self, "count", self.count + 1)
                hit = self.count == index
            if hit and when == "before":
                raise OSError("journal transition failure")
            super().replace(source, destination, **kwargs)
            if hit and when == "after":
                raise OSError("journal transition failure")

    roots, prepared = prepared_fork(tmp_path)
    fs = JournalFailure()
    result = apply_preview(roots.state, prepared.preview_id, services=replace(Services.local(), filesystem=fs))
    if index > fs.count:
        assert result.applied
        return
    assert not result.applied
    journal = load_journal(roots.state, prepared.preview_id)
    if journal.phase == "complete":
        assert (roots.content / "review/SKILL.md").exists()
        return
    recovery = plan_rollback(roots, prepared.variant_id, services=Services.local())
    assert recovery.allowed, recovery
    assert apply_preview(roots.state, recovery.preview_id, services=Services.local()).applied
    assert not roots.content.exists()


@pytest.mark.parametrize("index", range(1, 81))
def test_directory_sync_fault_boundaries(tmp_path, index):
    from dataclasses import replace

    from plugin_fork_io.machine import LocalFileSystem
    from plugin_fork_io.recovery import plan_rollback
    from plugin_fork_io.transactions import journal_path, load_journal

    class SyncFailure(LocalFileSystem):
        count = 0

        def sync_directory(self, path):
            object.__setattr__(self, "count", self.count + 1)
            super().sync_directory(path)
            if self.count == index:
                raise OSError("directory sync failure")

    roots, prepared = prepared_fork(tmp_path)
    result = apply_preview(
        roots.state, prepared.preview_id, services=replace(Services.local(), filesystem=SyncFailure())
    )
    if result.applied:
        return
    if not journal_path(roots.state, prepared.preview_id).exists():
        assert not roots.content.exists()
        return
    journal = load_journal(roots.state, prepared.preview_id)
    if journal.phase == "complete":
        return
    recovery = plan_rollback(roots, prepared.variant_id, services=Services.local())
    assert recovery.allowed, recovery
    assert apply_preview(roots.state, recovery.preview_id, services=Services.local()).applied


def test_ledger_decoder_rejects_future_schema_and_unsafe_members(tmp_path):
    import json

    from plugin_fork_io.store import load_ledger

    roots, prepared = prepared_fork(tmp_path)
    assert apply_preview(roots.state, prepared.preview_id, services=Services.local()).applied
    path = roots.state / "forks" / prepared.variant_id / "ledger.json"
    value = json.loads(path.read_bytes())
    for field, invalid in [
        ("schema_version", 2),
        ("generation", 0),
        ("content_root", "/absolute"),
        ("base_digest", None),
        ("intent", "wrong"),
    ]:
        modified = dict(value, **{field: invalid})
        path.write_bytes(json.dumps(modified).encode())
        with pytest.raises(ValueError):
            load_ledger(roots.state, prepared.variant_id)
    value["files"][0]["path"] = "../escape"
    path.write_bytes(json.dumps(value).encode())
    with pytest.raises(ValueError):
        load_ledger(roots.state, prepared.variant_id)


def test_recovery_preview_tampering_refused(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.recovery import plan_rollback

    roots, prepared = prepared_fork(tmp_path)
    apply_preview(
        roots.state,
        prepared.preview_id,
        services=replace(Services.local(), filesystem=FailingFS("SKILL.md", when="after")),
    )
    recovery = plan_rollback(roots, prepared.variant_id, services=Services.local())
    path = roots.state / "previews" / recovery.preview_id / "recovery.json"
    path.write_bytes(path.read_bytes().replace(b"Keep local", b"Changed"))
    # Change an actual encoded field, whose byte data is hexadecimal.
    path.write_bytes(path.read_bytes().replace(b'"schema_version":1', b'"schema_version":2'))
    assert "preview.invalid" in codes(apply_preview(roots.state, recovery.preview_id, services=Services.local()))
    assert (roots.content / "review/SKILL.md").exists()


def test_journal_intent_names_staging_before_any_live_file_promotion(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.machine import LocalFileSystem
    from plugin_fork_io.recovery import plan_rollback
    from plugin_fork_io.transactions import load_journal

    class SimulatedCrash(BaseException):
        pass

    roots, prepared = prepared_fork(tmp_path)

    class CrashingFS(LocalFileSystem):
        def write_exclusive(self, path, content):
            super().write_exclusive(path, content)
            if path.name.startswith(".plugin-fork-"):
                journal = load_journal(roots.state, prepared.preview_id)
                assert str(path) in {c.destination for c in journal.staged_changes}
                raise SimulatedCrash()

    with pytest.raises(SimulatedCrash):
        apply_preview(roots.state, prepared.preview_id, services=replace(Services.local(), filesystem=CrashingFS()))
    assert not (roots.content / "review/SKILL.md").exists()
    recovery = plan_rollback(roots, prepared.variant_id, services=Services.local())
    assert recovery.allowed, recovery
    assert apply_preview(roots.state, recovery.preview_id, services=Services.local()).applied
    assert not roots.content.exists()


def test_recovery_never_removes_a_directory_it_failed_to_create(tmp_path, monkeypatch):
    from pathlib import Path

    from plugin_fork_io.recovery import plan_rollback

    roots, prepared = prepared_fork(tmp_path)
    original = Path.mkdir

    def racing_mkdir(path, *args, **kwargs):
        if path == roots.content:
            original(path, *args, **kwargs)
            raise FileExistsError("another creator won")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", racing_mkdir)
    result = apply_preview(roots.state, prepared.preview_id, services=Services.local())
    assert not result.applied
    recovery = plan_rollback(roots, prepared.variant_id, services=Services.local())
    assert "recovery.ambiguous" in codes(recovery)
    assert roots.content.is_dir()


@pytest.mark.parametrize("boundary", ["SKILL.md", "ledger.json"])
def test_late_new_owned_file_blocks_tracking_or_completion(tmp_path, boundary):
    from dataclasses import replace

    from plugin_fork_io.machine import LocalFileSystem
    from plugin_fork_io.transactions import load_journal

    roots, prepared = prepared_fork(tmp_path)

    class AddingFileFS(LocalFileSystem):
        def replace(self, source, destination, **kwargs):
            super().replace(source, destination, **kwargs)
            if destination.name == boundary:
                (roots.content / "review/human.txt").write_bytes(b"late human work")

    result = apply_preview(
        roots.state, prepared.preview_id, services=replace(Services.local(), filesystem=AddingFileFS())
    )
    assert not result.applied
    assert "transaction.verify" in codes(result)
    assert load_journal(roots.state, prepared.preview_id).phase != "complete"
    assert (roots.content / "review/human.txt").read_bytes() == b"late human work"
    if boundary == "SKILL.md":
        assert not (roots.state / "forks" / prepared.variant_id / "ledger.json").exists()


@pytest.mark.parametrize("boundary", ["SKILL.md", "ledger.json"])
def test_created_directory_replacement_with_identical_bytes_is_refused(tmp_path, boundary):
    import shutil
    from dataclasses import replace

    from plugin_fork_io.machine import LocalFileSystem

    roots, prepared = prepared_fork(tmp_path)

    class ReplacingDirectoryFS(LocalFileSystem):
        def replace(self, source, destination, **kwargs):
            super().replace(source, destination, **kwargs)
            if destination.name == boundary:
                path = roots.content / "review"
                path.rename(tmp_path / "original-review")
                shutil.copytree(tmp_path / "original-review", path)

    result = apply_preview(
        roots.state, prepared.preview_id, services=replace(Services.local(), filesystem=ReplacingDirectoryFS())
    )
    assert not result.applied
    assert "preview.stale" in codes(result)
    assert (roots.content / "review/SKILL.md").exists()


def test_recovery_rechecks_directory_identity_at_deletion(tmp_path, monkeypatch):
    from dataclasses import replace

    import plugin_fork_io.recovery as recovery_module

    roots, prepared = prepared_fork(tmp_path)
    apply_preview(
        roots.state,
        prepared.preview_id,
        services=replace(Services.local(), filesystem=FailingFS("SKILL.md", when="after")),
    )
    recovery = recovery_module.plan_rollback(roots, prepared.variant_id, services=Services.local())
    original_promote = recovery_module.promote

    def swap_before_delete(change, expected, value, **kwargs):
        if change.destination == str(roots.content / "review"):
            path = roots.content / "review"
            path.rename(tmp_path / "original-review")
            path.mkdir()
        return original_promote(change, expected, value, **kwargs)

    monkeypatch.setattr(recovery_module, "promote", swap_before_delete)
    result = apply_preview(roots.state, recovery.preview_id, services=Services.local())
    assert not result.applied
    assert "recovery.ambiguous" in codes(result)
    assert (roots.content / "review").is_dir()


def test_late_file_in_separately_mapped_resource_root_is_refused(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.machine import LocalFileSystem
    from plugin_fork_io.records import SourceMapping

    source = tmp_path / "source"
    write_skill(source, "review")
    (source / "shared").mkdir()
    (source / "shared/data").write_bytes(b"resource")
    roots = Roots(tmp_path / "content", tmp_path / "state")
    chosen = replace(selection("review"), resources=(SourceMapping("shared", "resources"),))
    prepared = plan_fork(SourceSpec(str(source), "local"), chosen, roots, intent=(), services=Services.local())

    class AddingResourceFS(LocalFileSystem):
        def replace(self, staged, destination, **kwargs):
            super().replace(staged, destination, **kwargs)
            if destination.name == "SKILL.md":
                (roots.content / "resources/human").write_bytes(b"human resource")

    result = apply_preview(
        roots.state, prepared.preview_id, services=replace(Services.local(), filesystem=AddingResourceFS())
    )
    assert "transaction.verify" in codes(result)
    assert not result.applied
    assert (roots.content / "resources/human").read_bytes() == b"human resource"
