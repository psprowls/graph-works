"""`_plan_route` needs only a root and an unreadable map, not a bundle."""

from __future__ import annotations

from pathlib import Path

import pytest
from _display_fixture import display_workspace
from graph_works_core.work import commands as work
from work_tracker_okf.pipeline import PACKAGED_DEFINITION


def test_unknown_path_raises_value_error(tmp_path: Path) -> None:
    layout = display_workspace(tmp_path)
    with pytest.raises(ValueError, match="unknown work item 'work/nope'"):
        work._plan_route(layout, layout.bundle_dir, {}, (), "work/nope", descend=False, definition=PACKAGED_DEFINITION)


def test_unreadable_path_raises_with_its_detail(tmp_path: Path) -> None:
    layout = display_workspace(tmp_path)
    unreadable = {"work/bug-unreadable.md": "could not be decoded"}
    with pytest.raises(ValueError, match=r"work/bug-unreadable\.md could not be decoded"):
        work._plan_route(
            layout,
            layout.bundle_dir,
            unreadable,
            (),
            "work/bug-unreadable",
            descend=False,
            definition=PACKAGED_DEFINITION,
        )
