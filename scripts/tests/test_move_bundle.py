"""Acceptance tests for `scripts/move_bundle.py`.

Outside the package coverage gate on purpose: `scripts/` is repo tooling.
Run with `uv run pytest scripts/tests/test_move_bundle.py`.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from move_bundle import IGNORE, Refused, Rule, load_rules

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "move_bundle"


def write_rules(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "moves.yaml"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


DIATAXIS = """\
moves:
  - dir: explanations
    to: docs/explanations
  - dir: references
    to: docs/reference
"""


# --- the rules file -----------------------------------------------------------


def test_directory_rules_load_in_order(tmp_path: Path) -> None:
    rules, refusals = load_rules(write_rules(tmp_path, DIATAXIS))
    assert refusals == []
    assert rules == [
        Rule("explanations", "docs/explanations"),
        Rule("references", "docs/reference"),
    ]


def test_a_trailing_slash_is_stripped_from_either_end(tmp_path: Path) -> None:
    rules, refusals = load_rules(write_rules(tmp_path, "moves:\n  - dir: explanations/\n    to: docs/explanations/\n"))
    assert refusals == []
    assert rules == [Rule("explanations", "docs/explanations")]


def test_no_moves_list_is_bad_rules(tmp_path: Path) -> None:
    _, refusals = load_rules(write_rules(tmp_path, "rules:\n  - dir: a\n    to: b\n"))
    assert [r.kind for r in refusals] == ["bad-rules"]
    assert "needs a top-level `moves:` list" in refusals[0].detail


def test_invalid_yaml_is_bad_rules(tmp_path: Path) -> None:
    _, refusals = load_rules(write_rules(tmp_path, "moves: [\n"))
    assert [r.kind for r in refusals] == ["bad-rules"]
    assert "not valid YAML" in refusals[0].detail


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("moves:\n  - dir: explanations\n", "`to` must be a non-empty string"),
        ("moves:\n  - to: docs/explanations\n", "`dir` must be a non-empty string"),
        ("moves:\n  - dir: 7\n    to: docs\n", "`dir` must be a non-empty string"),
        ("moves:\n  - dir: /explanations\n    to: docs\n", "must be bundle-relative, not absolute"),
        ("moves:\n  - dir: ../outside\n    to: docs\n", "must not contain a `..` segment"),
        ("moves:\n  - dir: explanations\n    to: docs/../..\n", "must not contain a `..` segment"),
        ("moves:\n  - explanations\n", "is not a mapping"),
        ("moves:\n  - dir: a\n    to: b\n    when: never\n", "unknown key(s): when"),
    ],
)
def test_each_bad_rule_shape_is_refused(tmp_path: Path, body: str, fragment: str) -> None:
    rules, refusals = load_rules(write_rules(tmp_path, body))
    assert rules == []
    assert [r.kind for r in refusals] == ["bad-rule"]
    assert fragment in refusals[0].detail


def test_one_bad_rule_does_not_hide_the_others(tmp_path: Path) -> None:
    body = "moves:\n  - dir: explanations\n    to: docs/explanations\n  - dir: /bad\n    to: docs\n"
    rules, refusals = load_rules(write_rules(tmp_path, body))
    assert rules == [Rule("explanations", "docs/explanations")]
    assert [r.kind for r in refusals] == ["bad-rule"]


def test_the_ignore_recipe_hides_schema_and_sections_but_not_ds_store() -> None:
    assert IGNORE == ("schema/*", "*/schema/*", "sections/*", "*/sections/*")
