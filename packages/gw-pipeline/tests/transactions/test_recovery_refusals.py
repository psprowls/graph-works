"""Custody recovery preserves concurrent entries and reports unverifiable restoration."""

from __future__ import annotations

import os
from contextlib import closing
from types import SimpleNamespace

import pytest
from gw_kernel.testing.transactions import plan, workspace
from gw_pipeline import transactions as tx


@pytest.mark.parametrize("location", ["quarantine", "destination"])
@pytest.mark.parametrize(
    "fault", ["none", "changed-identity", "recreated-source", "changed-content", "quarantine-remains"]
)
def test_move_custody_recovers_original_or_retains_evidence(tmp_path, monkeypatch, location, fault):
    (tmp_path / "source").write_bytes(b"original")
    with closing(tx._open_root(tmp_path)) as root:
        identity = root.identity("source")
        fingerprint = tx._entry_fingerprint_at(root, "source")
        captured = "quarantine" if location == "quarantine" else "destination"
        (tmp_path / "source").rename(tmp_path / captured)
        if fault == "changed-identity":
            (tmp_path / captured).unlink()
            (tmp_path / captured).write_bytes(b"replacement")
            # Filesystems may immediately recycle the removed inode: inject observed mismatch deterministically.
            original_identity = tx._identity_at
            monkeypatch.setattr(
                tx,
                "_identity_at",
                lambda parent, name: (-1, -1) if name == captured else original_identity(parent, name),
            )
        elif fault == "recreated-source":
            (tmp_path / "source").write_bytes(b"concurrent")
        elif fault == "changed-content":
            (tmp_path / captured).write_bytes(b"altered")
        elif fault == "quarantine-remains":
            original_exists = tx._lexists_at
            monkeypatch.setattr(
                tx, "_lexists_at", lambda anchor, name: name == "quarantine" or original_exists(anchor, name)
            )
        touched = {"source", "destination"} if location == "destination" else {"source"}
        protected = {"source": "quarantine", "quarantine": "quarantine", "destination": "destination"}
        expected_error = RuntimeError if fault == "none" else tx._CustodyConflict
        with pytest.raises(expected_error):
            tx._recover_move_custody_or_raise(
                root,
                tx._Effect("move", "source", "destination"),
                source=root,
                source_name="source",
                quarantine="quarantine",
                quarantine_member="quarantine",
                destination=root,
                destination_name="destination",
                captured_at_destination=location == "destination",
                captured_identity=identity,
                expected_fingerprint=fingerprint,
                touched=touched,
                protected=protected,
                previously_touched=False,
                destination_was_touched=False,
                original=RuntimeError("injected move failure"),
            )
        if fault == "none":
            assert (tmp_path / "source").read_bytes() == b"original"
            assert not (tmp_path / "destination").exists()
            assert not protected and not touched
        elif fault == "recreated-source":
            assert (tmp_path / "source").read_bytes() == b"concurrent"
            assert (tmp_path / "quarantine").read_bytes() == b"original"
        elif fault in {"changed-content", "quarantine-remains"}:
            assert protected["source"] == "source"


@pytest.mark.parametrize("kind", ["file", "directory", "writable"])
def test_windows_custody_cleanup_clears_readonly_before_unlink(tmp_path, monkeypatch, kind):
    path = tmp_path / "entry"
    if kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"original")
    path.chmod(0o400 if kind != "writable" else 0o600)
    monkeypatch.setattr(tx, "sys", SimpleNamespace(platform="win32"))
    with closing(tx._open_root(tmp_path)) as root:
        tx._clear_read_only_before_unlink(root, "entry")
        assert path.stat().st_mode & 0o200


def test_transaction_creates_missing_cache_parent_under_anchor(tmp_path):
    layout = workspace(tmp_path)
    layout.cache_dir.rmdir()
    result = tx.apply_mutation(layout, plan(layout, mkdirs=("new",)), commit=None)
    assert result.ok, result.failures
    assert layout.cache_dir.is_dir() and (layout.bundle_dir / "new").is_dir()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires a POSIX named pipe")
@pytest.mark.parametrize("operation", ["manifest", "copy", "copy-live", "restore"])
def test_unsupported_entry_kind_is_refused_without_reading_pipe(tmp_path, operation):
    root_path = tmp_path / "root"
    root_path.mkdir()
    pipe = root_path / "pipe"
    os.mkfifo(pipe)
    with (
        closing(tx._open_root(root_path)) as root,
        pytest.raises(ValueError, match="unsupported filesystem entry type"),
    ):
        if operation == "manifest":
            tx._entry_manifest_at(root, "pipe", include_mode=True)
        elif operation == "copy":
            tx._copy_entry(pipe, tmp_path / "copy")
        elif operation == "copy-live":
            tx._copy_live_entry(root, "pipe", tmp_path / "copy")
        else:
            tx._copy_backup_to_live(root, pipe, "restored")
    assert not (root_path / "restored").exists()


