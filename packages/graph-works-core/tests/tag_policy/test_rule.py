from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from graph_works_core.tag_policy.rule import DEFAULT_FLOOR, draft, entity_names, field_values
from okf_ext.tags import inventory
from okf_io import load_bundle

TODAY = date(2026, 9, 8)


def build(tmp_path: Path, pages: dict[str, list[str]]) -> Path:
    """One bundle, one file per entry, tags from the list."""
    for concept_id, tags in pages.items():
        target = tmp_path / f"{concept_id}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        rendered = "".join(f"- {tag}\n" for tag in tags)
        target.write_text(
            f"---\ntype: Reference\ntitle: {concept_id}\ntags:\n{rendered}---\n\n# {concept_id}\n",
            encoding="utf-8",
            newline="",
        )
    return tmp_path


def dispose(tmp_path: Path, pages: dict[str, list[str]], **kwargs):
    root = build(tmp_path, pages)
    bundle = load_bundle(root)
    return draft(inventory(bundle), bundle, generated=TODAY, **kwargs)


def verdict_for(disposition, tag):
    return next((v.verdict, v.reason) for v in disposition.verdicts if v.tag == tag)


def test_a_tag_below_the_floor_is_stripped(tmp_path):
    pages = {f"p{n}": ["common"] for n in range(6)}
    pages["p0"] = ["common", "rare"]
    pages.update({f"q{n}": ["filler"] for n in range(10)})
    result = dispose(tmp_path, pages)
    assert verdict_for(result, "rare") == ("strip", "below-floor")
    assert verdict_for(result, "common") == ("keep", "survivor")


def test_the_floor_is_inclusive(tmp_path):
    pages = {f"p{n}": ["five"] for n in range(DEFAULT_FLOOR)}
    pages.update({f"q{n}": ["filler"] for n in range(20)})
    assert verdict_for(dispose(tmp_path, pages), "five") == ("keep", "survivor")


def test_a_tag_above_the_ceiling_is_stripped(tmp_path):
    pages = {f"p{n}": ["everywhere"] for n in range(20)}
    for n in range(5):
        pages[f"p{n}"] = ["everywhere", "some"]
    result = dispose(tmp_path, pages)
    assert verdict_for(result, "everywhere") == ("strip", "above-ceiling")
    assert verdict_for(result, "some") == ("keep", "survivor")


def test_the_ceiling_is_exclusive_and_measured_against_tagged_pages(tmp_path):
    # 10 tagged pages, `half` on exactly 4 of them -> 40%, which is NOT below
    # the 0.40 ceiling, so it goes. `three` on 3 -> 30%, which stays.
    pages = {f"p{n}": ["base"] for n in range(10)}
    for n in range(4):
        pages[f"p{n}"] = ["base", "half"]
    for n in range(4, 7):
        pages[f"p{n}"] = ["base", "three"]
    result = dispose(tmp_path, pages, floor=1)
    assert verdict_for(result, "half") == ("strip", "above-ceiling")
    assert verdict_for(result, "three") == ("keep", "survivor")


def test_a_tag_naming_an_entity_page_is_stripped(tmp_path):
    pages = {f"p{n}": ["okf-io"] for n in range(6)}
    pages.update({f"q{n}": ["filler"] for n in range(10)})
    pages["repositories/graph-works/packages/okf-io"] = []
    result = dispose(tmp_path, pages)
    assert verdict_for(result, "okf-io") == ("strip", "entity-dup")


def test_a_dependency_page_also_counts_as_an_entity(tmp_path):
    pages = {f"p{n}": ["mypy"] for n in range(6)}
    pages.update({f"q{n}": ["filler"] for n in range(10)})
    pages["dependencies/pypi/mypy"] = []
    assert verdict_for(dispose(tmp_path, pages), "mypy") == ("strip", "entity-dup")


def test_a_file_lane_page_is_not_an_entity(tmp_path):
    pages = {f"p{n}": ["thing"] for n in range(6)}
    pages.update({f"q{n}": ["filler"] for n in range(10)})
    pages["repositories/graph-works/files/thing"] = []
    assert verdict_for(dispose(tmp_path, pages), "thing") == ("keep", "survivor")


@pytest.mark.parametrize("tag", ["design", "plan", "tech-debt", "spike", "in-progress", "package"])
def test_a_tag_duplicating_a_frontmatter_field_value_is_stripped(tmp_path, tag):
    pages = {f"p{n}": [tag] for n in range(6)}
    pages.update({f"q{n}": ["filler"] for n in range(10)})
    assert verdict_for(dispose(tmp_path, pages), tag) == ("strip", "field-dup")


def test_a_contributed_tag_is_kept_even_at_zero_uses(tmp_path):
    result = dispose(tmp_path, {f"p{n}": ["only"] for n in range(6)})
    assert verdict_for(result, "perf") == ("keep", "contributed")
    assert verdict_for(result, "security") == ("keep", "contributed")


def test_the_floor_is_checked_before_the_ceiling_entity_and_field_tests(tmp_path):
    # `design` is a field value AND used once. Floor first means below-floor.
    pages = {f"p{n}": ["filler"] for n in range(6)}
    pages["p0"] = ["filler", "design"]
    assert verdict_for(dispose(tmp_path, pages), "design") == ("strip", "below-floor")


def test_every_tag_appears_exactly_once(tmp_path):
    result = dispose(tmp_path, {f"p{n}": ["a", "b", "c"] for n in range(6)})
    names = [v.tag for v in result.verdicts]
    assert names == sorted(names)
    assert len(names) == len(set(names))


def test_the_draft_never_proposes_a_merge(tmp_path):
    """Test 5 is judgment; the human turns keeps into merges by hand."""
    result = dispose(tmp_path, {f"p{n}": ["a", "b"] for n in range(6)})
    assert not result.merge


def test_the_header_records_what_was_measured(tmp_path):
    result = dispose(tmp_path, {f"p{n}": ["a"] for n in range(6)})
    assert result.generated == TODAY
    assert result.tagged_pages == 6
    assert result.total_tags == len(result.verdicts)


def test_entity_names_reads_only_entity_lane_pages(tmp_path):
    root = build(
        tmp_path,
        {
            "repositories/gw/repository": [],
            "repositories/gw/packages/okf-io": [],
            "dependencies/pypi/mypy": [],
            "repositories/gw/files/x": [],
            "concepts/thing": [],
        },
    )
    assert entity_names(load_bundle(root)) == frozenset({"gw", "okf-io", "mypy"})


def test_entity_names_derives_a_dependency_named_repository_by_its_own_name(tmp_path):
    """`dependencies/<eco>/repository` names a dependency literally called
    `repository` -- it must not be mistaken for the repository-page shape
    (`repositories/<repo>/repository`) just because the last segment matches.
    Position, not spelling, decides which shape a page has."""
    root = build(
        tmp_path,
        {
            "dependencies/pypi/repository": [],
            "repositories/gw/repository": [],
        },
    )
    assert entity_names(load_bundle(root)) == frozenset({"repository", "gw"})


def test_field_values_covers_all_four_frontmatter_fields():
    values = field_values()
    assert {"tech-debt", "spike"} <= values  # type, kebab-folded
    assert {"design", "plan", "execute", "finish", "done"} <= values  # phase
    assert {"open", "in-progress", "resolved"} <= values  # work_status
    assert {"file", "package", "domain", "system"} <= values  # blast-radius
