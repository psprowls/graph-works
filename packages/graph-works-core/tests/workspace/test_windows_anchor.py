"""Unit tests for `_WindowsAnchor`, the path-revalidating anchor tier.

These are the tier's unit tests; the engine-level coverage comes from
Task 11.  Most tests construct `_WindowsAnchor` directly for precise control
over its constructor arguments; a number of others go through
`open_anchor`/`anchor_tier` with `platform_name="win32"` now that Task 5 has
wired the selectors to return this tier.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

import pytest
from _transaction_helpers import _forced_tier, _plan, _snapshot, _workspace
from graph_works_core.workspace import anchors, transactions
from okf_io import load_bundle


def _anchor(root: Path) -> anchors._WindowsAnchor:
    return anchors._WindowsAnchor(root, long_paths=True)


def test_windows_anchor_satisfies_the_anchor_protocol(tmp_path: Path) -> None:
    """`Anchor` is `runtime_checkable`, so this is a real structural check."""
    anchor = _anchor(tmp_path)
    try:
        assert isinstance(anchor, anchors.Anchor)
    finally:
        anchor.close()


def test_windows_anchor_holds_a_resolved_root(tmp_path: Path) -> None:
    """A symlinked ANCESTOR of the root still resolves -- only the root's own
    final component being a symlink is refused (see
    `test_windows_anchor_refuses_a_symlinked_root`), matching how
    `_PosixAnchor.open_root` behaves when only an ancestor, not the immediate
    root, is a symlink."""
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    (tmp_path / "link" / "child").mkdir()
    anchor = _anchor(tmp_path / "link" / "child")
    try:
        assert anchor.root == (tmp_path / "real" / "child").resolve()
        assert anchor.path() == anchor.root
        assert anchor.alias() == anchor.root
    finally:
        anchor.close()


def test_windows_anchor_refuses_a_symlinked_root(tmp_path: Path) -> None:
    """Constructing directly on a path whose final component is a symlink is
    refused, matching `open_child`'s refusal of a symlinked component (see
    `test_open_child_refuses_a_symlinked_component`) -- just enforced at
    construction time and for the root itself.  `resolve()` would otherwise
    silently follow the link, so the check has to run against the unresolved
    path."""
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    with pytest.raises(NotADirectoryError, match="refusing to anchor a symlinked root"):
        anchors._WindowsAnchor(tmp_path / "link", long_paths=True)


def test_windows_anchor_refuses_a_root_that_is_not_a_directory(tmp_path: Path) -> None:
    target = tmp_path / "file.md"
    target.write_text("x\n", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        anchors._WindowsAnchor(target, long_paths=True)


def test_open_child_refuses_a_symlinked_component(tmp_path: Path) -> None:
    """The replacement for `O_NOFOLLOW`, which vanishes on Windows (L3)."""
    (tmp_path / "escape").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "escape", target_is_directory=True)
    anchor = _anchor(tmp_path)
    try:
        with pytest.raises(OSError):
            anchor.open_child("link")
    finally:
        anchor.close()


def test_open_child_raises_file_not_found_for_a_missing_component(tmp_path: Path) -> None:
    """`transactions._open_parent:436` catches FileNotFoundError specifically
    and treats every other OSError as an unsafe ancestor.  The Windows arm
    must preserve that distinction or the create-on-demand path breaks."""
    anchor = _anchor(tmp_path)
    try:
        with pytest.raises(FileNotFoundError):
            anchor.open_child("absent")
    finally:
        anchor.close()


def test_open_or_create_child_creates_then_reopens(tmp_path: Path) -> None:
    anchor = _anchor(tmp_path)
    try:
        child = anchor.open_or_create_child("lane")
        try:
            assert child.root == (tmp_path / "lane").resolve()
            assert (tmp_path / "lane").is_dir()
        finally:
            child.close()
        again = anchor.open_or_create_child("lane")
        again.close()
    finally:
        anchor.close()


def test_re_validation_catches_a_directory_swapped_under_the_anchor(tmp_path: Path) -> None:
    """L1: re-validation narrows the TOCTOU window; it must at least catch a
    swap between the anchor being taken and the next operation."""
    (tmp_path / "lane").mkdir()
    anchor = _anchor(tmp_path)
    try:
        child = anchor.open_child("lane")
        try:
            (tmp_path / "lane").rmdir()
            (tmp_path / "lane").mkdir()  # same path, new inode
            with pytest.raises(ValueError, match="changed during mutation"):
                child.listdir()
        finally:
            child.close()
    finally:
        anchor.close()


def test_rename_noreplace_refuses_to_clobber(tmp_path: Path) -> None:
    (tmp_path / "a").write_text("a\n", encoding="utf-8")
    (tmp_path / "b").write_text("b\n", encoding="utf-8")
    anchor = _anchor(tmp_path)
    try:
        with pytest.raises(FileExistsError):
            anchor.rename_noreplace("a", anchor, "b")
        assert (tmp_path / "a").read_text(encoding="utf-8") == "a\n"
        assert (tmp_path / "b").read_text(encoding="utf-8") == "b\n"
    finally:
        anchor.close()


def test_rename_noreplace_across_two_anchors(tmp_path: Path) -> None:
    (tmp_path / "from").mkdir()
    (tmp_path / "to").mkdir()
    (tmp_path / "from" / "page.md").write_text("x\n", encoding="utf-8")
    anchor = _anchor(tmp_path)
    try:
        source = anchor.open_child("from")
        destination = anchor.open_child("to")
        try:
            source.rename_noreplace("page.md", destination, "page.md")
            assert (tmp_path / "to" / "page.md").is_file()
        finally:
            source.close()
            destination.close()
    finally:
        anchor.close()


def test_fsync_is_a_documented_no_op(tmp_path: Path) -> None:
    """L2: directory fsync is impossible on Windows and this is where that
    fact lives.  `None` is the contract, not an accident."""
    anchor = _anchor(tmp_path)
    try:
        assert anchor.fsync() is None
    finally:
        anchor.close()


def test_symlink_raises_with_the_remedy_named(tmp_path: Path) -> None:
    anchor = _anchor(tmp_path)
    try:
        with pytest.raises(ValueError) as caught:
            anchor.symlink("target", "link")
        message = str(caught.value)
        assert "Developer Mode" in message
        assert "SeCreateSymbolicLinkPrivilege" in message
    finally:
        anchor.close()


def test_identity_and_lstat_agree(tmp_path: Path) -> None:
    (tmp_path / "page.md").write_text("x\n", encoding="utf-8")
    anchor = _anchor(tmp_path)
    try:
        info = anchor.lstat("page.md")
        assert anchor.identity("page.md") == (info.st_dev, info.st_ino)
    finally:
        anchor.close()


def test_open_absolute_walks_every_component(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    anchor = anchors._WindowsAnchor.open_absolute(nested)
    try:
        assert anchor.root == nested.resolve()
    finally:
        anchor.close()


def test_open_absolute_refuses_a_relative_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        anchors._WindowsAnchor.open_absolute(Path("relative/path"))


def test_the_windows_anchor_refuses_construction_without_long_path_support(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="long path support"):
        anchors._WindowsAnchor(tmp_path, long_paths=False)


def test_the_refusal_names_the_remedy_and_the_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as caught:
        anchors._WindowsAnchor(tmp_path, long_paths=False)
    message = str(caught.value)
    assert "LongPathsEnabled" in message
    assert "260" in message
    assert "WSL" in message


def test_long_paths_are_unconditionally_available_off_windows() -> None:
    assert anchors.long_paths_enabled() is True


def test_a_child_anchor_inherits_the_long_path_answer(tmp_path: Path) -> None:
    """`open_child` constructs a new anchor; it must not re-probe and it must
    not silently re-enable what the parent refused."""
    (tmp_path / "lane").mkdir()
    anchor = anchors._WindowsAnchor(tmp_path, long_paths=True)
    try:
        child = anchor.open_child("lane")
        try:
            assert child._long_paths is True
        finally:
            child.close()
    finally:
        anchor.close()


def test_the_windows_anchor_refuses_construction_without_hard_link_support(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="hard link support"):
        anchors._WindowsAnchor(tmp_path, hard_links=False)


def test_the_hard_link_refusal_names_the_path_and_the_alternative(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as caught:
        anchors._WindowsAnchor(tmp_path, hard_links=False)
    message = str(caught.value)
    assert str(tmp_path.resolve()) in message
    assert "exFAT" in message
    assert "WSL" in message
    assert message == anchors._hard_link_refusal_message(tmp_path.resolve())


def test_the_windows_anchor_constructs_normally_with_hard_link_support(tmp_path: Path) -> None:
    anchor = anchors._WindowsAnchor(tmp_path, hard_links=True)
    try:
        assert anchor._hard_links is True
    finally:
        anchor.close()


@pytest.mark.skipif(sys.platform == "win32", reason="constructs the strong tier, which needs os.O_DIRECTORY")
def test_refused_members_is_empty_on_the_strong_tier(tmp_path: Path) -> None:
    anchor = anchors.open_anchor(tmp_path, platform_name="linux")
    try:
        assert anchor.refused_members(["work/CON.md", "work/page.", "work/link.md"]) == ()
    finally:
        anchor.close()


def test_refused_members_names_every_symlink_at_once(tmp_path: Path) -> None:
    (tmp_path / "work").mkdir()
    (tmp_path / "target.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "work" / "a.md").symlink_to(tmp_path / "target.md")
    (tmp_path / "work" / "b.md").symlink_to(tmp_path / "target.md")
    anchor = _anchor(tmp_path)
    try:
        refusals = anchor.refused_members(["work/a.md", "work/b.md"])
        assert {item.member for item in refusals} == {"work/a.md", "work/b.md"}
        assert all("Developer Mode" in item.remedy for item in refusals)
    finally:
        anchor.close()


@pytest.mark.parametrize("member", ["work/CON.md", "work/nul.md", "work/COM1", "work/lpt9.md", "CON/page.md"])
def test_refused_members_catches_reserved_device_names(tmp_path: Path, member: str) -> None:
    anchor = _anchor(tmp_path)
    try:
        refusals = anchor.refused_members([member])
        assert len(refusals) == 1
        assert "reserved device name" in refusals[0].reason
    finally:
        anchor.close()


@pytest.mark.parametrize("member", ["work/page.", "work/page ", "work/trailing./child.md"])
def test_refused_members_catches_trailing_dots_and_spaces(tmp_path: Path, member: str) -> None:
    anchor = _anchor(tmp_path)
    try:
        refusals = anchor.refused_members([member])
        assert len(refusals) == 1
        assert "trailing dot or space" in refusals[0].reason
    finally:
        anchor.close()


def test_refused_members_accepts_ordinary_members(tmp_path: Path) -> None:
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "page.md").write_text("x\n", encoding="utf-8")
    anchor = _anchor(tmp_path)
    try:
        assert anchor.refused_members(["work/page.md", "work/lane"]) == ()
    finally:
        anchor.close()


def test_preflight_refuses_before_any_effect_lands(tmp_path: Path) -> None:
    """D-002's whole point: the refusal precedes the first mutation."""
    layout = _workspace(tmp_path)  # shared helper -- see Step 3
    (layout.bundle_dir / "work").mkdir(exist_ok=True)
    (layout.bundle_dir / "work" / "linked.md").symlink_to(layout.bundle_dir / "index.md")
    before = _snapshot(layout.bundle_dir)
    with _forced_tier("win32"):
        result = transactions.apply_mutation(layout, _plan(layout, deletes=("work/linked.md",)))
    assert not result.ok
    assert not result.rolled_back  # nothing was touched, so nothing was rolled back
    after = _snapshot(layout.bundle_dir)
    # `apply_mutation` takes the bundle-root lock -- creating BUNDLE_LOCK_NAME --
    # before `_preflight` runs, so the lock file's own appearance is expected
    # here and is not a bundle-content effect: it is exactly the artifact
    # `test_the_lock_file_never_enters_the_loaded_bundle` and
    # `test_the_lock_file_never_enters_a_manifest_digest` assert is invisible
    # to the bundle.  Strip it before comparing so this test still asserts
    # what it means to: no *bundle content* was touched.
    after.pop(anchors.BUNDLE_LOCK_NAME, None)
    assert after == before


