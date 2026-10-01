from __future__ import annotations

import pytest
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.manifest_edit import repoint_repository
from ruamel.yaml import YAML

CLONE = "okf/repositories/graph-works/references/git"
CHECKOUT = ".gw/worktrees/graph-works/main"

LIVE = """version: 1
initialized_at: '2026-08-02'
repositories:
  graph-works:
    path: ../gw
    ignore:
    - '**/fixtures/**'
    gate:
      full: just check
  gw-ui:
    path: ../gw-ui
"""


def test_the_live_shape_changes_only_two_lines() -> None:
    after = repoint_repository(LIVE, "graph-works", path=CLONE, checkout=CHECKOUT)
    assert after == LIVE.replace("    path: ../gw\n", f"    path: {CLONE}\n    checkout: {CHECKOUT}\n", 1)


def test_comments_quotes_and_neighbours_survive() -> None:
    text = (
        "# top\nrepositories:  # repos\n  demo:\n    # the code\n"
        "    path: '../code'  # sibling\n    ignore: [a]\n  other:\n    path: ../x\n"
    )
    after = repoint_repository(
        text, "demo", path="okf/repositories/demo/references/git", checkout=".gw/worktrees/demo/main"
    )
    assert after == (
        "# top\nrepositories:  # repos\n  demo:\n    # the code\n"
        "    path: okf/repositories/demo/references/git  # sibling\n"
        "    checkout: .gw/worktrees/demo/main\n"
        "    ignore: [a]\n  other:\n    path: ../x\n"
    )


def test_crlf_is_kept() -> None:
    text = LIVE.replace("\n", "\r\n")
    after = repoint_repository(
        text, "gw-ui", path="okf/repositories/gw-ui/references/git", checkout=".gw/worktrees/gw-ui/main"
    )
    assert "\n" not in after.replace("\r\n", "")
    assert "    path: okf/repositories/gw-ui/references/git\r\n    checkout: .gw/worktrees/gw-ui/main\r\n" in after


def test_last_line_without_a_newline() -> None:
    after = repoint_repository("repositories:\n  demo:\n    path: ../x", "demo", path="p", checkout="c")
    assert after == "repositories:\n  demo:\n    path: p\n    checkout: c\n"


def test_a_value_needing_quotes_is_double_quoted() -> None:
    after = repoint_repository("repositories:\n  demo:\n    path: x\n", "demo", path="p", checkout="C:\\ws\\main: x")
    assert YAML(typ="safe").load(after)["repositories"]["demo"]["checkout"] == "C:\\ws\\main: x"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("- a\n", "must hold a mapping"),
        ("version: 1\n", r"repositories\.demo"),
        ("repositories: {demo: {path: x}}\n", "block mapping"),
        ("repositories:\n  demo: {path: x}\n", "block mapping"),
        ("repositories:\n  demo:\n    ignore: []\n", r"repositories\.demo\.path"),
        ("repositories:\n  demo:\n    path: [x]\n", r"repositories\.demo\.path"),
        ("repositories:\n  demo:\n    path: x\n    checkout: y\n", "already declares checkout"),
    ],
)
def test_shapes_it_will_not_splice(text: str, message: str) -> None:
    with pytest.raises(WorkspaceError, match=message):
        repoint_repository(text, "demo", path="p", checkout="c")


@pytest.mark.parametrize("value", ["True", "NULL", "123", ".nan", ".inf"])
def test_yaml_implicit_values_remain_strings(value: str) -> None:
    after = repoint_repository("repositories:\n  demo:\n    path: x\n", "demo", path=value, checkout=value)
    entry = YAML(typ="safe").load(after)["repositories"]["demo"]
    assert entry == {"path": value, "checkout": value}


def test_an_alias_that_would_change_a_neighbour_is_refused() -> None:
    text = "repositories:\n  demo: &repo\n    path: x\n  other: *repo\n"
    with pytest.raises(WorkspaceError, match="would change more than path and checkout"):
        repoint_repository(text, "demo", path="p", checkout="c")


@pytest.mark.parametrize(
    "text",
    [
        "repositories:\n  demo:\n    path: |\n      ../code\n    ignore: []\n",
        "repositories:\n  demo:\n    ? path\n    : ../code\n    ignore: []\n",
    ],
)
def test_scalar_layouts_without_an_inline_path_value_are_refused(text: str) -> None:
    with pytest.raises(WorkspaceError, match=r"repositories\.demo\.path"):
        repoint_repository(text, "demo", path="p", checkout="c")
