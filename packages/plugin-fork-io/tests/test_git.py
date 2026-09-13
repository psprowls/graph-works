from plugin_fork_io.git import GitError, LocalGitRunner


def test_missing_git(tmp_path):
    import pytest

    with pytest.raises(GitError, match=r"git\.missing"):
        LocalGitRunner(executable=str(tmp_path / "absent")).acquire(str(tmp_path), "HEAD")


def git(root, *args):
    import subprocess

    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout.strip()


def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    (root / "script").write_bytes(b"\x00\xff\r\n")
    (root / ".gitattributes").write_bytes(b"script filter=poison\n")
    git(root, "add", ".")
    git(root, "update-index", "--chmod=+x", "script")
    git(root, "commit", "-m", "initial")
    return root


def test_tags_modes_binary_and_branch_movement(tmp_path):
    from plugin_fork_io.machine import Services
    from plugin_fork_io.records import SourceSpec
    from plugin_fork_io.snapshots import capture

    root = repository(tmp_path)
    commit = git(root, "rev-parse", "HEAD").decode()
    git(root, "tag", "light")
    git(root, "tag", "-a", "annotated", "-m", "tag")
    for tag in ("light", "annotated", "HEAD"):
        snapshot = capture(SourceSpec(str(root), "git", tag), ("script",), services=Services.local())
        assert snapshot.source.resolved_commit == commit
        assert snapshot.entries[0].mode == 0o755
        assert snapshot.entries[0].content == b"\x00\xff\r\n"
    (root / "script").write_bytes(b"changed")
    git(root, "add", ".")
    git(root, "commit", "-m", "move")
    assert snapshot.source.resolved_commit == commit
    assert snapshot.entries[0].content == b"\x00\xff\r\n"


def test_git_ignores_source_filters_hooks_and_environment(tmp_path, monkeypatch):
    root = repository(tmp_path)
    marker = tmp_path / "executed"
    hook = root / ".git" / "hooks" / "post-checkout"
    hook.write_bytes(f'#!/bin/sh\ntouch "{marker}"\n'.encode())
    hook.chmod(0o755)
    git(root, "config", "filter.poison.smudge", f'touch "{marker}"')
    git(root, "config", "filter.poison.required", "true")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.sshCommand")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", f'touch "{marker}"')
    snapshot = LocalGitRunner().acquire(str(root), "HEAD")
    assert next(e for e in snapshot.entries if e.path == "script").content == b"\x00\xff\r\n"
    assert not marker.exists()


def test_bad_revision(tmp_path):
    import pytest

    root = repository(tmp_path)
    with pytest.raises(GitError, match=r"git\.revision"):
        LocalGitRunner().acquire(str(root), "missing")


def test_unsafe_transports():
    import pytest
    from plugin_fork_io.git import validate_locator

    for locator in ("-x", "ext::sh command", "file:///tmp/repo", "ftp://host/repo", ""):
        with pytest.raises(GitError):
            validate_locator(locator)


def test_supported_transport_validation():
    from plugin_fork_io.git import validate_locator

    for locator in ("https://host/repo", "ssh://user@host/repo", "git@host:repo"):
        assert validate_locator(locator) == locator


def test_option_like_revision_is_refused(tmp_path):
    import pytest

    with pytest.raises(GitError, match=r"git\.revision"):
        LocalGitRunner().acquire(str(tmp_path), "--upload-pack=evil")


def test_git_timeout_is_structured(tmp_path, monkeypatch):
    import subprocess

    import pytest

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("git", 60)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(GitError, match=r"git\.failed"):
        LocalGitRunner().acquire(str(tmp_path), "HEAD")


def test_git_snapshot_roundtrip_inspection_and_missing_selection(tmp_path):
    import pytest
    from helpers import write_skill
    from plugin_fork_io import inspect_source
    from plugin_fork_io.machine import Services
    from plugin_fork_io.records import SourceSpec
    from plugin_fork_io.snapshots import SnapshotError, capture, read_snapshot, write_snapshot

    root = repository(tmp_path)
    write_skill(root, "review")
    (root / "alias").symlink_to("script")
    git(root, "add", ".")
    git(root, "commit", "-m", "skill and link")
    source = SourceSpec(str(root), "git", "HEAD")
    snapshot = capture(source, (), services=Services.local())
    assert next(e for e in snapshot.entries if e.path == "alias").kind == "symlink"
    archive = tmp_path / "base.gz"
    write_snapshot(snapshot, archive)
    assert read_snapshot(archive) == snapshot
    assert inspect_source(source, services=Services.local()).allowed
    with pytest.raises(SnapshotError):
        capture(source, ("absent",), services=Services.local())


def test_submodules_are_not_recursed(tmp_path):
    import pytest

    root = repository(tmp_path)
    commit = git(root, "rev-parse", "HEAD").decode()
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{commit},submodule")
    git(root, "commit", "-m", "submodule")
    with pytest.raises(GitError, match=r"git\.failed"):
        LocalGitRunner().acquire(str(root), "HEAD")


def test_git_links_to_directories_can_be_materialized(tmp_path):
    from plugin_fork_io.snapshots import materialize_link

    root = repository(tmp_path)
    (root / "dir").mkdir()
    (root / "dir" / "file").write_bytes(b"content")
    (root / "alias").symlink_to("dir", target_is_directory=True)
    git(root, "add", ".")
    git(root, "commit", "-m", "directory link")
    snapshot = LocalGitRunner().acquire(str(root), "HEAD")
    assert materialize_link(snapshot, "alias")[-1].content == b"content"


def test_transport_failure_differs_from_missing_revision(tmp_path):
    import pytest

    with pytest.raises(GitError) as caught:
        LocalGitRunner().acquire(str(tmp_path / "absent"), "HEAD")
    assert caught.value.code == "git.failed"