def test_preflight_directly_refuses_a_symlink_member_on_the_windows_tier(tmp_path: Path) -> None:
    """Exercises `_preflight`'s call to `_refuse_unsupported_shapes` directly, without going
    through `apply_mutation`'s lock acquisition.
    """
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "work").mkdir(exist_ok=True)
    (layout.bundle_dir / "work" / "linked.md").symlink_to(layout.bundle_dir / "index.md")
    with _forced_tier("win32"), pytest.raises(ValueError, match="durability tier refuses"):
        transactions._preflight(layout, _plan(layout, deletes=("work/linked.md",)))


def test_the_bundle_lock_file_is_dot_prefixed_at_the_bundle_root() -> None:
    """ADR-0028's dot exclusion is root-scoped, so a root-level dotted name is
    already invisible to `load_bundle()`.  That is why this name was chosen."""
    assert anchors.BUNDLE_LOCK_NAME.startswith(".")
    assert "/" not in anchors.BUNDLE_LOCK_NAME


def test_exclusive_lock_creates_and_holds_the_lock_file(tmp_path: Path) -> None:
    anchor = _anchor(tmp_path)
    try:
        with anchor.exclusive_lock():
            assert (tmp_path / anchors.BUNDLE_LOCK_NAME).is_file()
    finally:
        anchor.close()


