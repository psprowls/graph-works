"""Unit coverage for `entities.lanes.is_entity_lane_page` -- the structural
predicate `sync/snapshot.py` and `graph_works_core.scan.commands` both key
off, so a lane change can never silently drift between the two.
"""

from __future__ import annotations

from code_wiki_okf.entities.lanes import is_entity_lane_page


def test_a_global_lane_page_is_a_member() -> None:
    assert is_entity_lane_page("dependencies/requests") is True


def test_the_repository_page_itself_is_a_member() -> None:
    assert is_entity_lane_page("repositories/repo-a") is True


def test_a_nested_repo_scoped_lane_page_is_a_member() -> None:
    assert is_entity_lane_page("repositories/repo-a/packages/widgets") is True
    assert is_entity_lane_page("repositories/repo-a/apps/cli-app") is True
    assert is_entity_lane_page("repositories/repo-a/test-suites/tests") is True
    assert is_entity_lane_page("repositories/repo-a/agent-plugins/demo-plugin") is True


def test_a_nested_mirror_file_page_is_not_a_member() -> None:
    assert is_entity_lane_page("repositories/repo-a/fs/src/mod.py") is False


def test_a_flat_legacy_style_lane_page_is_not_a_member() -> None:
    """The pre-nesting flat shape is no longer recognized -- this is the
    intended behavior change this item makes (§4/§7 of the design spec)."""
    assert is_entity_lane_page("packages/widgets") is False


def test_something_outside_every_lane_is_not_a_member() -> None:
    assert is_entity_lane_page("concepts/some-page") is False
