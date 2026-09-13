import pytest
from helpers import write_skill
from plugin_fork_io.machine import Services
from plugin_fork_io.records import SourceSpec
from plugin_fork_io.snapshots import SnapshotError, capture, read_snapshot, write_snapshot


def test_archive_preserves_bytes_and_has_no_machine_identity(tmp_path):
    source = tmp_path / "source"
    skill = write_skill(source, "review")
    skill.write_bytes(b"\xef\xbb\xbf---\r\nname: review\r\ndescription: Review\r\n---\r\n")
    snap = capture(SourceSpec(str(source), "local"), ("skills/review/SKILL.md",), services=Services.local())
    archive = tmp_path / "base.tar.gz"
    write_snapshot(snap, archive)
    restored = read_snapshot(archive)
    assert restored.entries[0].content == skill.read_bytes()
    assert restored.source.resolved_commit is None
    assert restored.digest == snap.digest
    second = tmp_path / "other.gz"
    write_snapshot(snap, second)
    assert archive.read_bytes() == second.read_bytes()


@pytest.mark.parametrize("path", ["/a", "../a", "C:/a", "//host/a", "a\\..\\b", "NUL", "a.", "a:b"])
def test_refuses_unsafe_selected_paths(tmp_path, path):
    with pytest.raises(SnapshotError):
        capture(SourceSpec(str(tmp_path), "local"), (path,), services=Services.local())


def test_local_links_are_evidence_and_materialize_contained_targets(tmp_path):
    from plugin_fork_io.snapshots import materialize_link

    (tmp_path / "file").write_bytes(b"original\r\n")
    (tmp_path / "alias").symlink_to("file")
    snap = capture(SourceSpec(str(tmp_path), "local"), (), services=Services.local())
    assert next(e for e in snap.entries if e.path == "alias").kind == "symlink"
    assert materialize_link(snap, "alias")[0].content == b"original\r\n"
    assert (tmp_path / "alias").is_symlink()


@pytest.mark.parametrize("target", ["missing", "../outside", "/absolute", "link"])
def test_refuses_dangling_escaping_and_cyclic_links(tmp_path, target):
    from plugin_fork_io.snapshots import materialize_link

    (tmp_path / "link").symlink_to(target)
    snap = capture(SourceSpec(str(tmp_path), "local"), (), services=Services.local())
    with pytest.raises(SnapshotError):
        materialize_link(snap, "link")


def test_selected_paths_cannot_traverse_links(tmp_path):
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "file").write_bytes(b"data")
    (tmp_path / "alias").symlink_to("dir", target_is_directory=True)
    with pytest.raises(SnapshotError):
        capture(SourceSpec(str(tmp_path), "local"), ("alias/file",), services=Services.local())


@pytest.mark.parametrize(
    "kind,name", [("file", "../escape"), ("file", "/absolute"), ("symlink", "safe"), ("duplicate", "safe")]
)
def test_archive_rejects_unsafe_members(tmp_path, kind, name):
    import io
    import tarfile

    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo(name)
        if kind == "symlink":
            member.type = tarfile.SYMTYPE
            member.linkname = "../escape"
        tar.addfile(member, io.BytesIO())
        if kind == "duplicate":
            tar.addfile(member, io.BytesIO())
    with pytest.raises(SnapshotError):
        read_snapshot(archive)
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize(
    "change", ["schema", "source", "label", "commit", "entries", "entry", "hash", "digest", "missing"]
)
def test_archive_validates_metadata_and_content(tmp_path, change):
    import io
    import json
    import tarfile

    (tmp_path / "data").write_bytes(b"bytes")
    snapshot = capture(SourceSpec(str(tmp_path), "local"), ("data",), services=Services.local())
    archive = tmp_path / "base.gz"
    write_snapshot(snapshot, archive)
    with tarfile.open(archive, "r:gz") as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar}
    metadata = json.loads(members["manifest.json"])
    if change == "schema":
        metadata["schema_version"] = 2
    elif change == "source":
        metadata["source"]["kind"] = "network"
    elif change == "label":
        metadata["source"]["label"] = []
    elif change == "commit":
        metadata["source"]["resolved_commit"] = "not-a-commit"
    elif change == "entries":
        metadata["entries"] = {}
    elif change == "entry":
        metadata["entries"][0]["mode"] = True
    elif change == "hash":
        members["objects/0"] = b"corrupted"
    elif change == "digest":
        metadata["digest"] = "wrong"
    else:
        members.pop("objects/0")
    members["manifest.json"] = json.dumps(metadata).encode()
    with tarfile.open(archive, "w:gz") as tar:
        for name, content in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            tar.addfile(member, io.BytesIO(content))
    with pytest.raises(SnapshotError):
        read_snapshot(archive)