def test_exclusive_lock_refuses_a_root_that_is_no_longer_a_directory(tmp_path: Path) -> None:
    """Parity with `_PosixAnchor.exclusive_lock`'s `NotADirectoryError`, which
    raises on `__enter__` rather than on construction."""
    inner = tmp_path / "bundle"
    inner.mkdir()
    anchor = _anchor(inner)
    try:
        inner.rmdir()
        with pytest.raises(ValueError, match="changed during mutation"), anchor.exclusive_lock():
            pass
    finally:
        anchor.close()


def test_the_lock_file_never_enters_the_loaded_bundle(tmp_path: Path) -> None:
    """Both tiers must produce the same member set for identical content."""
    layout = _workspace(tmp_path)
    anchor = _anchor(layout.bundle_dir)
    try:
        with anchor.exclusive_lock():
            bundle = load_bundle(layout.bundle_dir)
        members = set(bundle.concepts) | bundle.assets
        assert anchors.BUNDLE_LOCK_NAME not in members
    finally:
        anchor.close()


def test_the_lock_file_never_enters_a_manifest_digest(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "work" / "lane").mkdir(parents=True, exist_ok=True)
    anchor = _anchor(layout.bundle_dir)
    try:
        before = transactions._entry_manifest_at(anchor, "work/lane", include_mode=True)
        with anchor.exclusive_lock():
            after = transactions._entry_manifest_at(anchor, "work/lane", include_mode=True)
        assert before == after
    finally:
        anchor.close()


