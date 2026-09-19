from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from okf_io import load_bundle
from okf_io.links import build
from okf_io.validate import RuleContext
from work_helpers import write_item
from work_tracker_okf._rules import _common
from work_tracker_okf.items import IGNORE

TODAY = date(2026, 8, 3)


def context_for(root: Path) -> RuleContext:
    bundle = load_bundle(root, ignore=IGNORE)
    return RuleContext(bundle=bundle, links=build(bundle), today=TODAY)


def test_lane_config_defaults_to_no_repo_root() -> None:
    assert _common.LaneConfig().repo_root is None


def test_lane_config_defaults_to_no_repo_roots() -> None:
    assert _common.LaneConfig().repo_roots == ()
    assert _common.LaneConfig().code_roots == ()


def test_lane_config_code_roots_joins_both_forms_in_order_without_duplicates() -> None:
    one, two = Path("/one"), Path("/two")
    assert _common.LaneConfig(repo_root=one).code_roots == (one,)
    assert _common.LaneConfig(repo_roots=(one, two)).code_roots == (one, two)
    assert _common.LaneConfig(repo_root=two, repo_roots=(one, two)).code_roots == (two, one)


def test_lane_config_is_frozen() -> None:
    config = _common.LaneConfig(repo_root=Path("/tmp"))
    with pytest.raises(AttributeError):
        config.repo_root = Path("/other")  # type: ignore[misc]


def test_active_skips_archived_and_yields_in_path_order(tmp_path: Path) -> None:
    write_item(tmp_path, "bug-beta", "type: Bug\n")
    write_item(tmp_path, "bug-alpha", "type: Bug\n")
    archived = tmp_path / "work" / "_archive" / "bug-gamma.md"
    archived.parent.mkdir(parents=True, exist_ok=True)
    archived.write_text("---\ntitle: T\ndescription: D\ntype: Bug\n---\n", encoding="utf-8")

    ctx = context_for(tmp_path)
    assert [item.path for item in _common.active(ctx)] == [
        "work/bug-alpha",
        "work/bug-beta",
    ]
    assert "work/_archive/bug-gamma" in {item.path for item in _common.items(ctx)}


def test_with_documents_skips_an_unparseable_page(tmp_path: Path) -> None:
    write_item(tmp_path, "bug-fine", "type: Bug\n")
    broken = tmp_path / "work" / "bug-broken.md"
    broken.write_text('---\ntype: "unterminated\n---\n\n## Plan\n', encoding="utf-8")

    ctx = context_for(tmp_path)
    assert [item.path for item, _ in _common.with_documents(ctx)] == ["work/bug-fine"]


@pytest.mark.parametrize(
    ("frontmatter", "expected"),
    [
        ("type: Bug\nmitigation: rolled back\n", "rolled back"),
        ("type: Bug\nmitigation: '   '\n", ""),
        ("type: Bug\nmitigation: 3\n", ""),
        ("type: Bug\n", ""),
    ],
)
def test_text_key_reads_only_a_non_blank_string(tmp_path: Path, frontmatter: str, expected: str) -> None:
    write_item(tmp_path, "bug-x", frontmatter)
    _, document = next(iter(_common.with_documents(context_for(tmp_path))))
    assert _common.text_key(document, "mitigation") == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("2026-05-01", 94), ("2026-08-03", 0), ("2026-05-01T09:00:00Z", 94), ("", 0), ("soonish", 0)],
)
def test_days_since_is_a_function_of_the_injected_clock(value: str, expected: int) -> None:
    assert _common.days_since(value, TODAY) == expected
