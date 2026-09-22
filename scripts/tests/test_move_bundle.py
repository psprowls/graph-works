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

from okf_io import load_bundle

from move_bundle import IGNORE, Expansion, Refused, Rule, expand, load_rules

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


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    """A mutable copy of the fixture bundle. A move writes, so never the original."""
    root = tmp_path / "okf"
    shutil.copytree(FIXTURE, root)
    return root


def test_the_fixture_bundle_has_the_shape_the_tests_assume(bundle: Path) -> None:
    present = sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file())
    assert present == [
        "explanations/diagram.png",
        "explanations/index.md",
        "explanations/why-graphs.md",
        "index.md",
        "log.md",
        "references/index.md",
        "references/moves-api.md",
        "sources/2026-08-spec.md",
        "sources/references/2026-08-spec.md",
        "work/feature-x.md",
        "work/feature-x/references/01-design.md",
    ]


def expanded(root: Path, *rules: Rule) -> Expansion:
    return expand(load_bundle(root, ignore=IGNORE), list(rules))


# --- expansion ----------------------------------------------------------------


def test_a_directory_rule_maps_every_member_beneath_it(bundle: Path) -> None:
    result = expanded(bundle, Rule("explanations", "docs/explanations"))
    assert result.refusals == ()
    assert dict(result.ordinary) == {
        "explanations/diagram.png": "docs/explanations/diagram.png",
        "explanations/why-graphs.md": "docs/explanations/why-graphs.md",
    }
    assert dict(result.reserved) == {"explanations/index.md": "docs/explanations/index.md"}


def test_a_references_rule_is_anchored_at_the_bundle_root(bundle: Path) -> None:
    """`sources/references/` and `work/<item>/references/` are out of reach."""
    result = expanded(bundle, Rule("references", "docs/reference"))
    assert result.refusals == ()
    assert sorted(result.ordinary) == ["references/moves-api.md"]
    assert sorted(result.reserved) == ["references/index.md"]


def test_two_rules_expand_into_one_mapping(bundle: Path) -> None:
    result = expanded(
        bundle,
        Rule("explanations", "docs/explanations"),
        Rule("references", "docs/reference"),
    )
    assert result.refusals == ()
    assert sorted(result.ordinary) == [
        "explanations/diagram.png",
        "explanations/why-graphs.md",
        "references/moves-api.md",
    ]
    assert sorted(result.reserved) == ["explanations/index.md", "references/index.md"]


def test_a_nested_rule_keeps_the_remainder(bundle: Path) -> None:
    result = expanded(bundle, Rule("work/feature-x/references", "work/feature-x/artifacts"))
    assert dict(result.ordinary) == {
        "work/feature-x/references/01-design.md": "work/feature-x/artifacts/01-design.md"
    }


# --- the two refusals the engine cannot see -----------------------------------


def test_two_rules_claiming_one_source_differently_is_rule_overlap(bundle: Path) -> None:
    result = expanded(
        bundle,
        Rule("explanations", "docs/explanations"),
        Rule("explanations", "guides/explanations"),
    )
    assert [r.kind for r in result.refusals] == ["rule-overlap"] * 3
    assert "`explanations/diagram.png` is claimed by 2 rules" in result.refusals[0].detail


def test_two_rules_claiming_one_source_identically_is_not_an_overlap(bundle: Path) -> None:
    result = expanded(
        bundle,
        Rule("explanations", "docs/explanations"),
        Rule("explanations", "docs/explanations"),
    )
    assert result.refusals == ()
    assert sorted(result.ordinary) == ["explanations/diagram.png", "explanations/why-graphs.md"]


def test_a_rule_matching_nothing_with_an_empty_destination_is_empty_rule(bundle: Path) -> None:
    result = expanded(bundle, Rule("reference", "docs/reference"))
    assert [r.kind for r in result.refusals] == ["empty-rule"]
    assert "`reference` matched no member and `docs/reference` holds none either" in result.refusals[0].detail


def test_a_rule_matching_nothing_whose_destination_holds_members_is_a_clean_no_op(bundle: Path) -> None:
    """The second-run case: the typo guard must not fire once the move landed."""
    result = expanded(bundle, Rule("no-such-lane", "sources"))
    assert result.refusals == ()
    assert dict(result.ordinary) == {}
    assert dict(result.reserved) == {}
