"""`write_all` on its own terms: the probe, the staging abort, the isolated commit."""

from __future__ import annotations

import pytest
from okf_ext.writing import ApplyResult, PendingWrite, Skipped, WriteFailure, write_all


def pending(tmp_path, name, text="new\n", commits=None):
    target = tmp_path / name
    target.write_text("old\n", encoding="utf-8")
    return PendingWrite(
        member=name,
        path=target,
        rendered=text,
        on_written=(lambda: commits.append(name)) if commits is not None else (lambda: None),
    )


def test_an_empty_batch_writes_nothing():
    result = write_all([])
    assert result == ApplyResult(written=(), failed=(), skipped=())
    assert result.ok


def test_content_failures_and_skips_are_carried_through(tmp_path):
    failure = WriteFailure(path="b.md", kind="parse-error", error="boom")
    skip = Skipped(concept_id="c", path="c.md", reason="parse-error", detail="boom")
    result = write_all([pending(tmp_path, "a.md")], failed=[failure], skipped=[skip])
    assert result.written == ("a.md",)
    assert result.failed == (failure,)
    assert result.skipped == (skip,)
    assert not result.ok


def test_every_write_lands_and_on_written_runs_once_each(tmp_path):
    commits: list[str] = []
    items = [pending(tmp_path, "a.md", "A\n", commits), pending(tmp_path, "b.md", "B\n", commits)]
    result = write_all(items)
    assert result.written == ("a.md", "b.md")
    assert (tmp_path / "a.md").read_text() == "A\n"
    assert (tmp_path / "b.md").read_text() == "B\n"
    assert commits == ["a.md", "b.md"]


def test_an_unwritable_target_blocks_the_whole_batch(tmp_path):
    items = [pending(tmp_path, "a.md", "A\n"), pending(tmp_path, "b.md", "B\n")]
    items[1].path.chmod(0o400)
    try:
        result = write_all(items)
    finally:
        items[1].path.chmod(0o644)
    assert result.written == ()
    assert [(f.path, f.kind) for f in result.failed] == [("b.md", "unwritable")]
    assert (tmp_path / "a.md").read_text() == "old\n"


def test_a_missing_target_is_reported_as_unwritable(tmp_path):
    item = pending(tmp_path, "a.md")
    item.path.unlink()
    result = write_all([item])
    assert [(f.path, f.kind) for f in result.failed] == [("a.md", "unwritable")]


def test_a_staging_failure_aborts_the_batch_and_leaves_no_temp_file(tmp_path, monkeypatch):
    items = [pending(tmp_path, "a.md", "A\n"), pending(tmp_path, "b.md", "B\n")]
    real = type(items[0].path).write_bytes

    def flaky(self, data):
        if self.name.startswith(".b.md"):
            raise OSError("no space left on device")
        return real(self, data)

    monkeypatch.setattr(type(items[0].path), "write_bytes", flaky)
    result = write_all(items)
    assert result.written == ()
    assert [(f.path, f.kind) for f in result.failed] == [("b.md", "stage-error")]
    assert (tmp_path / "a.md").read_text() == "old\n"
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_a_staging_cleanup_failure_does_not_mask_the_original_error(tmp_path, monkeypatch):
    item = pending(tmp_path, "a.md")
    path_type = type(item.path)
    monkeypatch.setattr(path_type, "write_bytes", lambda self, data: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(path_type, "unlink", lambda self, missing_ok=False: (_ for _ in ()).throw(OSError("nope")))
    result = write_all([item])
    assert [(f.path, f.kind) for f in result.failed] == [("a.md", "stage-error")]
    assert "disk full" in result.failed[0].error


def test_a_commit_failure_is_isolated_and_its_temp_file_is_cleaned_up(tmp_path, monkeypatch):
    commits: list[str] = []
    items = [
        pending(tmp_path, "a.md", "A\n", commits),
        pending(tmp_path, "b.md", "B\n", commits),
        pending(tmp_path, "c.md", "C\n", commits),
    ]
    real = type(items[0].path).replace

    def flaky(self, target):
        if getattr(target, "name", "") == "b.md":
            raise OSError("cross-device link")
        return real(self, target)

    monkeypatch.setattr(type(items[0].path), "replace", flaky)
    result = write_all(items)
    assert result.written == ("a.md", "c.md")
    assert [(f.path, f.kind) for f in result.failed] == [("b.md", "commit-error")]
    assert commits == ["a.md", "c.md"]
    assert (tmp_path / "b.md").read_text() == "old\n"
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_a_commit_cleanup_failure_does_not_mask_the_original_error(tmp_path, monkeypatch):
    item = pending(tmp_path, "a.md")
    path_type = type(item.path)
    monkeypatch.setattr(path_type, "replace", lambda self, target: (_ for _ in ()).throw(OSError("EXDEV")))
    monkeypatch.setattr(path_type, "unlink", lambda self, missing_ok=False: (_ for _ in ()).throw(OSError("nope")))
    result = write_all([item])
    assert [(f.path, f.kind) for f in result.failed] == [("a.md", "commit-error")]
    assert "EXDEV" in result.failed[0].error


def test_an_on_written_failure_propagates_and_cleans_up_queued_temp_files(tmp_path):
    commits: list[str] = []
    a = pending(tmp_path, "a.md", "A\n", commits)
    c = pending(tmp_path, "c.md", "C\n", commits)

    def boom():
        raise RuntimeError("caller bug")

    b = PendingWrite(member="b.md", path=tmp_path / "b.md", rendered="B\n", on_written=boom)
    (tmp_path / "b.md").write_text("old\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="caller bug"):
        write_all([a, b, c])

    assert commits == ["a.md"]
    assert (tmp_path / "a.md").read_text() == "A\n"
    # b's own bytes land -- the rename that precedes the callback already
    # succeeded -- even though the callback itself blew up.
    assert (tmp_path / "b.md").read_text() == "B\n"
    # c was never reached by the commit loop, so its content is untouched...
    assert (tmp_path / "c.md").read_text() == "old\n"
    # ...but its staged temp file must not survive the abandoned batch.
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_failures_are_sorted_by_path(tmp_path):
    items = [pending(tmp_path, "b.md"), pending(tmp_path, "a.md")]
    for item in items:
        item.path.chmod(0o400)
    try:
        result = write_all(items, failed=[WriteFailure(path="z.md", kind="stale", error="x")])
    finally:
        for item in items:
            item.path.chmod(0o644)
    assert [f.path for f in result.failed] == ["a.md", "b.md", "z.md"]


@pytest.mark.parametrize("reason", ["parse-error", "section-missing", "tags-not-a-sequence", "unreadable"])
def test_every_skip_reason_is_constructible(reason):
    assert Skipped(concept_id="c", path="c.md", reason=reason, detail="d").reason == reason


def test_write_all_commits_in_the_order_it_is_given(tmp_path):
    """Spec §10 of the moves item: `moves` commits destinations before
    referrers, and that invariant rests on this loop preserving caller order
    rather than sorting. Sorting would be a strictly stronger constraint that
    happens to break it, so the guarantee is tested, not assumed.
    """
    order = ["zeta.md", "alpha.md", "mid.md"]
    pending_list = []
    for member in order:
        target = tmp_path / member
        target.write_text("before\n", encoding="utf-8")
        pending_list.append(PendingWrite(member=member, path=target, rendered="after\n", on_written=lambda: None))

    result = write_all(pending_list)

    assert result.ok
    assert list(result.written) == order, "write_all must not sort; moves depends on caller order"
