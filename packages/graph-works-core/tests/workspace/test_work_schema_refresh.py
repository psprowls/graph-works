"""`plan_schema_refresh` / `apply_schema_refresh`: provenance-guarded and all-or-nothing."""

from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.workspace import work_schemas as ws
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout

TODAY = date(2026, 10, 5)
BASE = "schema/_base.schema.json"
BUG = "schema/Bug.schema.json"


@pytest.fixture
def layout(tmp_path: Path) -> WorkspaceLayout:
    layout = apply_init(plan_init(tmp_path / "workspace", today=TODAY, topic="Refresh")).layout
    _path(layout, ws.PROVENANCE_RELATIVE).unlink(missing_ok=True)
    return layout


def _path(layout: WorkspaceLayout, relative: str) -> Path:
    return ws.declarations_dir_for(layout) / relative


def _record(layout: WorkspaceLayout, overrides: dict[str, bytes]) -> None:
    digests = {k: hashlib.sha256(v).hexdigest() for k, v in ws.packaged_schemas().items()}
    digests.update({k: hashlib.sha256(v).hexdigest() for k, v in overrides.items()})
    _path(layout, ws.PROVENANCE_RELATIVE).write_bytes(ws.render_provenance(digests))


def _snapshot(layout: WorkspaceLayout) -> dict[str, bytes]:
    root = ws.declarations_dir_for(layout)
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / "schema").iterdir() if p.is_file()}


def test_plan_writes_nothing(layout: WorkspaceLayout) -> None:
    _path(layout, BASE).write_bytes(b"{}\n")
    before = _snapshot(layout)
    ws.plan_schema_refresh(layout, force=True)
    assert _snapshot(layout) == before


def test_recognized_old_copy_is_replaced(layout: WorkspaceLayout) -> None:
    old = b'{"old": 1}\n'
    _path(layout, BASE).write_bytes(old)
    _record(layout, {BASE: old})
    plan = ws.plan_schema_refresh(layout)
    assert plan.ok and [w.relative for w in plan.writes] == [BASE]
    assert '-{"old": 1}' in plan.writes[0].diff
    ws.apply_schema_refresh(plan)
    assert _path(layout, BASE).read_bytes() == ws.packaged_schemas()[BASE]
    assert ws.inspect_work_schemas(layout).drifted == ()


def test_missing_file_is_created(layout: WorkspaceLayout) -> None:
    _path(layout, BUG).unlink()
    ws.apply_schema_refresh(ws.plan_schema_refresh(layout))
    assert _path(layout, BUG).read_bytes() == ws.packaged_schemas()[BUG]


def test_missing_configured_schema_directory_is_repaired_in_place(layout: WorkspaceLayout) -> None:
    from graph_works_core.work.commands import run_lint
    from graph_works_core.workspace.config import load_workspace_config

    (layout.config_dir / "schema").rename(layout.root / "saved-schema")
    plan = ws.plan_schema_refresh(layout)
    assert plan.declarations_dir == layout.config_dir
    assert plan.ok and len(plan.writes) == 8
    ws.apply_schema_refresh(plan)
    assert ws.inspect_work_schemas(layout).drifted == ()
    assert not (layout.bundle_dir / "schema").exists()
    assert run_lint(layout, load_workspace_config(layout), today=TODAY).ok


def test_local_edit_is_refused_with_diff(layout: WorkspaceLayout) -> None:
    _record(layout, {})
    _path(layout, BASE).write_bytes(b"{}\n")
    plan = ws.plan_schema_refresh(layout)
    [refusal] = plan.refusals
    assert refusal.reason == "edited" and refusal.diff
    with pytest.raises(WorkspaceError):
        ws.apply_schema_refresh(plan)
    assert _path(layout, BASE).read_bytes() == b"{}\n"


def test_legacy_copy_without_provenance_is_refused_then_forced(layout: WorkspaceLayout) -> None:
    _path(layout, ws.PROVENANCE_RELATIVE).unlink(missing_ok=True)
    _path(layout, BASE).write_bytes(b"{}\n")
    assert [r.reason for r in ws.plan_schema_refresh(layout).refusals] == ["unrecorded"]
    ws.apply_schema_refresh(ws.plan_schema_refresh(layout, force=True))
    assert ws.inspect_work_schemas(layout).drifted == ()
    assert ws.inspect_work_schemas(layout).recorded is not None


