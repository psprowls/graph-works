"""The ledger round-trips, and a missing one is empty rather than an error."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from workflow_local.ledger import LEDGER_NAME, LedgerEntry, read_ledger, write_ledger


def entry(**over):
    base = {
        "key": "slug#plan",
        "handle": "slug_plan-1a2b3c4d",
        "pid": 4242,
        "state": "running",
        "argv": ("python", "child.py", "done:succeeded"),
        "cwd": "/tmp/wt",
        "started_at": "2026-08-14T12:00:00+00:00",
    }
    return LedgerEntry(**{**base, **over})


def test_a_missing_ledger_is_empty_not_an_error(tmp_path):
    # A session that has never launched anything is the normal first call to
    # open_session, not a failure.
    assert read_ledger(tmp_path / LEDGER_NAME) == {}


def test_round_trips_every_field(tmp_path):
    path = tmp_path / LEDGER_NAME
    original = {"slug#plan": entry(acked_through=3, exit_code=0, last_heartbeat_at="t", detail="d")}
    write_ledger(path, original)
    assert read_ledger(path) == original


def test_argv_comes_back_as_a_tuple(tmp_path):
    # JSON has no tuples; a list would make LedgerEntry unhashable-by-surprise
    # and would not compare equal to what was written.
    path = tmp_path / LEDGER_NAME
    write_ledger(path, {"slug#plan": entry()})
    assert read_ledger(path)["slug#plan"].argv == ("python", "child.py", "done:succeeded")


def test_defaults_are_the_never_touched_values(tmp_path):
    e = entry()
    assert (e.acked_through, e.exit_code, e.last_heartbeat_at, e.detail) == (0, None, None, None)


def test_the_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / LEDGER_NAME
    write_ledger(path, {"slug#plan": entry()})
    write_ledger(path, {"slug#plan": entry(state="succeeded")})
    assert read_ledger(path)["slug#plan"].state == "succeeded"
    assert sorted(p.name for p in tmp_path.iterdir()) == [LEDGER_NAME]


def test_the_write_creates_the_parent_directory(tmp_path):
    path = tmp_path / "deep" / "deeper" / LEDGER_NAME
    write_ledger(path, {"slug#plan": entry()})
    assert path.is_file()


def test_a_corrupt_ledger_raises_rather_than_returning_half(tmp_path):
    # Losing the ledger silently would let a coordinator relaunch every key it
    # had already dispatched. Loud is the only safe failure here.
    path = tmp_path / LEDGER_NAME
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_ledger(path)


def test_a_failed_write_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    # A write that dies mid-flight (disk full, permissions) must not litter a
    # discoverable half-written file next to the real ledger.
    path = tmp_path / LEDGER_NAME
    write_ledger(path, {"slug#plan": entry()})

    def boom(self, target):
        raise OSError("simulated failure")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="simulated failure"):
        write_ledger(path, {"slug#plan": entry(state="succeeded")})

    assert sorted(p.name for p in tmp_path.iterdir()) == [LEDGER_NAME]
    assert read_ledger(path)["slug#plan"].state != "succeeded"


def test_the_on_disk_shape_is_a_flat_key_to_object_map(tmp_path):
    # Asserted so a human can read and repair the file; it is the durable state
    # a coordinator restart depends on.
    path = tmp_path / LEDGER_NAME
    write_ledger(path, {"slug#plan": entry()})
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw) == {"slug#plan"}
    assert raw["slug#plan"]["pid"] == 4242
    assert raw["slug#plan"]["argv"] == ["python", "child.py", "done:succeeded"]
