"""Display `run_*` reads accept a caller's session and then never open their own.

`session=None` is the CLI path and must answer exactly what a borrowed snapshot
answers (feature-sidecar-read-generation's seam convention).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from graph_works_core.read_session import materialize, open_read_session
from graph_works_core.read_session import open as read_session_open
from graph_works_core.wiki_page import citations as wiki_citations
from graph_works_core.wiki_page import commands as wiki_commands
from graph_works_core.work import commands as work_commands
from graph_works_core.workspace.layout import WorkspaceLayout

READS: dict[str, Callable[..., object]] = {
    "status": lambda layout, **kw: work_commands.run_status(layout, **kw),
    "work_list": lambda layout, **kw: work_commands.run_work_list(layout, **kw),
    "item_read": lambda layout, **kw: work_commands.run_item_read(layout, "work/epic-a", **kw),
    "item_read_unknown": lambda layout, **kw: work_commands.run_item_read(layout, "work/nope", **kw),
    "work_queue": lambda layout, **kw: work_commands.run_work_queue(layout, **kw),
    "open_decisions": lambda layout, **kw: work_commands.run_open_decisions(layout, **kw),
    "page_read": lambda layout, **kw: wiki_commands.run_page_read(layout, "docs/explanations/p", **kw),
    "wiki_tree": lambda layout, **kw: wiki_commands.run_wiki_tree(layout, **kw),
    "citations": lambda layout, **kw: wiki_citations.run_wiki_citations(layout, "docs/explanations/p", **kw),
}


def _refuse(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("opened its own read session despite a borrowed one")


@pytest.mark.parametrize("name", sorted(READS))
def test_borrowed_session_answers_like_an_owned_one_and_opens_none(
    workspace: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    read = READS[name]
    owned = read(workspace, session=None)
    with open_read_session(workspace) as session:
        snapshot = materialize(session)
    for module in (work_commands, wiki_commands, wiki_citations, read_session_open):
        monkeypatch.setattr(module, "open_read_session", _refuse, raising=False)
    assert read(workspace, session=snapshot) == owned
