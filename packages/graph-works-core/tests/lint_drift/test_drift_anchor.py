"""The regenerable `_cache/drift/propagated.json` anchor. Losing it costs one
extra judging pass, so nothing here raises."""

from __future__ import annotations

import json

from graph_works_core.lint_drift.drift_anchor import read_anchors, write_anchors


def test_an_absent_file_reads_as_empty(tmp_path):
    assert read_anchors(tmp_path) == {}


def test_a_write_then_read_round_trips(tmp_path):
    write_anchors(tmp_path, {"packages/okf-io": "abc1234", "apps/cli": "def5678"})
    assert read_anchors(tmp_path) == {"packages/okf-io": "abc1234", "apps/cli": "def5678"}


def test_the_file_lands_under_drift_and_is_sorted_and_newline_terminated(tmp_path):
    write_anchors(tmp_path, {"b": "2", "a": "1"})
    path = tmp_path / "drift" / "propagated.json"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert list(json.loads(text)) == ["a", "b"]


def test_invalid_json_reads_as_empty(tmp_path):
    (tmp_path / "drift").mkdir()
    (tmp_path / "drift" / "propagated.json").write_text("{not json", encoding="utf-8")
    assert read_anchors(tmp_path) == {}


def test_a_non_mapping_top_level_reads_as_empty(tmp_path):
    (tmp_path / "drift").mkdir()
    (tmp_path / "drift" / "propagated.json").write_text('["a", "b"]', encoding="utf-8")
    assert read_anchors(tmp_path) == {}


def test_entries_that_are_not_string_pairs_are_dropped(tmp_path):
    (tmp_path / "drift").mkdir()
    (tmp_path / "drift" / "propagated.json").write_text(
        json.dumps({"good": "abc", "blank": "", "numeric": 7, "null": None}), encoding="utf-8"
    )
    assert read_anchors(tmp_path) == {"good": "abc"}


def test_a_directory_where_the_file_belongs_reads_as_empty(tmp_path):
    (tmp_path / "drift" / "propagated.json").mkdir(parents=True)
    assert read_anchors(tmp_path) == {}


def test_writing_twice_replaces_rather_than_merges(tmp_path):
    write_anchors(tmp_path, {"a": "1"})
    write_anchors(tmp_path, {"b": "2"})
    assert read_anchors(tmp_path) == {"b": "2"}
