"""Unit tests for the directory-anchor seam.

Every selector test injects `platform_name` explicitly rather than
monkeypatching `sys.platform`, which is the whole point of the argument: a
POSIX box asserts the Windows arm without a Windows box, and neither arm is
dead lines against the 95% floor.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import os
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from graph_works_core.util.platform import POSIX_ONLY_MODULES
from graph_works_core.workspace import anchors


@pytest.mark.skipif(sys.platform == "win32", reason="constructs _PosixAnchor, which needs os.O_DIRECTORY")
def test_open_anchor_selects_the_posix_implementation_and_reports_its_tier(tmp_path: Path) -> None:
    anchor = anchors.open_anchor(tmp_path, platform_name="linux")
    try:
        assert isinstance(anchor, anchors._PosixAnchor)
        assert anchors.anchor_tier("linux") == anchors.POSIX_STRONG_TIER
        assert anchors.anchor_tier("darwin") == anchors.POSIX_STRONG_TIER
    finally:
        anchor.close()


def test_raw_descriptor_refuses_a_windows_anchor(tmp_path: Path) -> None:
    """`UnsupportedAnchorPlatform` still fires -- now only from the one call
    site (`transactions._raw_descriptor`) that genuinely cannot take a
    `_WindowsAnchor`, not from the selectors themselves."""
    from graph_works_core.workspace import transactions

    anchor = anchors.open_anchor(tmp_path, platform_name="win32")
    try:
        with pytest.raises(anchors.UnsupportedAnchorPlatform, match="no descriptor to lend"):
            transactions._raw_descriptor(anchor)
    finally:
        anchor.close()


@pytest.mark.parametrize("platform_name", ["win32", "cygwin", "windows"])
def test_a_windows_platform_selects_the_revalidated_tier(platform_name: str) -> None:
    assert anchors.anchor_tier(platform_name) == anchors.WINDOWS_REVALIDATED_TIER


@pytest.mark.parametrize("platform_name", ["linux", "darwin", "freebsd13"])
def test_a_posix_platform_still_selects_the_strong_tier(platform_name: str) -> None:
    assert anchors.anchor_tier(platform_name) == anchors.POSIX_STRONG_TIER


def test_open_anchor_returns_the_windows_arm_when_asked(tmp_path: Path) -> None:
    anchor = anchors.open_anchor(tmp_path, platform_name="win32")
    try:
        assert isinstance(anchor, anchors._WindowsAnchor)
    finally:
        anchor.close()


def test_open_absolute_anchor_returns_the_windows_arm_when_asked(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    anchor = anchors.open_absolute_anchor(nested, platform_name="win32")
    try:
        assert isinstance(anchor, anchors._WindowsAnchor)
    finally:
        anchor.close()


def test_the_two_tier_names_are_the_only_two(tmp_path: Path) -> None:
    """A third tier must be a deliberate act, not a typo that falls through."""
    assert {anchors.anchor_tier("linux"), anchors.anchor_tier("win32")} == {
        anchors.POSIX_STRONG_TIER,
        anchors.WINDOWS_REVALIDATED_TIER,
    }


def test_posix_anchor_opens_creates_and_lists_children(tmp_path: Path) -> None:
    anchor = anchors.open_anchor(tmp_path)
    try:
        child = anchor.open_or_create_child("created")
        try:
            assert stat.S_ISDIR(child.self_stat().st_mode)
        finally:
            child.close()
        reopened = anchor.open_or_create_child("created")
        reopened.close()
        anchor.mkdir("plain")
        anchor.mkdir("moded", 0o700)
        assert sorted(anchor.listdir()) == ["created", "moded", "plain"]
        anchor.rmdir("plain")
        assert "plain" not in anchor.listdir()
    finally:
        anchor.close()


@pytest.mark.skipif(sys.platform == "win32", reason="constructs _PosixAnchor, which needs os.O_DIRECTORY")
def test_posix_anchor_entry_operations_cover_links_unlink_and_identity(tmp_path: Path) -> None:
    (tmp_path / "payload").write_text("bytes", encoding="utf-8")
    anchor = anchors.open_anchor(tmp_path)
    try:
        anchor.symlink("payload", "link")
        assert anchor.readlink("link") == "payload"
        anchor.link("payload", "hard")
        assert anchor.identity("hard") == anchor.identity("payload")
        assert not stat.S_ISLNK(anchor.lstat("payload").st_mode)
        assert stat.S_ISLNK(anchor.lstat("link").st_mode)
        anchor.unlink("hard")
        with pytest.raises(FileNotFoundError):
            anchor.lstat("hard")
        anchor.fsync()
    finally:
        anchor.close()


def test_posix_anchor_open_file_returns_a_plain_descriptor(tmp_path: Path) -> None:
    anchor = anchors.open_anchor(tmp_path)
    try:
        descriptor = anchor.open_file("fresh", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            assert isinstance(descriptor, int)
            assert stat.S_ISREG(anchors.require_regular_file(descriptor, "fresh").st_mode)
        finally:
            os.close(descriptor)
        with pytest.raises(FileExistsError):
            anchor.open_file("fresh", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    finally:
        anchor.close()


@pytest.mark.skipif(sys.platform == "win32", reason="constructs _PosixAnchor, which needs os.O_DIRECTORY")
def test_posix_anchor_chmods_a_child_directory(tmp_path: Path) -> None:
    (tmp_path / "child").mkdir(mode=0o755)
    anchor = anchors.open_anchor(tmp_path)
    try:
        anchor.chmod_child_directory("child", 0o700)
        assert stat.S_IMODE((tmp_path / "child").stat().st_mode) == 0o700
    finally:
        anchor.close()


def test_posix_anchor_rename_noreplace_refuses_to_clobber(tmp_path: Path) -> None:
    (tmp_path / "source").write_text("one", encoding="utf-8")
    (tmp_path / "occupied").write_text("two", encoding="utf-8")
    anchor = anchors.open_anchor(tmp_path)
    try:
        with pytest.raises(OSError):
            anchor.rename_noreplace("source", anchor, "occupied")
        assert (tmp_path / "occupied").read_text(encoding="utf-8") == "two"
        anchor.rename_noreplace("source", anchor, "vacant")
        assert (tmp_path / "vacant").read_text(encoding="utf-8") == "one"
        assert not (tmp_path / "source").exists()
    finally:
        anchor.close()


def test_posix_anchor_duplicate_is_independently_closable(tmp_path: Path) -> None:
    anchor = anchors.open_anchor(tmp_path)
    duplicate = anchor.duplicate()
    duplicate.close()
    try:
        assert stat.S_ISDIR(anchor.self_stat().st_mode)
    finally:
        anchor.close()


def test_posix_anchor_path_and_alias_resolve_to_the_pinned_directory(tmp_path: Path) -> None:
    anchor = anchors.open_anchor(tmp_path)
    try:
        assert anchor.path() == tmp_path.resolve()
        assert (anchor.alias() / "..").exists()
    finally:
        anchor.close()


def test_posix_anchor_identity_assertions_detect_a_swapped_directory(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    anchor = anchors.open_anchor(target)
    try:
        anchor.assert_identity(target, "target")
        anchor.assert_directory_identity(target, "target")
        target.rename(tmp_path / "moved")
        with pytest.raises(ValueError, match="directory changed during mutation"):
            anchor.assert_directory_identity(target, "target")
        (tmp_path / "target").mkdir()
        with pytest.raises(ValueError, match="changed during mutation"):
            anchor.assert_identity(target, "target")
    finally:
        anchor.close()


def test_open_absolute_anchor_rejects_a_relative_path_and_walks_an_absolute_one(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        anchors.open_absolute_anchor(Path("relative"))
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    anchor = anchors.open_absolute_anchor(nested)
    try:
        assert anchor.path() == nested.resolve()
    finally:
        anchor.close()
    with pytest.raises(FileNotFoundError):
        anchors.open_absolute_anchor(tmp_path / "absent")


@pytest.mark.skipif(sys.platform == "win32", reason="constructs _PosixAnchor, which needs os.O_DIRECTORY")
def test_exclusive_lock_refuses_a_descriptor_that_is_not_a_directory(tmp_path: Path) -> None:
    file = tmp_path / "file"
    file.write_text("x", encoding="utf-8")
    anchor = anchors._PosixAnchor(os.open(file, os.O_RDONLY))
    try:
        with pytest.raises(NotADirectoryError), anchor.exclusive_lock():
            pass
    finally:
        anchor.close()


def test_exclusive_lock_and_lock_file_round_trip(tmp_path: Path) -> None:
    anchor = anchors.open_anchor(tmp_path)
    try:
        with anchor.exclusive_lock():
            pass
        with anchor.lock_file("executor.lock", assert_identity=True):
            assert (tmp_path / "executor.lock").is_file()
        with anchor.lock_file("executor.lock", assert_identity=False):
            pass
    finally:
        anchor.close()


@pytest.mark.skipif(sys.platform == "win32", reason="monkeypatches fcntl.flock; fcntl does not exist on Windows")
def test_lock_file_detects_a_lock_swapped_between_open_and_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fcntl

    anchor = anchors.open_anchor(tmp_path)
    original = fcntl.flock

    def swap_then_lock(descriptor: int, operation: int) -> None:
        if operation == fcntl.LOCK_EX and (tmp_path / "executor.lock").exists():
            (tmp_path / "executor.lock").unlink()
            (tmp_path / "executor.lock").write_text("", encoding="utf-8")
        original(descriptor, operation)

    monkeypatch.setattr(fcntl, "flock", swap_then_lock)
    try:
        with (
            pytest.raises(ValueError, match="changed during mutation"),
            anchor.lock_file("executor.lock", assert_identity=True),
        ):
            pass
    finally:
        anchor.close()


def test_lock_path_locks_an_absolute_regular_file(tmp_path: Path) -> None:
    with anchors.lock_path(tmp_path / "executor.lock"):
        assert (tmp_path / "executor.lock").is_file()
    (tmp_path / "directory").mkdir()
    with pytest.raises((ValueError, IsADirectoryError, OSError)), anchors.lock_path(tmp_path / "directory"):
        pass


def test_lock_path_windows_arm_delegates_to_the_portable_primitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Windows arm carried `# pragma: no cover` and an `UnboundLocalError`
    behind it for as long as it existed: the module-level `locked` import was
    shadowed for the whole function by a local of the same name on the POSIX
    path, so the branch raised before it ever reached the lock. No coverage
    gate saw it, because the pragma said not to look."""
    calls: list[Path] = []

    @contextmanager
    def record(path: Path) -> Iterator[None]:
        calls.append(path)
        yield

    monkeypatch.setattr(anchors.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(anchors, "locked", record)

    target = tmp_path / "executor.lock"
    with anchors.lock_path(target):
        pass

    assert calls == [target]


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="opens a directory with os.open, which Windows refuses with PermissionError",
)
def test_require_regular_file_rejects_a_directory(tmp_path: Path) -> None:
    descriptor = os.open(tmp_path, os.O_RDONLY)
    try:
        with pytest.raises(ValueError, match="not a regular file"):
            anchors.require_regular_file(descriptor, "directory")
    finally:
        os.close(descriptor)


