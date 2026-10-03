from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve.readstate import ReadState


@dataclass
class FakeSession:
    backend: str = "index"
    fallback: str | None = None
    generation: int | None = 1

    def concept_hashes(self, *, ignore: Sequence[str] = ()) -> Mapping[str, str] | None:
        return None


class FakeOpener:
    def __init__(self, session: FakeSession | None = None) -> None:
        self.value = session or FakeSession()
        self.calls = []
        self.fail = None

    @contextmanager
    def __call__(self, layout: WorkspaceLayout, *, reconcile: bool = True) -> Iterator[FakeSession]:
        self.calls.append(reconcile)
        if self.fail:
            raise self.fail
        yield self.value


def state_for(opener: FakeOpener) -> ReadState:
    return ReadState(opener=opener, materializer=lambda s: s)
