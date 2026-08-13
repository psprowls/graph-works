"""Tests for config_io.projection — the JSON read surface for non-Python consumers."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest
from config_fakes import DictStore
from config_io.projection import PROJECTION_FILENAME, write_projection
from config_io.store import PlainYamlStore


def _payload(target):
    return json.loads(target.read_text(encoding="utf-8"))


def test_returns_the_target_path(tmp_path):
    target = tmp_path / "out" / PROJECTION_FILENAME
    assert write_projection(DictStore({"topic": "x"}), target) == target


def test_explicit_values_only_no_defaults_merged(tmp_path):
    target = tmp_path / PROJECTION_FILENAME
    write_projection(DictStore({"version": 2}), target)
    payload = _payload(target)
    assert payload["version"] == 2
    # The store held no `workflow` block, so the projection has none — a
    # consumer's own `${VAR:-default}` fallback is what supplies the default.
    assert "workflow" not in payload


def test_meta_keys_are_source_prefixed(tmp_path):
    # Renamed from manifest_mtime / manifest_sha256: "manifest" is caller
    # vocabulary in a package that must not know its caller.
    target = tmp_path / PROJECTION_FILENAME
    write_projection(DictStore(), target)
    assert set(_payload(target)["_meta"]) == {"source_mtime", "source_sha256"}


def test_meta_from_a_real_store_fingerprint(tmp_path):
    source = tmp_path / "config.yaml"
    source.write_text("topic: x\n", encoding="utf-8")
    target = tmp_path / PROJECTION_FILENAME
    write_projection(PlainYamlStore(source), target)
    meta = _payload(target)["_meta"]
    assert meta["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert meta["source_mtime"] is not None


def test_absent_store_leaves_both_meta_fields_none(tmp_path):
    # Both fields must agree, so a consumer's staleness check never compares a
    # real hash against a missing mtime.
    target = tmp_path / PROJECTION_FILENAME
    write_projection(PlainYamlStore(tmp_path / "missing.yaml"), target)
    payload = _payload(target)
    assert payload["_meta"] == {"source_mtime": None, "source_sha256": None}
    assert set(payload) == {"_meta"}


def test_creates_the_target_parent_directory(tmp_path):
    target = tmp_path / "a" / "b" / PROJECTION_FILENAME
    write_projection(DictStore({"topic": "x"}), target)
    assert target.is_file()


def test_leaves_no_temp_file_behind(tmp_path):
    target = tmp_path / PROJECTION_FILENAME
    write_projection(DictStore({"topic": "x"}), target)
    assert [p.name for p in tmp_path.iterdir()] == [PROJECTION_FILENAME]


def test_overwrites_an_existing_projection_atomically(tmp_path):
    target = tmp_path / PROJECTION_FILENAME
    write_projection(DictStore({"topic": "old"}), target)
    write_projection(DictStore({"topic": "new"}), target)
    assert _payload(target)["topic"] == "new"
    assert [p.name for p in tmp_path.iterdir()] == [PROJECTION_FILENAME]


def test_dates_serialise_rather_than_raising(tmp_path):
    # PyYAML parses a bare `2026-07-04` into datetime.date, which json.dumps
    # refuses without default=str.
    class DateStore(DictStore):
        def read_explicit(self):
            return {"initialized_at": dt.date(2026, 7, 4)}

    target = tmp_path / PROJECTION_FILENAME
    write_projection(DateStore(), target)
    assert _payload(target)["initialized_at"] == "2026-07-04"


def test_a_failing_render_cleans_up_and_does_not_clobber(tmp_path):
    target = tmp_path / PROJECTION_FILENAME
    write_projection(DictStore({"topic": "keep"}), target)

    class ExplodingStore(DictStore):
        def read_explicit(self):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        write_projection(ExplodingStore(), target)
    assert _payload(target)["topic"] == "keep"
    assert [p.name for p in tmp_path.iterdir()] == [PROJECTION_FILENAME]


def test_a_failure_after_the_temp_file_exists_still_cleans_up(tmp_path, monkeypatch):
    # Distinct from the render-failure case above: here the temp file has
    # actually been created and written before the failure, so this exercises
    # the `except BaseException: tmp.unlink(...)` branch itself rather than
    # the earlier read_explicit() guard.
    target = tmp_path / PROJECTION_FILENAME
    write_projection(DictStore({"topic": "keep"}), target)

    def _boom(self, _target):
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", _boom)
    with pytest.raises(OSError, match="replace failed"):
        write_projection(DictStore({"topic": "new"}), target)
    assert _payload(target)["topic"] == "keep"
    assert [p.name for p in tmp_path.iterdir()] == [PROJECTION_FILENAME]


def test_the_written_file_is_not_left_owner_only(tmp_path):
    # mkstemp creates its file at mode 0600; a regenerated projection must not
    # silently narrow to owner-only for consumers reading across a process or
    # user boundary.
    target = tmp_path / PROJECTION_FILENAME
    write_projection(DictStore({"topic": "x"}), target)
    assert target.stat().st_mode & 0o777 == 0o644