@pytest.mark.parametrize(
    "entries",
    [
        [("a", "file", b"", 420), ("A", "file", b"", 420)],
        [("a", "fifo", b"", 420)],
        [("a", "directory", b"bad", 493)],
        [("a", "symlink", b"target", 511), ("a/b", "file", b"", 420)],
    ],
)
def test_refuses_ambiguous_snapshot_inventories(tmp_path, entries):
    from plugin_fork_io.records import Snapshot, SnapshotEntry, SourceIdentity

    snapshot = Snapshot(SourceIdentity("local", "source"), tuple(SnapshotEntry(p, c, k, m) for p, k, c, m in entries))
    with pytest.raises(SnapshotError):
        write_snapshot(snapshot, tmp_path / "bad.gz")


def test_link_directory_and_normalized_relative_target(tmp_path):
    from plugin_fork_io.snapshots import materialize_link

    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "data").write_bytes(b"data")
    (tmp_path / "dir" / "alias").symlink_to("../dir/./data")
    (tmp_path / "alias").symlink_to("dir", target_is_directory=True)
    snapshot = capture(SourceSpec(str(tmp_path), "local"), (), services=Services.local())
    assert [(e.path, e.content) for e in materialize_link(snapshot, "alias") if e.kind == "file"] == [
        ("alias/alias", b"data"),
        ("alias/data", b"data"),
    ]


def test_duplicate_selections_and_non_directory_root(tmp_path):
    with pytest.raises(SnapshotError):
        capture(SourceSpec(str(tmp_path), "local"), ("a", "a"), services=Services.local())
    file = tmp_path / "file"
    file.write_bytes(b"")
    with pytest.raises(SnapshotError):
        capture(SourceSpec(str(file), "local"), (), services=Services.local())


def test_git_without_runner_is_structured_failure(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.git import GitError

    with pytest.raises(GitError, match=r"git\.missing"):
        capture(SourceSpec(str(tmp_path), "git", "HEAD"), (), services=replace(Services.local(), git=None))


@pytest.mark.parametrize(
    "target,expected", [("dirlink/child", b"child bytes"), ("dirlink/../file", b"correct sub bytes")]
)
def test_intermediate_directory_links_resolve_before_parent_traversal(tmp_path, target, expected):
    from plugin_fork_io.snapshots import materialize_link

    (tmp_path / "sub" / "deep").mkdir(parents=True)
    (tmp_path / "sub" / "deep" / "child").write_bytes(b"child bytes")
    (tmp_path / "sub" / "file").write_bytes(b"correct sub bytes")
    (tmp_path / "file").write_bytes(b"wrong root bytes")
    (tmp_path / "dirlink").symlink_to("sub/deep", target_is_directory=True)
    (tmp_path / "alias").symlink_to(target)
    snapshot = capture(SourceSpec(str(tmp_path), "local"), (), services=Services.local())

    assert materialize_link(snapshot, "alias")[0].content == expected
    assert (tmp_path / "alias").read_bytes() == expected


@pytest.mark.parametrize("intermediate", ["missing", "../outside", "dirlink"])
def test_parent_traversal_does_not_erase_unsafe_intermediate_links(tmp_path, intermediate):
    from plugin_fork_io.snapshots import materialize_link

    (tmp_path / "file").write_bytes(b"must not substitute this")
    (tmp_path / "dirlink").symlink_to(intermediate, target_is_directory=True)
    (tmp_path / "alias").symlink_to("dirlink/../file")
    snapshot = capture(SourceSpec(str(tmp_path), "local"), (), services=Services.local())

    with pytest.raises(SnapshotError):
        materialize_link(snapshot, "alias")


def test_link_cannot_traverse_regular_file_even_before_parent_component(tmp_path):
    from plugin_fork_io.snapshots import materialize_link

    (tmp_path / "file").write_bytes(b"bytes")
    (tmp_path / "alias").symlink_to("file/../file")
    snapshot = capture(SourceSpec(str(tmp_path), "local"), (), services=Services.local())
    with pytest.raises(SnapshotError, match="non-directory"):
        materialize_link(snapshot, "alias")


def test_materialization_infers_omitted_selected_ancestors(tmp_path):
    from plugin_fork_io.snapshots import materialize_link

    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "file").write_bytes(b"bytes")
    (tmp_path / "dir" / "alias").symlink_to("file")
    snapshot = capture(SourceSpec(str(tmp_path), "local"), ("dir/file", "dir/alias"), services=Services.local())
    assert materialize_link(snapshot, "dir/alias")[0].content == b"bytes"


def test_link_to_ancestor_cannot_recursively_materialize_itself(tmp_path):
    from plugin_fork_io.snapshots import materialize_link

    (tmp_path / "alias").symlink_to(".", target_is_directory=True)
    snapshot = capture(SourceSpec(str(tmp_path), "local"), (), services=Services.local())
    with pytest.raises(SnapshotError, match="Cyclic"):
        materialize_link(snapshot, "alias")
