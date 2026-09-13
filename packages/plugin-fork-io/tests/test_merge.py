from dataclasses import replace

import pytest
from plugin_fork_io import Services
from plugin_fork_io.merge import merge_entry
from plugin_fork_io.records import SnapshotEntry


def test_three_way_nonoverlapping_text():
    from plugin_fork_io.merge import merge_text

    content, conflicted = merge_text(b"a\nb\nc\n", b"local\nb\nc\n", b"a\nb\nincoming\n", services=Services.local())
    assert content == b"local\nb\nincoming\n"
    assert not conflicted


def entry(content, mode=0o644):
    return None if content is None else SnapshotEntry("file", content, "file", mode)


@pytest.mark.parametrize(
    "base,local,incoming,expected,code",
    [
        (b"base", b"base", b"new", b"new", None),
        (b"base", b"local", b"base", b"local", None),
        (b"base", None, None, None, None),
        (None, b"a", b"b", b"a", "merge.add-add"),
        (b"base", b"local", None, b"local", "merge.modify-delete"),
        (b"base", None, b"new", None, "merge.modify-delete"),
        (b"\0base", b"\0local", b"\0new", b"\0local", "merge.binary"),
        (b"\0base", b"\0new", b"\0new", b"\0new", None),
        (None, b"local", None, b"local", None),
        (None, None, b"new", b"new", None),
    ],
)
def test_entry_matrix(base, local, incoming, expected, code):
    result, conflicts = merge_entry("file", entry(base), entry(local), entry(incoming), services=Services.local())
    assert (result.content if result else None) == expected
    assert [c.code for c in conflicts] == ([code] if code else [])
    if conflicts:
        conflict = conflicts[0]
        assert conflict.base_hash == (entry(base).hash if base is not None else None)
        assert conflict.local_hash == (entry(local).hash if local is not None else None)
        assert conflict.incoming_hash == (entry(incoming).hash if incoming is not None else None)
        assert "Explicit" in conflict.resolution


def test_permissions_merge_separately_from_content():
    result, conflicts = merge_entry(
        "file", entry(b"base"), entry(b"local"), entry(b"base", 0o755), services=Services.local()
    )
    assert result.content == b"local" and result.mode == 0o755
    assert not conflicts
    result, conflicts = merge_entry(
        "file", entry(b"base"), entry(b"base", 0o600), entry(b"base", 0o755), services=Services.local()
    )
    assert result.mode == 0o600
    assert [c.code for c in conflicts] == ["merge.mode"]


def test_text_conflict_and_crlf_are_preserved():
    content, conflict = __import__("plugin_fork_io.merge", fromlist=["merge_text"]).merge_text(
        b"base\r\n", b"local\r\n", b"incoming\r\n", services=Services.local()
    )
    assert conflict and b"local\r\n" in content and b"incoming\r\n" in content


def test_git_failures_are_not_empty_successes(tmp_path):
    from plugin_fork_io.git import GitError, LocalGitRunner
    from plugin_fork_io.merge import merge_text

    with pytest.raises(GitError, match=r"git\.missing"):
        merge_text(
            b"base",
            b"local",
            b"incoming",
            services=replace(Services.local(), git=LocalGitRunner(str(tmp_path / "absent"))),
        )
    with pytest.raises(GitError, match=r"git\.failed"):
        merge_text(b"base", b"local", b"incoming", services=replace(Services.local(), git=LocalGitRunner(timeout=0)))
