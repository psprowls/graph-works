"""Unit tests for `_WindowsAnchor`, the path-revalidating anchor tier.

These are the tier's unit tests; the engine-level coverage comes from
Task 11.  Most tests construct `_WindowsAnchor` directly for precise control
over its constructor arguments; a number of others go through
`open_anchor`/`anchor_tier` with `platform_name="win32"` now that Task 5 has
wired the selectors to return this tier.
"""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

import pytest
from gw_kernel.testing.transactions import plan as _plan
from gw_kernel.testing.transactions import snapshot as _snapshot
from gw_kernel.testing.transactions import workspace as _workspace
from gw_kernel.workspace import anchors
from gw_pipeline import transactions
from gw_pipeline.testing import forced_tier as _forced_tier


def test_preflight_refuses_before_any_effect_lands(tmp_path: Path) -> None:
    """D-002's whole point: the refusal precedes the first mutation."""
    layout = _workspace(tmp_path)  # shared helper -- see Step 3
    (layout.bundle_dir / "work").mkdir(exist_ok=True)
    (layout.bundle_dir / "work" / "linked.md").symlink_to(layout.bundle_dir / "index.md")
    before = _snapshot(layout.bundle_dir)
    with _forced_tier("win32"):
        result = transactions.apply_mutation(layout, _plan(layout, deletes=("work/linked.md",)), commit=None)
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


def _anchor(root: Path) -> anchors._WindowsAnchor:
    return anchors._WindowsAnchor(root, long_paths=True)


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