def test_the_windows_lock_serializes_two_anchors_on_one_bundle(tmp_path: Path) -> None:
    """The weak tier's serialization guarantee, exercised in-process on POSIX."""
    layout = _workspace(tmp_path)
    first, second = _anchor(layout.bundle_dir), _anchor(layout.bundle_dir)
    order: list[str] = []
    entered = threading.Event()

    def _contend() -> None:
        entered.set()
        with second.exclusive_lock():
            order.append("second")

    try:
        with first.exclusive_lock():
            order.append("first")
            worker = threading.Thread(target=_contend)
            worker.start()
            entered.wait(timeout=5)
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert order == ["first", "second"]
    finally:
        first.close()
        second.close()


def test_lock_file_serializes_the_executor_lock(tmp_path: Path) -> None:
    anchor = _anchor(tmp_path)
    try:
        with anchor.lock_file("executor.lock", assert_identity=True):
            assert (tmp_path / "executor.lock").is_file()
    finally:
        anchor.close()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "swaps the lock file's inode while held via unlink+recreate; Windows' mandatory locking "
        "raises PermissionError on the unlink of a locked, open file, unlike POSIX advisory locking"
    ),
)
def test_lock_file_detects_a_swapped_lock_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`assert_identity=True` is what the engine always passes.  Parity with
    the POSIX arm's `_assert_regular_entry_identity` call: the file is
    replaced between the lock being taken and the identity being checked, and
    the mismatch must be caught rather than silently locking a dead inode."""
    anchor = _anchor(tmp_path)
    target = tmp_path / "executor.lock"
    target.write_text("", encoding="utf-8")
    real_locked = anchors.locked

    @contextmanager
    def swap_then_lock(path: Path, **kwargs: object) -> Iterator[None]:
        with real_locked(path, **kwargs):  # type: ignore[arg-type]
            path.unlink()
            path.write_text("", encoding="utf-8")  # same name, new inode
            yield

    monkeypatch.setattr(anchors, "locked", swap_then_lock)
    try:
        with (
            pytest.raises(ValueError, match="changed during mutation"),
            anchor.lock_file("executor.lock", assert_identity=True),
        ):
            pass
    finally:
        anchor.close()


def test_nofollow_availability_is_stated_rather_than_inferred() -> None:
    assert anchors.NOFOLLOW_AVAILABLE is hasattr(os, "O_NOFOLLOW")


def test_the_windows_tier_still_refuses_a_symlinked_ancestor_without_o_nofollow(tmp_path: Path) -> None:
    """The flag is gone; the refusal is not.  `open_child` lstats every
    component, which is a check rather than a kernel guarantee -- weaker, and
    ADR-0042 says so."""
    (tmp_path / "escape").mkdir()
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "link").symlink_to(tmp_path / "escape", target_is_directory=True)
    anchor = _anchor(tmp_path)
    try:
        with pytest.raises(ValueError, match="unsafe ancestor"):
            transactions._open_parent(anchor, "work/link/page.md")
    finally:
        anchor.close()


@pytest.mark.skipif(sys.platform == "win32", reason="constructs the strong tier, which needs os.O_DIRECTORY")
def test_directory_fsync_is_honored_only_on_the_strong_tier(tmp_path: Path) -> None:
    posix = anchors.open_anchor(tmp_path, platform_name="linux")
    windows = anchors.open_anchor(tmp_path, platform_name="win32")
    try:
        assert posix.fsync() is None and windows.fsync() is None  # both return None...
        assert anchors.DIRECTORY_FSYNC_HONORED is (sys.platform != "win32")
    finally:
        posix.close()
        windows.close()


def test_a_moved_symlink_escaping_the_bundle_is_refused_on_the_windows_tier(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "work").mkdir(exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (layout.bundle_dir / "work" / "escape.md").symlink_to(outside / "target.md")
    anchor = _anchor(layout.bundle_dir)
    try:
        assert not transactions._projected_symlink_is_internal(
            anchor, "work/moved.md", PurePosixPath((layout.bundle_dir / "work" / "escape.md").readlink())
        )
    finally:
        anchor.close()


def test_an_internal_moved_symlink_is_accepted_on_the_windows_tier(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "work").mkdir(exist_ok=True)
    (layout.bundle_dir / "work" / "inside.md").symlink_to("index.md")
    anchor = _anchor(layout.bundle_dir)
    try:
        assert transactions._projected_symlink_is_internal(anchor, "work/moved.md", PurePosixPath("index.md"))
    finally:
        anchor.close()


@pytest.mark.skipif(sys.platform == "win32", reason="constructs the strong tier, which needs os.O_DIRECTORY")
def test_both_tiers_agree_on_the_same_projection(tmp_path: Path) -> None:
    """The strongest form of this test: the two anchors must not disagree
    about whether a given symlink escapes."""
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "work").mkdir(exist_ok=True)
    (layout.bundle_dir / "work" / "inside.md").symlink_to("index.md")
    posix = anchors.open_anchor(layout.bundle_dir, platform_name="linux")
    windows = anchors.open_anchor(layout.bundle_dir, platform_name="win32")
    try:
        for link in (PurePosixPath("index.md"), PurePosixPath("../outside.md")):
            assert transactions._projected_symlink_is_internal(
                posix, "work/moved.md", link
            ) is transactions._projected_symlink_is_internal(windows, "work/moved.md", link)
    finally:
        posix.close()
        windows.close()


@pytest.mark.skipif(sys.platform == "win32", reason="constructs the strong tier, which needs os.O_DIRECTORY")
def test_both_tiers_agree_on_the_same_projection_when_the_target_exists(tmp_path: Path) -> None:
    """Same claim as above, but for the `.resolve(strict=True)` SUCCESS arm.

    The other test's targets are never materialized, so both tiers only ever
    exercise the `FileNotFoundError` -> non-strict-resolve fallback. Here the
    projected target physically exists for both the internal and the
    external case, so this is the first test to demonstrate tier agreement
    on the strict-resolve success path itself.
    """
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "work").mkdir(exist_ok=True)
    (layout.bundle_dir / "work" / "inside.md").symlink_to("index.md")
    (layout.bundle_dir / "work" / "index.md").write_text("x\n")  # internal target: real, matches the projection
    assert (layout.bundle_dir / "work" / "index.md").exists()
    (layout.bundle_dir.parent / "outside.md").write_text("outside\n")  # external target: real, on purpose
    posix = anchors.open_anchor(layout.bundle_dir, platform_name="linux")
    windows = anchors.open_anchor(layout.bundle_dir, platform_name="win32")
    try:
        # "work/../../outside.md" is the genuinely-external one: "work/.." cancels
        # to the bundle root, and the remaining ".." steps out of it -- unlike
        # the sibling test's "../outside.md", which only cancels back to the
        # bundle root itself and so never actually escapes.
        for link in (PurePosixPath("index.md"), PurePosixPath("../../outside.md")):
            posix_result = transactions._projected_symlink_is_internal(posix, "work/moved.md", link)
            windows_result = transactions._projected_symlink_is_internal(windows, "work/moved.md", link)
            assert posix_result is windows_result
        assert transactions._projected_symlink_is_internal(posix, "work/moved.md", PurePosixPath("index.md"))
        assert not transactions._projected_symlink_is_internal(
            posix, "work/moved.md", PurePosixPath("../../outside.md")
        )
    finally:
        posix.close()
        windows.close()


def test_validation_state_loads_through_either_tier(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    windows = anchors.open_anchor(layout.bundle_dir, platform_name="win32")
    try:
        state = transactions._capture_validation_state(layout, _plan(layout), windows, repo_root=None)
        assert isinstance(state.findings, dict)
    finally:
        windows.close()


def test_raw_descriptor_still_refuses_a_path_revalidated_anchor(tmp_path: Path) -> None:
    """The escape hatch keeps its guard; it just has one consumer now."""
    anchor = anchors.open_anchor(tmp_path, platform_name="win32")
    try:
        with pytest.raises(anchors.UnsupportedAnchorPlatform):
            transactions._raw_descriptor(anchor)
    finally:
        anchor.close()


@pytest.mark.parametrize("platform_name", ["linux", "darwin", "win32"])
def test_every_tier_record_answers_all_seven_questions(platform_name: str) -> None:
    tier = anchors.durability_tier(platform_name)
    assert tier.name == anchors.anchor_tier(platform_name)
    assert tier.anchoring
    assert isinstance(tier.directory_fsync, bool)
    assert isinstance(tier.nofollow_protection, bool)
    assert isinstance(tier.refused_plan_shapes, tuple)
    assert tier.filesystem_requirement
    assert tier.verification_status


def test_the_strong_tier_refuses_no_plan_shapes() -> None:
    tier = anchors.durability_tier("linux")
    assert tier.refused_plan_shapes == ()
    assert tier.directory_fsync is True
    assert tier.nofollow_protection is True


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the constants read the real host's sys.platform; this check is meaningful on a POSIX host only",
)
def test_the_posix_tier_record_traces_to_the_module_constants_it_claims_to_mirror() -> None:
    """`DurabilityTier`'s docstring claims every field derives from a module constant.
    `directory_fsync`/`nofollow_protection` are literals, not live references (see the
    module docstring for why -- `_WINDOWS_TIER`'s equivalents can't literally reference
    these constants, since the constants read the REAL host's `sys.platform`, not the
    simulated `platform_name` a Windows-tier query might be asked about). This closes the
    silent-drift risk for the one tier record that CAN be checked directly: on a real
    POSIX host, `_POSIX_TIER`'s two literals must agree with what the constants say.
    """
    assert anchors._POSIX_TIER.directory_fsync is anchors.DIRECTORY_FSYNC_HONORED
    assert anchors._POSIX_TIER.nofollow_protection is anchors.NOFOLLOW_AVAILABLE


def test_the_weak_tier_reports_its_refusals_as_normal_output() -> None:
    """A refusal is a contract statement, not an incident (D-002)."""
    tier = anchors.durability_tier("win32")
    assert tier.name == anchors.WINDOWS_REVALIDATED_TIER
    assert any("symlink" in shape for shape in tier.refused_plan_shapes)
    assert any("reserved device name" in shape for shape in tier.refused_plan_shapes)
    assert any("trailing dot" in shape for shape in tier.refused_plan_shapes)
    assert tier.directory_fsync is False
    assert tier.nofollow_protection is False
    assert "NTFS" in tier.filesystem_requirement
    assert "unverified" in tier.verification_status


def test_the_declared_refusals_match_what_the_anchor_actually_refuses(tmp_path: Path) -> None:
    """The record is not allowed to drift from the code it describes."""
    (tmp_path / "CON.md").write_text("x\n", encoding="utf-8")
    refusals = _anchor(tmp_path).refused_members(["CON.md"])
    declared = anchors.durability_tier("win32").refused_plan_shapes
    assert any("reserved device name" in shape for shape in declared)
    assert "reserved device name" in refusals[0].reason


def test_hard_links_are_unconditionally_available_off_windows() -> None:
    assert anchors.hard_links_supported(Path("/nonexistent/anywhere")) is True


def test_the_hard_link_refusal_message_names_the_requirement_and_the_path(tmp_path: Path) -> None:
    message = anchors._hard_link_refusal_message(tmp_path)
    assert "hard link support" in message
    assert str(tmp_path) in message
    assert "exFAT" in message
    assert "WSL" in message
    assert "posix-strong" in message


def test_a_child_anchor_inherits_the_hard_link_answer(tmp_path: Path) -> None:
    """Mirrors test_a_child_anchor_inherits_the_long_path_answer: open_child
    must not re-probe and must not silently re-enable what the parent refused."""
    (tmp_path / "lane").mkdir()
    anchor = anchors._WindowsAnchor(tmp_path, long_paths=True, hard_links=True)
    try:
        child = anchor.open_child("lane")
        try:
            assert child._hard_links is True
        finally:
            child.close()
    finally:
        anchor.close()


def test_a_duplicate_anchor_inherits_the_hard_link_answer(tmp_path: Path) -> None:
    anchor = anchors._WindowsAnchor(tmp_path, long_paths=True, hard_links=True)
    try:
        copy = anchor.duplicate()
        try:
            assert copy._hard_links is True
        finally:
            copy.close()
    finally:
        anchor.close()


def test_open_child_does_not_reprobe_hard_link_support(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "lane").mkdir()
    calls: list[Path] = []
    original = anchors.hard_links_supported

    def _spy(path: Path) -> bool:
        calls.append(path)
        return original(path)

    monkeypatch.setattr(anchors, "hard_links_supported", _spy)
    anchor = anchors._WindowsAnchor(tmp_path, long_paths=True, hard_links=True)
    try:
        calls.clear()
        child = anchor.open_child("lane")
        try:
            assert calls == []
        finally:
            child.close()
    finally:
        anchor.close()


def test_link_translates_error_invalid_function_into_the_named_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "source.txt").write_text("x\n", encoding="utf-8")
    anchor = anchors._WindowsAnchor(tmp_path, long_paths=True, hard_links=True)
    try:

        def _raise_invalid_function(*args: object, **kwargs: object) -> None:
            raise OSError(0, "Incorrect function", None, anchors.ERROR_INVALID_FUNCTION)

        monkeypatch.setattr(anchors.os, "link", _raise_invalid_function)
        with pytest.raises(ValueError) as caught:
            anchor.link("source.txt", "dest.txt")
        assert str(caught.value) == anchors._hard_link_refusal_message(anchor.root)
    finally:
        anchor.close()


def test_link_translates_error_not_supported_into_the_named_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "source.txt").write_text("x\n", encoding="utf-8")
    anchor = anchors._WindowsAnchor(tmp_path, long_paths=True, hard_links=True)
    try:

        def _raise_not_supported(*args: object, **kwargs: object) -> None:
            raise OSError(0, "Not supported", None, anchors.ERROR_NOT_SUPPORTED)

        monkeypatch.setattr(anchors.os, "link", _raise_not_supported)
        with pytest.raises(ValueError) as caught:
            anchor.link("source.txt", "dest.txt")
        assert str(caught.value) == anchors._hard_link_refusal_message(anchor.root)
    finally:
        anchor.close()


def test_link_lets_file_exists_error_propagate_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "source.txt").write_text("x\n", encoding="utf-8")
    anchor = anchors._WindowsAnchor(tmp_path, long_paths=True, hard_links=True)
    try:

        def _raise_exists(*args: object, **kwargs: object) -> None:
            raise FileExistsError(17, "File exists")

        monkeypatch.setattr(anchors.os, "link", _raise_exists)
        with pytest.raises(FileExistsError):
            anchor.link("source.txt", "dest.txt")
    finally:
        anchor.close()


def test_link_lets_unrelated_oserrors_propagate_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "source.txt").write_text("x\n", encoding="utf-8")
    anchor = anchors._WindowsAnchor(tmp_path, long_paths=True, hard_links=True)
    try:

        def _raise_other(*args: object, **kwargs: object) -> None:
            raise OSError(0, "Access is denied", None, 5)  # ERROR_ACCESS_DENIED

        monkeypatch.setattr(anchors.os, "link", _raise_other)
        with pytest.raises(OSError, match="Access is denied"):
            anchor.link("source.txt", "dest.txt")
    finally:
        anchor.close()