def test_malformed_provenance_does_not_bless_a_differing_file(layout: WorkspaceLayout) -> None:
    _path(layout, ws.PROVENANCE_RELATIVE).write_bytes(b"{nope")
    _path(layout, BASE).write_bytes(b"{}\n")
    assert [r.reason for r in ws.plan_schema_refresh(layout).refusals] == ["unrecorded"]


def test_refresh_records_provenance_for_current_files(layout: WorkspaceLayout) -> None:
    _path(layout, ws.PROVENANCE_RELATIVE).unlink(missing_ok=True)
    plan = ws.plan_schema_refresh(layout)
    assert plan.writes == () and plan.provenance is not None
    ws.apply_schema_refresh(plan)
    assert ws.plan_schema_refresh(layout).changed is False


def test_second_refresh_is_empty(layout: WorkspaceLayout) -> None:
    _path(layout, BUG).unlink()
    ws.apply_schema_refresh(ws.plan_schema_refresh(layout))
    assert ws.plan_schema_refresh(layout).changed is False


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_symlink_target_refused_even_with_force(layout: WorkspaceLayout, tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    target.write_bytes(b"{}\n")
    _path(layout, BASE).unlink()
    _path(layout, BASE).symlink_to(target)
    plan = ws.plan_schema_refresh(layout, force=True)
    assert [r.reason for r in plan.refusals] == ["unsafe"]
    with pytest.raises(WorkspaceError):
        ws.apply_schema_refresh(plan)
    assert target.read_bytes() == b"{}\n"


def test_multi_file_refusal_writes_nothing(layout: WorkspaceLayout) -> None:
    _record(layout, {})
    _path(layout, BUG).unlink()  # would be created
    _path(layout, BASE).write_bytes(b"{}\n")  # refused
    before = _snapshot(layout)
    with pytest.raises(WorkspaceError):
        ws.apply_schema_refresh(ws.plan_schema_refresh(layout))
    assert _snapshot(layout) == before


def test_stale_plan_is_rejected(layout: WorkspaceLayout) -> None:
    _path(layout, BUG).unlink()
    plan = ws.plan_schema_refresh(layout)
    _path(layout, BASE).write_bytes(b"{}\n")
    with pytest.raises(ws.SchemaRefreshStale):
        ws.apply_schema_refresh(plan)
    assert not _path(layout, BUG).exists()


def test_write_failure_rolls_back(layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    old = b'{"old": 1}\n'
    _path(layout, BASE).write_bytes(old)
    _path(layout, BUG).write_bytes(old)
    _record(layout, {BASE: old, BUG: old})
    before = _snapshot(layout)
    plan = ws.plan_schema_refresh(layout)
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        real_replace(src, dst)

    monkeypatch.setattr(ws.os, "replace", flaky)
    with pytest.raises(OSError):
        ws.apply_schema_refresh(plan)
    monkeypatch.setattr(ws.os, "replace", real_replace)
    assert _snapshot(layout) == before


def test_custom_schema_is_preserved(layout: WorkspaceLayout) -> None:
    _path(layout, "schema/Custom.schema.json").write_bytes(b"{}\n")
    _path(layout, BUG).unlink()
    ws.apply_schema_refresh(ws.plan_schema_refresh(layout))
    assert _path(layout, "schema/Custom.schema.json").read_bytes() == b"{}\n"


def test_seed_provenance_only_when_all_current(layout: WorkspaceLayout) -> None:
    _path(layout, ws.PROVENANCE_RELATIVE).unlink(missing_ok=True)
    _path(layout, BASE).write_bytes(b"{}\n")
    assert ws.seed_provenance(layout) is None
    _path(layout, BASE).write_bytes(ws.packaged_schemas()[BASE])
    assert ws.seed_provenance(layout) is not None
    assert ws.seed_provenance(layout) is None  # already present and valid


@pytest.mark.parametrize("target", [BASE, ws.PROVENANCE_RELATIVE, "schema"])
@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_unsafe_paths_refused_even_with_force(layout: WorkspaceLayout, tmp_path: Path, target: str, kind: str) -> None:
    import shutil

    path = _path(layout, target)
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)
    if kind == "symlink":
        outside = tmp_path / "outside"
        outside.mkdir()
        path.symlink_to(outside, target_is_directory=True)
    else:
        path.mkdir()
    plan = ws.plan_schema_refresh(layout, force=True)
    # A schema directory is a valid parent; the directory variant applies only to files.
    if target == "schema" and kind == "directory":
        assert plan.ok
        return
    assert any(r.reason == "unsafe" for r in plan.refusals)
    with pytest.raises(WorkspaceError):
        ws.apply_schema_refresh(plan)


@pytest.mark.parametrize("target", [BASE, ws.PROVENANCE_RELATIVE, "schema"])
def test_same_bytes_replaced_identity_is_stale(layout: WorkspaceLayout, target: str) -> None:
    _record(layout, {})
    _path(layout, BUG).unlink()
    plan = ws.plan_schema_refresh(layout)
    path = _path(layout, target)
    saved = path.with_name(path.name + ".saved")
    path.rename(saved)
    if saved.is_dir():
        import shutil

        shutil.copytree(saved, path)
    else:
        path.write_bytes(saved.read_bytes())
    with pytest.raises(ws.SchemaRefreshStale):
        ws.apply_schema_refresh(plan)
    assert not _path(layout, BUG).exists()


@pytest.mark.parametrize("target", [BASE, ws.PROVENANCE_RELATIVE])
def test_unreadable_inputs_are_reviewable_refusals(
    layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    real_read = Path.read_bytes
    path = _path(layout, target)

    def unreadable(self: Path) -> bytes:
        if self == path:
            raise PermissionError("denied")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    plan = ws.plan_schema_refresh(layout, force=True)
    assert any(r.reason == "unsafe" for r in plan.refusals)
    with pytest.raises(WorkspaceError):
        ws.apply_schema_refresh(plan)


@pytest.mark.parametrize("corruption", ["outside", "after", "before", "relative", "fingerprints", "omit", "provenance"])
def test_malformed_plan_refused_before_writes(layout: WorkspaceLayout, tmp_path: Path, corruption: str) -> None:
    from dataclasses import replace

    _path(layout, BUG).unlink()
    plan = ws.plan_schema_refresh(layout)
    write = plan.writes[0]
    if corruption == "outside":
        plan = replace(plan, writes=(replace(write, path=tmp_path / "outside"),))
    elif corruption == "after":
        plan = replace(plan, writes=(replace(write, after=b"{}"),))
    elif corruption == "before":
        plan = replace(plan, writes=(replace(write, before=b"{}"),))
    elif corruption == "relative":
        plan = replace(plan, writes=(replace(write, relative="../outside"),))
    elif corruption == "fingerprints":
        plan = replace(plan, fingerprints={})
    elif corruption == "omit":
        plan = replace(plan, writes=())
    else:
        assert plan.provenance is not None
        plan = replace(plan, provenance=replace(plan.provenance, after=b"{}"))
    with pytest.raises(WorkspaceError):
        ws.apply_schema_refresh(plan)
    assert not _path(layout, BUG).exists()
    assert not (tmp_path / "outside").exists()


def test_partial_write_failure_cleans_temporary(layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    _path(layout, BUG).unlink()
    plan = ws.plan_schema_refresh(layout)
    from contextlib import contextmanager

    real_open = Path.open

    class PartialWriter:
        def __init__(self, stream):
            self.stream = stream

        def write(self, data):
            self.stream.write(data[:3])
            raise OSError("short write")

    @contextmanager
    def fail(self, *args, **kwargs):
        with real_open(self, *args, **kwargs) as stream:
            yield PartialWriter(stream) if ".tmp-" in self.name else stream

    monkeypatch.setattr(Path, "open", fail)
    with pytest.raises(OSError):
        ws.apply_schema_refresh(plan)
    assert not _path(layout, BUG).exists()
    assert not list(ws.declarations_dir_for(layout).rglob("*.tmp-*"))


def test_provenance_failure_restores_created_file(layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    _path(layout, BUG).unlink()
    before = _snapshot(layout)
    real_replace = os.replace

    def fail(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        if Path(dst).name == "work-tracker-okf.provenance.json":
            raise OSError("provenance failure")
        real_replace(src, dst)

    monkeypatch.setattr(ws.os, "replace", fail)
    with pytest.raises(OSError):
        ws.apply_schema_refresh(ws.plan_schema_refresh(layout))
    assert _snapshot(layout) == before


def test_seed_invalid_provenance_repaired_but_valid_history_retained(layout: WorkspaceLayout) -> None:
    _record(layout, {BASE: b"old"})
    before = _path(layout, ws.PROVENANCE_RELATIVE).read_bytes()
    assert ws.seed_provenance(layout) is None
    assert _path(layout, ws.PROVENANCE_RELATIVE).read_bytes() == before
    _path(layout, ws.PROVENANCE_RELATIVE).write_bytes(b"{invalid")
    assert ws.seed_provenance(layout) is not None
    assert ws.inspect_work_schemas(layout).recorded is not None


@pytest.mark.parametrize("target", [BASE, ws.PROVENANCE_RELATIVE])
def test_content_change_is_stale_even_with_force(layout: WorkspaceLayout, target: str) -> None:
    _path(layout, BUG).unlink()
    plan = ws.plan_schema_refresh(layout, force=True)
    _path(layout, target).write_bytes(b"new content")
    with pytest.raises(ws.SchemaRefreshStale):
        ws.apply_schema_refresh(plan)
    assert not _path(layout, BUG).exists()


def test_existing_temp_symlink_is_preserved(layout: WorkspaceLayout, tmp_path: Path) -> None:
    _path(layout, BUG).unlink()
    target = tmp_path / "unrelated"
    target.write_bytes(b"preserve me")
    temporary = _path(layout, BUG).with_name(f"Bug.schema.json.tmp-{os.getpid()}")
    temporary.symlink_to(target)
    with pytest.raises(OSError):
        ws.apply_schema_refresh(ws.plan_schema_refresh(layout))
    assert target.read_bytes() == b"preserve me"
    assert temporary.is_symlink()
    assert not _path(layout, BUG).exists()


def test_relocated_declarations_return_workspace_labels(layout: WorkspaceLayout) -> None:
    from dataclasses import replace

    directory = layout.root / "declarations"
    layout.config_dir.rename(directory)
    found = replace(layout, config_dir=directory)
    _path(found, BUG).unlink()
    result = ws.apply_schema_refresh(ws.plan_schema_refresh(found))
    assert result.written == (
        "declarations/schema/Bug.schema.json",
        "declarations/schema/work-tracker-okf.provenance.json",
    )
    assert result.commit is not None
    assert not ws.plan_schema_refresh(found).changed


def test_external_declarations_are_refreshed_without_workspace_commit(layout: WorkspaceLayout, tmp_path: Path) -> None:
    import shutil
    from dataclasses import replace

    outside = tmp_path / "declarations"
    shutil.copytree(layout.config_dir, outside)
    found = replace(layout, config_dir=outside)
    _path(found, BUG).unlink()
    result = ws.apply_schema_refresh(ws.plan_schema_refresh(found))
    assert result.commit is None
    assert _path(found, BUG).read_bytes() == ws.packaged_schemas()[BUG]
    assert all(Path(label).is_absolute() for label in result.written)


def test_real_git_commit_includes_only_refresh_paths(layout: WorkspaceLayout) -> None:
    import subprocess

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(layout.root), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    _path(layout, BUG).write_bytes(b'{"old": 1}\n')
    _record(layout, {BUG: b'{"old": 1}\n'})
    git("init")
    git("config", "user.name", "Refresh Test")
    git("config", "user.email", "refresh@example.test")
    git("add", ".")
    git("commit", "-m", "workspace: initial")
    unrelated = layout.root / "authored.txt"
    unrelated.write_bytes(b"authored")
    git("add", "authored.txt")
    _path(layout, BUG).unlink()
    result = ws.apply_schema_refresh(ws.plan_schema_refresh(layout))
    assert result.commit is not None and result.commit.status == "committed"
    assert git("log", "-1", "--format=%s") == "workspace: refresh work-lane schemas"
    assert set(git("show", "--format=", "--name-only", "HEAD").splitlines()) == {
        ".gw/schema/Bug.schema.json",
        ".gw/schema/work-tracker-okf.provenance.json",
    }
    assert git("diff", "--cached", "--name-only") == "authored.txt"
    assert ws.apply_schema_refresh(ws.plan_schema_refresh(layout)).commit is None


def test_seed_does_not_follow_unsafe_provenance(layout: WorkspaceLayout, tmp_path: Path) -> None:
    target = tmp_path / "outside"
    target.write_bytes(b"{invalid")
    _path(layout, ws.PROVENANCE_RELATIVE).symlink_to(target)
    assert ws.seed_provenance(layout) is None
    assert target.read_bytes() == b"{invalid"


@pytest.mark.parametrize(
    "field,value",
    [
        ("fingerprints", None),
        ("identities", {}),
        ("writes", (None,)),
        ("refusals", ("unsafe",)),
        ("force", "yes"),
        ("declarations_dir", "/outside"),
    ],
)
def test_invalid_plan_field_types_are_workspace_refusals(layout: WorkspaceLayout, field: str, value: object) -> None:
    from dataclasses import replace

    _path(layout, BUG).unlink()
    plan = replace(ws.plan_schema_refresh(layout), **{field: value})
    with pytest.raises(WorkspaceError):
        ws.apply_schema_refresh(plan)
    assert not _path(layout, BUG).exists()


def test_rollback_continues_and_reports_a_restore_failure(
    layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = list(ws.packaged_schemas())[:3]
    old = b'{"old": 1}\n'
    for relative in owned:
        _path(layout, relative).write_bytes(old)
    _record(layout, dict.fromkeys(owned, old))
    plan = ws.plan_schema_refresh(layout)
    real_replace = os.replace
    calls = 0

    def fail(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        nonlocal calls
        calls += 1
        if calls in (4, 5):
            raise OSError("write failure" if calls == 4 else "restore failure")
        real_replace(src, dst)

    monkeypatch.setattr(ws.os, "replace", fail)
    with pytest.raises(OSError, match="write failure") as caught:
        ws.apply_schema_refresh(plan)
    assert _path(layout, owned[0]).read_bytes() == old
    assert _path(layout, owned[1]).read_bytes() == old
    assert any("restore failure" in note for note in caught.value.__notes__)


def test_input_change_on_lock_acquisition_is_rejected(layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import contextmanager

    from graph_works_core.workspace import transactions

    _path(layout, BUG).unlink()
    plan = ws.plan_schema_refresh(layout)
    real_lock = transactions.held_bundle_lock

    @contextmanager
    def changed_on_acquisition(found: WorkspaceLayout):
        with real_lock(found):
            _path(layout, BASE).write_bytes(b"changed at lock boundary")
            yield

    monkeypatch.setattr(transactions, "held_bundle_lock", changed_on_acquisition)
    with pytest.raises(ws.SchemaRefreshStale):
        ws.apply_schema_refresh(plan)
    assert not _path(layout, BUG).exists()


def test_legacy_destination_selection_change_is_stale(layout: WorkspaceLayout) -> None:
    layout.manifest_path.unlink()
    (layout.config_dir / "schema").rename(layout.bundle_dir / "schema")
    _path(layout, BUG).unlink()
    plan = ws.plan_schema_refresh(layout)
    assert plan.declarations_dir == layout.bundle_dir
    layout.manifest_path.write_text("version: 1\ntopic: Refresh\n", encoding="utf-8", newline="")
    (layout.config_dir / "schema").mkdir()
    with pytest.raises(ws.SchemaRefreshStale):
        ws.apply_schema_refresh(plan)
    assert not (layout.bundle_dir / BUG).exists()


@pytest.mark.skipif(os.name == "nt", reason="FIFO creation is POSIX-only")
@pytest.mark.parametrize("kind", ["fifo", "fifo-symlink", "directory", "symlink-parent"])
@pytest.mark.parametrize("operation", ["plan", "seed"])
def test_refresh_unsafe_provenance_never_opens_it(
    layout: WorkspaceLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, operation: str
) -> None:
    path = _path(layout, ws.PROVENANCE_RELATIVE)
    path.unlink(missing_ok=True)
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "fifo-symlink":
        target = tmp_path / "outside.fifo"
        os.mkfifo(target)
        path.symlink_to(target)
    elif kind == "directory":
        path.mkdir()
    else:
        parent = path.parent
        target = tmp_path / "outside-schema"
        parent.rename(target)
        os.mkfifo(target / path.name)
        parent.symlink_to(target, target_is_directory=True)
    real_open = Path.open

    def no_unsafe_open(self: Path, *args: object, **kwargs: object) -> object:
        if self == path:
            pytest.fail("unsafe provenance opened before type/parent guard")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_unsafe_open)
    if operation == "seed":
        assert ws.seed_provenance(layout) is None
    else:
        plan = ws.plan_schema_refresh(layout, force=True)
        assert any(refusal.reason == "unsafe" for refusal in plan.refusals)
        with pytest.raises(WorkspaceError):
            ws.apply_schema_refresh(plan)
    assert path.exists()
