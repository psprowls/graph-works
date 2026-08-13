"""Spec §7's acceptance properties, over data copied from the 11 live proposals."""

import importlib.resources

import pytest
from doc_wiki_okf.diataxis import directory_for
from doc_wiki_okf.proposals.filing import plan_file
from doc_wiki_okf.proposals.lanes import DIATAXIS_LANES
from doc_wiki_okf.proposals.render import ReviewRenderer
from okf_ext.proposals import apply
from okf_ext.schemas import load_schemas
from proposal_helpers import AT, BY, LIVE_SOURCES, build_bundle, lanes


@pytest.fixture(params=sorted(LIVE_SOURCES))
def live_source(request):
    return LIVE_SOURCES[request.param]


def test_rendering_the_same_sources_twice_is_byte_identical(live_source) -> None:
    render = ReviewRenderer(lane=lanes()["adr"], target="adrs/x.md", mode="create")
    assert render(description="d", sources=[live_source]) == render(description="d", sources=[live_source])


def test_every_live_shape_renders_seven_headings(live_source) -> None:
    body = ReviewRenderer(lane=lanes()["adr"], target="adrs/x.md", mode="create")(
        description="d", sources=[live_source]
    )
    for heading in (
        "## Suggested Action",
        "## Evidence From Source",
        "## Existing Pages Considered",
        "## Reasoning Summary",
        "## Potential Conflicts",
        "## Implementation Notes",
        "## Origins",
    ):
        assert heading in body


def test_reapplying_an_applied_plan_is_a_no_op(tmp_path, live_source) -> None:
    root = tmp_path / "b"
    bundle = build_bundle(root)
    first = plan_file(
        bundle,
        lanes(),
        lane="adr",
        title="Staging Protocol",
        description="d",
        source=live_source,
        by=BY,
        at=AT,
    )
    assert apply(bundle, first).ok

    second = plan_file(
        build_bundle(root),
        lanes(),
        lane="adr",
        title="Staging Protocol",
        description="d",
        source=live_source,
        by=BY,
        at=AT,
    )
    assert second.is_empty


def test_a_merge_renders_a_body_naming_both_sources(tmp_path) -> None:
    root = tmp_path / "b"
    bundle = build_bundle(root)
    apply(
        bundle,
        plan_file(
            bundle,
            lanes(),
            lane="adr",
            title="Staging Protocol",
            description="d",
            source=LIVE_SOURCES["many-evidence"],
            by=BY,
            at=AT,
        ),
    )
    plan = plan_file(
        build_bundle(root),
        lanes(),
        lane="adr",
        title="Staging Protocol",
        description="d",
        source=LIVE_SOURCES["with-considered"],
        by=BY,
        at=AT,
    )
    body = plan.writes[0].body
    assert "Commit is Path.replace (os.replace): a single filesystem rename." in body
    assert "sections seeds and validates; generators regenerates." in body
    assert "[/adrs/0012-pascalcase-types.md](/adrs/0012-pascalcase-types.md)" in body


def test_the_lane_map_agrees_with_the_declarations() -> None:
    """Spec §7: the declarations stay the single source for the four Diátaxis
    directories -- restated here as an acceptance property, not only a unit."""
    schema_set = load_schemas(str(importlib.resources.files("doc_wiki_okf") / "assets" / "_schema"))
    built = lanes()
    for name, type_name in DIATAXIS_LANES.items():
        assert built[name].directory == directory_for(schema_set, type_name)
