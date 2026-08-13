"""RunContext's reader seam: injected, lazy, closed once, typed when absent."""

from __future__ import annotations

from pathlib import Path

import pytest
from subagents_io.adapters import (
    Adapter,
    LoopAdapter,
    LoopOutcome,
    NoGraphReader,
    Prepared,
    RunContext,
)


class FakeReader:
    """Satisfies `Closeable` and counts its own closes."""

    def __init__(self) -> None:
        self.closes = 0

    def close(self) -> None:
        self.closes += 1


class CountingOpener:
    def __init__(self) -> None:
        self.calls: list[Path] = []
        self.reader = FakeReader()

    def __call__(self, workspace: Path) -> FakeReader:
        self.calls.append(workspace)
        return self.reader


class FakeAdapter:
    name = "fake"
    role = "librarian"
    selector = "file"
    supports_all = True

    async def prepare(self, ctx, item):
        return Prepared(item_id=item, system="s", human="h")

    def items(self, ctx):
        return ["one", "two"]


class FakeLoopAdapter:
    name = "query_orchestrator"
    role = "query_orchestrator"
    selector = "query"

    async def run(self, ctx, item) -> LoopOutcome:
        return LoopOutcome(
            item_id=item,
            role=self.role,
            model_id="",
            region=None,
            answer="the answer",
            structured=None,
            trace_metadata={},
            latency_s=0.0,
            trace_path=None,
        )


def _ctx(tmp_path: Path, opener=None) -> RunContext[FakeReader]:
    return RunContext(
        workspace=tmp_path,
        repo_root=tmp_path,
        wiki=tmp_path / "wiki",
        open_reader=opener,
    )


def test_the_reader_opens_lazily_and_exactly_once(tmp_path):
    opener = CountingOpener()
    ctx = _ctx(tmp_path, opener)
    assert opener.calls == []  # constructing the context opens nothing
    first = ctx.graph_reader()
    second = ctx.graph_reader()
    assert first is second
    assert opener.calls == [tmp_path]


def test_close_closes_the_reader_and_is_idempotent(tmp_path):
    opener = CountingOpener()
    ctx = _ctx(tmp_path, opener)
    reader = ctx.graph_reader()
    ctx.close()
    ctx.close()
    assert reader.closes == 1


def test_close_before_any_read_is_a_no_op(tmp_path):
    opener = CountingOpener()
    ctx = _ctx(tmp_path, opener)
    ctx.close()
    assert opener.calls == []


def test_reopening_after_close_calls_the_opener_again(tmp_path):
    opener = CountingOpener()
    ctx = _ctx(tmp_path, opener)
    ctx.graph_reader()
    ctx.close()
    ctx.graph_reader()
    assert opener.calls == [tmp_path, tmp_path]


def test_no_opener_raises_no_graph_reader_not_attribute_error(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(NoGraphReader) as excinfo:
        ctx.graph_reader()
    # The message has to name the fix, not just the fact — D3's standard.
    assert "open_reader=" in str(excinfo.value)


def test_no_opener_context_still_closes_cleanly(tmp_path):
    _ctx(tmp_path).close()


def test_the_open_reader_is_absent_from_the_repr(tmp_path):
    ctx = _ctx(tmp_path, CountingOpener())
    ctx.graph_reader()
    # `field(repr=False)` on the cached reader. Asserted by the reader's own
    # repr rather than by the field name: "open_reader=" contains "_reader="
    # as a substring, so the obvious assertion can never fail.
    assert "FakeReader object" not in repr(ctx)


def test_fakes_satisfy_the_two_protocols():
    assert isinstance(FakeAdapter(), Adapter)
    assert isinstance(FakeLoopAdapter(), LoopAdapter)


def test_a_non_adapter_does_not_satisfy_the_protocol():
    # Guards the guard: `isinstance` against a runtime_checkable Protocol is
    # attribute-presence only, so a test that only ever asserts True proves
    # very little.
    assert not isinstance(object(), Adapter)