@pytest.mark.parametrize("check", ["locate", "fingerprint", "missing", "changed"])
def test_snapshot_verification_reports_unprovable_entries(tmp_path, monkeypatch, check):
    (tmp_path / "entry").write_bytes(b"original")
    with closing(tx._open_root(tmp_path)) as root:
        entry = tx._SnapshotEntry("entry", True, tmp_path / "unused", "expected")
        if check == "locate":
            monkeypatch.setattr(tx, "_lexists_at", lambda *args: (_ for _ in ()).throw(OSError("denied")))
        elif check == "fingerprint":
            monkeypatch.setattr(tx, "_entry_fingerprint_at", lambda *args: (_ for _ in ()).throw(OSError("denied")))
        elif check == "missing":
            (tmp_path / "entry").unlink()
        (failure,) = tx._verify_snapshot(root, (entry,))
        assert failure.startswith("entry: ")
        assert (
            "original" in failure
            if check == "missing"
            else ("denied" in failure if check != "changed" else "differs" in failure)
        )


def test_directory_snapshot_restores_nested_bytes_links_and_modes(tmp_path):
    layout = workspace(tmp_path)
    tree = layout.bundle_dir / "tree"
    (tree / "nested").mkdir(parents=True)
    (tree / "nested/file").write_bytes(b"captured")
    try:
        (tree / "link").symlink_to("nested/file")
    except OSError as exc:
        pytest.skip(f"host cannot create directory snapshot symlink: {exc}")
    tree.chmod(0o750)
    (tree / "nested").chmod(0o700)
    tree_mode = tree.stat().st_mode & 0o777
    nested_mode = (tree / "nested").stat().st_mode & 0o777
    transaction = tmp_path / "transaction"
    transaction.mkdir()
    captured = tx._create_snapshot(plan(layout, deletes=("tree",)), transaction)
    tx._remove_entry(tree)
    tx._restore_snapshot(layout.bundle_dir, captured)
    assert (tree / "nested/file").read_bytes() == b"captured"
    assert (tree / "link").readlink().as_posix() == "nested/file"
    assert tree.stat().st_mode & 0o777 == tree_mode
    assert (tree / "nested").stat().st_mode & 0o777 == nested_mode
    with closing(tx._open_root(layout.bundle_dir)) as root:
        assert tx._verify_snapshot(root, captured) == ()


def test_commit_digest_read_failure_restores_preimage_and_reports_refusal(tmp_path, monkeypatch):
    import hashlib

    from work_tracker_okf.mutation import PlannedWrite

    layout = workspace(tmp_path)
    page = layout.bundle_dir / "existing.md"
    page.write_bytes(b"original")
    mutation = plan(layout, writes=(PlannedWrite("existing.md", hashlib.sha256(b"original").hexdigest(), b"updated"),))
    original_digest = tx._digest_in_directory

    def digest(parent, name):
        if name.endswith(".quarantine"):
            raise OSError("read refused")
        return original_digest(parent, name)

    monkeypatch.setattr(tx, "_digest_in_directory", digest)
    result = tx.apply_mutation(layout, mutation, commit=None)
    assert not result.ok
    assert not result.rolled_back  # The custody helper restored its preimage before rollback was needed.
    assert "changed since planning" in result.failures[0]
    assert page.read_bytes() == b"original"
    assert not list(layout.bundle_dir.glob("*.quarantine"))


def test_snapshot_verification_detects_an_originally_absent_entry(tmp_path):
    (tmp_path / "new").write_bytes(b"concurrent")
    with closing(tx._open_root(tmp_path)) as root:
        assert tx._verify_snapshot(root, (tx._SnapshotEntry("new", False, None, None),)) == (
            "new: originally absent target remains",
        )


@pytest.mark.parametrize("fault", ["missing", "changed", "delete-fails"])
def test_delete_custody_refuses_concurrent_change_and_restores_preimage(tmp_path, monkeypatch, fault):
    path = tmp_path / "entry"
    path.write_bytes(b"original")
    with closing(tx._open_root(tmp_path)) as root:
        fingerprint = tx._entry_fingerprint_at(root, "entry")
        identity = tx._entry_identity(path.stat())
        if fault == "missing":
            path.unlink()
        elif fault == "changed":
            path.write_bytes(b"external")
        else:
            monkeypatch.setattr(
                tx, "_clear_read_only_before_unlink", lambda *args: (_ for _ in ()).throw(OSError("unlink refused"))
            )
        with pytest.raises(ValueError, match=r"changed since planning|could not delete"):
            tx._commit_effect(
                tmp_path,
                tx._Effect("delete", "entry"),
                root=root,
                expected_fingerprints={"entry": fingerprint},
                expected_identities={"entry": identity},
            )
    if fault != "missing":
        assert path.read_bytes() == (b"external" if fault == "changed" else b"original")
    assert not list(tmp_path.glob("*.quarantine"))