def _module_scope_imports(source: str) -> set[str]:
    """Top-level import names only -- a function-scope import is not the hazard."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def _module_source(dotted: str) -> str:
    spec = importlib.util.find_spec(dotted)
    assert spec is not None and spec.origin is not None
    return Path(spec.origin).read_text(encoding="utf-8")


def test_transactions_imports_no_posix_only_module_at_module_scope() -> None:
    """On native Windows the engine must *import* and fail at call time.

    Asserted against the module source rather than a mocked import: a mock can
    pass vacuously, and this property is the reason the anchor seam exists.
    """
    source = _module_source("graph_works_core.workspace.transactions")
    offending = _module_scope_imports(source) & POSIX_ONLY_MODULES
    assert offending == set(), f"transactions.py imports POSIX-only modules at module scope: {sorted(offending)}"


def test_anchors_imports_no_posix_only_module_at_module_scope() -> None:
    """The hazard is transitive: `transactions` imports `anchors`.

    The sibling item asserted this property against `transactions.py` alone,
    which passed vacuously -- `import fcntl` at `anchors.py` module scope
    still killed `import transactions` on native Windows before argv was
    parsed.  Both halves are asserted now.
    """
    source = _module_source("graph_works_core.workspace.anchors")
    offending = _module_scope_imports(source) & POSIX_ONLY_MODULES
    assert offending == set(), f"anchors.py imports POSIX-only modules at module scope: {sorted(offending)}"


def test_anchors_still_owns_the_posix_only_primitives() -> None:
    """The seam did not simply delete the dependency: `fcntl` still appears,
    inside the functions that use it, and nowhere else in `workspace`."""
    source = _module_source("graph_works_core.workspace.anchors")
    assert "import fcntl" in source
    assert "fcntl" not in _module_scope_imports(source)


def test_no_workspace_module_imports_a_posix_only_module_at_module_scope() -> None:
    """The transitive closure, asserted once rather than per module."""
    package = Path(importlib.util.find_spec("graph_works_core.workspace").origin).parent
    for module_path in sorted(package.glob("*.py")):
        offending = _module_scope_imports(module_path.read_text(encoding="utf-8")) & POSIX_ONLY_MODULES
        assert offending == set(), f"{module_path.name} imports {sorted(offending)} at module scope"


def test_workspace_engine_imports_with_fcntl_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The native-Windows import path, simulated: hide `fcntl`, drop the
    modules from `sys.modules`, and import again.  A structural `ast` check
    cannot catch a lazily-added top-level import in a helper module; this can.
    """
    for name in [n for n in sys.modules if n.startswith("graph_works_core.workspace")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, "fcntl", None)  # a None entry makes `import fcntl` raise
    importlib.import_module("graph_works_core.workspace.transactions")


