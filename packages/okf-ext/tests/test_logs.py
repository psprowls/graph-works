"""`okf_ext.logs`: the locked, atomic §9 append.

Lifted out of `work_tracker_okf.compose`, where the same three functions
already served two call sites. These are the properties that lift has to keep:
one entry lands, a second on the same day joins its section, and every refusal
comes back as `None` rather than as an exception.
"""

from __future__ import annotations

import stat
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest
from ext_helpers import write
from okf_ext.logs import append_entry, atomic_replace, locked_log

TODAY = date(2026, 8, 19)

LOG = """# Log

## 2026-08-18

- **note** yesterday
"""


def _bundle(tmp_path: Path, log: str | None = LOG) -> Path:
    root = tmp_path / "okf"
    root.mkdir()
    write(root / "index.md", "# Index\n")
    if log is not None:
        write(root / "log.md", log)
    return root


def test_an_entry_lands_in_a_new_dated_section(tmp_path):
    root = _bundle(tmp_path)

    assert append_entry(root, "**note** today", on=TODAY) == "**note** today"

    text = (root / "log.md").read_text(encoding="utf-8")
    assert "## 2026-08-19" in text
    assert "- **note** today" in text
    assert "- **note** yesterday" in text


def test_a_second_entry_on_the_same_day_joins_the_existing_section(tmp_path):
    root = _bundle(tmp_path)
    append_entry(root, "**note** first", on=TODAY)
    append_entry(root, "**note** second", on=TODAY)

    text = (root / "log.md").read_text(encoding="utf-8")
    assert text.count("## 2026-08-19") == 1
    assert text.index("- **note** first") < text.index("- **note** second")


def test_a_bundle_with_no_log_is_refused_silently(tmp_path):
    root = _bundle(tmp_path, log=None)

    assert append_entry(root, "nothing to record", on=TODAY) is None


def test_an_unparseable_log_is_refused_silently(tmp_path):
    root = _bundle(tmp_path, log="---\nnot: [closed\n")
    before = (root / "log.md").read_text(encoding="utf-8")

    assert append_entry(root, "**note** today", on=TODAY) is None
    assert (root / "log.md").read_text(encoding="utf-8") == before


def test_an_out_of_order_log_is_refused_rather_than_stranding_a_section(tmp_path):
    root = _bundle(tmp_path, log="# Log\n\n## 2026-08-10\n\n- old\n\n## 2026-08-20\n\n- newer\n")
    before = (root / "log.md").read_text(encoding="utf-8")

    assert append_entry(root, "**note** today", on=TODAY) is None
    assert (root / "log.md").read_text(encoding="utf-8") == before


def test_a_write_failure_is_refused_rather_than_raised(tmp_path, monkeypatch):
    root = _bundle(tmp_path)

    def fail(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", fail)

    assert append_entry(root, "**note** today", on=TODAY) is None
    assert not tuple(root.glob(".log.md.*.tmp"))


def test_a_log_that_disappears_inside_the_lock_is_refused(tmp_path, monkeypatch):
    """The second `is_file()` guard, inside the lock, is the race this covers:
    another process replacing the bundle between the first check and the read."""
    root = _bundle(tmp_path)
    real = locked_log

    @contextmanager
    def unlinking(log_path):
        with real(log_path):
            log_path.unlink()
            yield

    monkeypatch.setattr("okf_ext.logs.locked_log", unlinking)

    assert append_entry(root, "**note** today", on=TODAY) is None


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="group/other mode bits have no representation on NTFS",
)
def test_atomic_replace_keeps_the_full_existing_file_mode_on_posix(tmp_path):
    target = tmp_path / "log.md"
    write(target, "before\n")
    target.chmod(0o640)

    atomic_replace(target, b"after\n")

    assert target.read_text(encoding="utf-8") == "after\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_atomic_replace_preserves_the_read_only_bit_on_both_platforms(tmp_path):
    target = tmp_path / "log.md"
    write(target, "before\n")
    target.chmod(stat.S_IREAD)

    atomic_replace(target, b"after\n")

    assert target.read_text(encoding="utf-8") == "after\n"
    assert not stat.S_IMODE(target.stat().st_mode) & stat.S_IWRITE


def test_a_failed_replace_against_a_read_only_target_raises_its_own_error_and_leaves_no_temp(tmp_path, monkeypatch):
    target = tmp_path / "log.md"
    write(target, "before\n")
    target.chmod(stat.S_IREAD)

    def fail(self, other):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", fail)

    with pytest.raises(OSError, match="disk full"):
        atomic_replace(target, b"after\n")

    assert not tuple(tmp_path.glob(".log.md.*.tmp"))


def test_the_lock_file_sits_outside_the_bundle(tmp_path):
    root = _bundle(tmp_path)

    with locked_log(root / "log.md"):
        pass

    assert (tmp_path / ".okf.log.md.lock").is_file()
    assert not tuple(root.glob("*.lock"))
