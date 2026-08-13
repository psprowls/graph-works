"""The lane map for proposal rendering.

Five lanes: four Diátaxis types plus one ADR lane (the only dated one).
"""

from __future__ import annotations

from datetime import date

import pytest
from diataxis_helpers import schema_set
from doc_wiki_okf.proposals import lane_set


def test_five_lanes_in_order() -> None:
    """Lanes are: tutorial, how-to, reference, explanation, adr."""
    lanes_obj = lane_set(schema_set())
    assert len(lanes_obj.lanes) == 5
    assert [lane.name for lane in lanes_obj.lanes] == [
        "tutorial",
        "how-to",
        "reference",
        "explanation",
        "adr",
    ]


def test_diataxis_lanes_directory_from_schema_set() -> None:
    """Each Diátaxis lane's directory equals directory_for(schema_set, <Type>)."""
    ss = schema_set()
    lanes_obj = lane_set(ss)

    # Tutorial
    assert lanes_obj.lanes[0].directory == "tutorials/"
    assert lanes_obj.lanes[0].type_name == "Tutorial"

    # HowTo
    assert lanes_obj.lanes[1].directory == "how-tos/"
    assert lanes_obj.lanes[1].type_name == "HowTo"

    # Reference
    assert lanes_obj.lanes[2].directory == "references/"
    assert lanes_obj.lanes[2].type_name == "Reference"

    # Explanation
    assert lanes_obj.lanes[3].directory == "explanations/"
    assert lanes_obj.lanes[3].type_name == "Explanation"


def test_adr_lane_is_explanation_in_adrs_and_is_dated() -> None:
    """adr is Explanation in adrs/, and is the only dated lane."""
    lanes_obj = lane_set(schema_set())
    adr_lane = lanes_obj.lanes[4]

    assert adr_lane.name == "adr"
    assert adr_lane.directory == "adrs/"
    assert adr_lane.type_name == "Explanation"
    assert adr_lane.dated is True

    # Verify Diátaxis lanes are not dated
    for lane in lanes_obj.lanes[:4]:
        assert lane.dated is False


def test_target_for_undated_by_default() -> None:
    """target_for is undated by default."""
    lanes_obj = lane_set(schema_set())

    # Diátaxis lanes are undated
    assert lanes_obj.target_for("tutorial", "Getting Started") == "tutorials/getting-started.md"
    assert lanes_obj.target_for("how-to", "Build a Widget") == "how-tos/build-a-widget.md"
    assert lanes_obj.target_for("reference", "Config") == "references/config.md"
    assert lanes_obj.target_for("explanation", "Why Events Work") == "explanations/why-events-work.md"

    # adr is also undated by default
    assert lanes_obj.target_for("adr", "Use YAML for Config") == "adrs/use-yaml-for-config.md"


def test_on_date_prefixes_only_dated_lane() -> None:
    """on=date(...) prefixes only the dated lane."""
    lanes_obj = lane_set(schema_set())
    test_date = date(2026, 8, 12)

    # Diátaxis lanes ignore on= (no date prefix)
    assert lanes_obj.target_for("tutorial", "Getting Started", on=test_date) == "tutorials/getting-started.md"
    assert lanes_obj.target_for("how-to", "Build a Widget", on=test_date) == "how-tos/build-a-widget.md"
    assert lanes_obj.target_for("reference", "Config", on=test_date) == "references/config.md"
    assert lanes_obj.target_for("explanation", "Why Events Work", on=test_date) == "explanations/why-events-work.md"

    # adr lane dates the filename when on= is given
    assert lanes_obj.target_for("adr", "Use YAML for Config", on=test_date) == "adrs/2026-08-12-use-yaml-for-config.md"


def test_lane_for_resolves_directory_to_lane() -> None:
    """lane_for reads a directory and returns the lane; None outside all declared lanes."""
    lanes_obj = lane_set(schema_set())

    assert lanes_obj.lane_for("tutorials/") == lanes_obj.lanes[0]
    assert lanes_obj.lane_for("how-tos/") == lanes_obj.lanes[1]
    assert lanes_obj.lane_for("references/") == lanes_obj.lanes[2]
    assert lanes_obj.lane_for("explanations/") == lanes_obj.lanes[3]
    assert lanes_obj.lane_for("adrs/") == lanes_obj.lanes[4]

    # Unknown directory returns None
    assert lanes_obj.lane_for("docs/") is None
    assert lanes_obj.lane_for("guides/") is None


@pytest.mark.parametrize(
    "target_path,expected_lane_name",
    [
        # Full file paths within each lane
        ("adrs/2026-08-12-x.md", "adr"),
        ("adrs/bulk-write.md", "adr"),
        ("references/deep/x.md", "reference"),
        ("references/config.md", "reference"),
        ("tutorials/getting-started.md", "tutorial"),
        ("how-tos/build-a-widget.md", "how-to"),
        ("explanations/why-events-work.md", "explanation"),
        # Nested paths within lane directories
        ("references/deep/nested/page.md", "reference"),
        ("tutorials/advanced/section/page.md", "tutorial"),
        # Paths outside all lane directories return None
        ("docs/something.md", None),
        ("guides/unknown.md", None),
        ("other/file.md", None),
    ],
)
def test_lane_for_matches_full_paths(target_path: str, expected_lane_name: str | None) -> None:
    """lane_for resolves full file paths to lanes by directory prefix."""
    lanes_obj = lane_set(schema_set())

    result = lanes_obj.lane_for(target_path)
    if expected_lane_name is None:
        assert result is None
    else:
        assert result is not None
        assert result.name == expected_lane_name


def test_unknown_lane_name_raises_keyerror() -> None:
    """An unknown lane name in target_for raises KeyError."""
    lanes_obj = lane_set(schema_set())

    with pytest.raises(KeyError):
        lanes_obj.target_for("unknown", "Some Title")


def test_lane_is_frozen() -> None:
    """Lane is a frozen dataclass."""
    ss = schema_set()
    lanes_obj = lane_set(ss)
    lane = lanes_obj.lanes[0]

    with pytest.raises(AttributeError):
        lane.name = "something-else"  # type: ignore


def test_lane_set_is_frozen() -> None:
    """LaneSet is a frozen dataclass."""
    lanes_obj = lane_set(schema_set())

    with pytest.raises(AttributeError):
        lanes_obj.lanes = ()  # type: ignore