def test_directory_flags_refuses_by_name_where_o_directory_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """`AttributeError: module 'os' has no attribute 'O_DIRECTORY'` is what 96
    Windows test failures looked like. A named refusal says which primitive is
    missing and which tier the caller landed on by mistake."""
    monkeypatch.setattr(anchors.sys, "platform", "win32", raising=False)
    with pytest.raises(anchors.UnsupportedAnchorPlatform, match="O_DIRECTORY"):
        anchors.directory_flags()


def test_nofollow_flag_is_zero_where_the_kernel_has_no_o_nofollow(monkeypatch: pytest.MonkeyPatch) -> None:
    """`nofollow_flag` degrades to 0 rather than refusing -- unlike
    `directory_flags` -- because `_WindowsAnchor.open_child` supplies a real
    (weaker) replacement check. ADR-0042 records the difference."""
    monkeypatch.setattr(anchors.sys, "platform", "win32", raising=False)
    assert anchors.nofollow_flag() == 0


def test_flock_helpers_refuse_by_name_where_fcntl_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anchors.sys, "platform", "win32", raising=False)
    for helper in (anchors._flock_exclusive, anchors._flock_release):
        with pytest.raises(anchors.UnsupportedAnchorPlatform, match=r"fcntl\.flock"):
            helper(0)


def test_set_mode_uses_the_descriptor_where_os_fchmod_exists(tmp_path: Path) -> None:
    target = tmp_path / "page.md"
    target.write_text("x\n", encoding="utf-8")
    descriptor = os.open(target, os.O_RDWR)
    try:
        anchors.set_mode(descriptor, target, 0o600)
    finally:
        os.close(descriptor)
    assert stat.S_IMODE(target.stat().st_mode) & stat.S_IWRITE


def test_set_mode_falls_back_to_a_path_chmod_on_windows_below_3_13(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`os.fchmod` gained Windows support in CPython 3.13; the workspace floor
    is 3.12, where it does not exist. Bumping mypy's `python_version` would
    silence the error and leave the crash (D-3), so the branch is real."""
    target = tmp_path / "page.md"
    target.write_text("x\n", encoding="utf-8")
    seen: list[tuple[Path, int]] = []
    monkeypatch.setattr(anchors.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(anchors.sys, "version_info", (3, 12, 0, "final", 0), raising=False)
    monkeypatch.setattr(Path, "chmod", lambda self, mode: seen.append((self, mode)))

    anchors.set_mode(-1, target, 0o600)  # -1: an invalid descriptor proves it was not used

    assert seen == [(target, 0o600)]
