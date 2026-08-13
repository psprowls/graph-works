"""The generic runner over its four injected seams."""

from __future__ import annotations

import os
import time as _t

from subagents_io.adapters import LoopAdapter, LoopOutcome, Prepared, RunContext
from subagents_io.roles import RoleBinding, RoleSpec
from subagents_io.runner import run_all, run_loop, run_single, stream_and_parse

# `os` and `time` are here for the two `os.utime` calls that force a
# deterministic mtime ordering in the `_newest_trace` test. `test_boundaries.py`
# walks `src/subagents_io` only, so a test importing `os` breaks nothing.


class FakeReader:
    def close(self) -> None:
        pass


def _ctx(tmp_path) -> RunContext[FakeReader]:
    return RunContext(workspace=tmp_path, repo_root=tmp_path, wiki=tmp_path / "wiki")


class _Chunk:
    """Minimal stand-in for langchain AIMessageChunk supporting + accumulation."""

    def __init__(self, content, usage=None):
        self.content = content
        self.usage_metadata = usage

    def __add__(self, other):
        return _Chunk(self.content + other.content, other.usage_metadata or self.usage_metadata)


class FakeLLM:
    def __init__(self, chunks, response=None):
        self._chunks = chunks
        self._response = response
        self.invocations = []

    async def astream(self, messages):
        self.invocations.append(messages)
        for c in self._chunks:
            yield c

    async def ainvoke(self, messages):
        self.invocations.append(messages)
        return self._response


def _binding(llm=None, **spec_kwargs) -> RoleBinding:
    spec = RoleSpec(model_id=spec_kwargs.pop("model_id", "vendor.model-1:0"), **spec_kwargs)
    return RoleBinding(spec=spec, make_llm=lambda: llm)


class _FakeAdapter:
    name = "fake"
    role = "librarian"
    selector = "file"
    supports_all = True

    def __init__(self, items=("a", "b"), parse=None, note=None):
        self._items = list(items)
        self._parse = parse
        self._note = note

    async def prepare(self, ctx, item) -> Prepared:
        return Prepared(item_id=item, system=f"sys:{item}", human=f"hum:{item}", parse=self._parse, note=self._note)

    def items(self, ctx):
        return list(self._items)


class _FakeLoopAdapter:
    name = "query_orchestrator"
    role = "query_orchestrator"
    selector = "query"

    async def run(self, ctx, item) -> LoopOutcome:
        return LoopOutcome(
            item_id=item[:80],
            role=self.role,
            model_id="",
            region="",
            answer="the answer",
            structured={"confidence": "high", "citations": ["a"], "gaps": []},
            trace_metadata={"status": "ok", "worker_batches": 1},
            latency_s=0.0,
            trace_path=None,
            note="loop note",
        )


# --- stream_and_parse ---------------------------------------------------------


async def test_stream_aggregates_usage_and_parses():
    chunks = [
        _Chunk("topic: "),
        _Chunk("parsing\n"),
        _Chunk("", usage={"input_tokens": 142, "output_tokens": 38}),
    ]
    seen = []
    raw, parsed, perr, tin, tout, latency, interrupted = await stream_and_parse(
        FakeLLM(chunks),
        system="s",
        human="h",
        parse=lambda text: {"len": len(text)},
        do_parse=True,
        on_chunk=seen.append,
    )
    assert raw == "topic: parsing\n"
    assert seen == ["topic: ", "parsing\n", ""]
    assert tin == 142 and tout == 38
    assert parsed == {"len": len("topic: parsing\n")} and perr is None
    assert interrupted is False and latency >= 0.0


async def test_parser_failure_is_captured_not_raised():
    def boom(_text):
        raise ValueError("bad")

    _raw, parsed, perr, *_ = await stream_and_parse(
        FakeLLM([_Chunk("x")]), system="s", human="h", parse=boom, do_parse=True, on_chunk=lambda _t: None
    )
    assert parsed is None
    assert perr == "ValueError: bad"


async def test_no_usage_metadata_leaves_tokens_none():
    _raw, parsed, _perr, tin, tout, *_ = await stream_and_parse(
        FakeLLM([_Chunk("hi")]), system="s", human="h", parse=None, do_parse=False, on_chunk=lambda _t: None
    )
    assert tin is None and tout is None and parsed is None


async def test_content_block_lists_are_coerced_to_text():
    chunks = [_Chunk([{"text": "block "}, "plain", {"no_text": 1}, 7])]
    raw, *_ = await stream_and_parse(
        FakeLLM(chunks), system="s", human="h", parse=None, do_parse=False, on_chunk=lambda _t: None
    )
    assert raw == "block plain"


async def test_non_string_non_list_content_is_stringified():
    # One chunk per call, not one call with two chunks: `_Chunk.__add__`
    # concatenates `.content`, and None + 12 is a TypeError before
    # `_coerce_content` ever sees either.
    none_raw, *_ = await stream_and_parse(
        FakeLLM([_Chunk(None)]), system="s", human="h", parse=None, do_parse=False, on_chunk=lambda _t: None
    )
    int_raw, *_ = await stream_and_parse(
        FakeLLM([_Chunk(12)]), system="s", human="h", parse=None, do_parse=False, on_chunk=lambda _t: None
    )
    assert none_raw == ""
    assert int_raw == "12"


async def test_keyboard_interrupt_marks_interrupted_and_skips_the_parse():
    class Interrupting:
        async def astream(self, messages):
            yield _Chunk("partial")
            raise KeyboardInterrupt

    raw, parsed, perr, _tin, _tout, _latency, interrupted = await stream_and_parse(
        Interrupting(),
        system="s",
        human="h",
        parse=lambda _text: {"never": True},
        do_parse=True,
        on_chunk=lambda _t: None,
    )
    assert interrupted is True
    assert raw == "partial"
    assert parsed is None and perr is None


# --- run_single ---------------------------------------------------------------


async def test_run_single_takes_its_model_and_spec_from_the_binding(tmp_path):
    llm = FakeLLM([_Chunk("hello", usage={"input_tokens": 3, "output_tokens": 4})])
    binding = _binding(llm, model_id="vendor.model-9:0", region="eu-west-1")
    outcome = await run_single(
        _FakeAdapter(note="n"),
        _ctx(tmp_path),
        "item-1",
        binding=binding,
        do_parse=False,
        on_chunk=lambda _t: None,
    )
    assert outcome.item_id == "item-1"
    assert outcome.role == "librarian"
    assert outcome.model_id == "vendor.model-9:0"
    assert outcome.region == "eu-west-1"
    assert outcome.system == "sys:item-1" and outcome.human == "hum:item-1"
    assert outcome.raw == "hello"
    assert outcome.tokens_in == 3 and outcome.tokens_out == 4
    assert outcome.note == "n"
    assert outcome.cost_usd is None  # no lookup injected


async def test_run_single_costs_through_the_injected_lookup(tmp_path):
    llm = FakeLLM([_Chunk("x", usage={"input_tokens": 10, "output_tokens": 20})])
    seen = []

    def lookup(model_id, usage):
        seen.append((model_id, dict(usage)))
        return 0.5

    outcome = await run_single(
        _FakeAdapter(),
        _ctx(tmp_path),
        "i",
        binding=_binding(llm),
        do_parse=False,
        on_chunk=lambda _t: None,
        price_lookup=lookup,
    )
    assert outcome.cost_usd == 0.5
    assert seen == [("vendor.model-1:0", {"input": 10, "output": 20})]


async def test_an_unknown_model_leaves_cost_none_rather_than_raising(tmp_path):
    def lookup(model_id, usage):
        raise KeyError(model_id)  # UnknownModelError subclasses KeyError

    outcome = await run_single(
        _FakeAdapter(),
        _ctx(tmp_path),
        "i",
        binding=_binding(FakeLLM([_Chunk("x", usage={"input_tokens": 1, "output_tokens": 2})])),
        do_parse=False,
        on_chunk=lambda _t: None,
        price_lookup=lookup,
    )
    assert outcome.cost_usd is None


async def test_missing_tokens_short_circuit_the_lookup(tmp_path):
    def lookup(model_id, usage):
        raise AssertionError("must not be called without both token counts")

    outcome = await run_single(
        _FakeAdapter(),
        _ctx(tmp_path),
        "i",
        binding=_binding(FakeLLM([_Chunk("x")])),
        do_parse=False,
        on_chunk=lambda _t: None,
        price_lookup=lookup,
    )
    assert outcome.cost_usd is None


async def test_run_single_surfaces_a_parse_error(tmp_path):
    def boom(_text):
        raise ValueError("nope")

    outcome = await run_single(
        _FakeAdapter(parse=boom),
        _ctx(tmp_path),
        "i",
        binding=_binding(FakeLLM([_Chunk("x")])),
        do_parse=True,
        on_chunk=lambda _t: None,
    )
    assert outcome.parsed is None
    assert outcome.parse_error == "ValueError: nope"


# --- run_all ------------------------------------------------------------------


class _Resp:
    def __init__(self, content):
        self.content = content
        self.usage_metadata = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}


async def test_run_all_fans_out_over_the_adapter_worklist(tmp_path):
    trace_dir = tmp_path / "traces"
    llm = FakeLLM([], response=_Resp("answer"))
    result = await run_all(
        _FakeAdapter(items=("a", "b", "c")),
        _ctx(tmp_path),
        binding=_binding(llm, max_concurrency=2),
        trace_dir=trace_dir,
    )
    assert [item for item, _value in result.successes] == ["a", "b", "c"]
    assert {value for _item, value in result.successes} == {"answer"}
    assert result.errors == []
    # The pool wrote its JSONL into the injected directory, not a derived one.
    assert list(trace_dir.glob("*.jsonl"))


async def test_run_all_forwards_the_price_lookup_to_the_pool(tmp_path):
    calls = []

    def lookup(model_id, usage):
        calls.append(model_id)
        return 1.25

    await run_all(
        _FakeAdapter(items=("a",)),
        _ctx(tmp_path),
        binding=_binding(FakeLLM([], response=_Resp("x"))),
        trace_dir=tmp_path / "traces",
        price_lookup=lookup,
    )
    assert calls == ["vendor.model-1:0"]


async def test_run_all_calls_the_factory_once_per_item(tmp_path):
    made = []

    def factory():
        made.append(1)
        return FakeLLM([], response=_Resp("x"))

    binding = RoleBinding(spec=RoleSpec(model_id="m"), make_llm=factory)
    await run_all(_FakeAdapter(items=("a", "b")), _ctx(tmp_path), binding=binding, trace_dir=tmp_path / "t")
    assert len(made) == 2


# --- run_loop -----------------------------------------------------------------


def test_fake_adapter_satisfies_loop_protocol():
    assert isinstance(_FakeLoopAdapter(), LoopAdapter)


async def test_run_loop_overlays_model_region_latency_and_trace(tmp_path):
    traces = tmp_path / "traces"
    traces.mkdir(parents=True)
    (traces / "old.jsonl").write_text("{}\n")
    newest = traces / "new.jsonl"
    newest.write_text("{}\n")
    os.utime(traces / "old.jsonl", (1, 1))
    os.utime(newest, (_t.time(), _t.time()))

    outcome = await run_loop(
        _FakeLoopAdapter(),
        _ctx(tmp_path),
        "what is X?",
        binding=_binding(model_id="vendor.model-1:0", region="us-west-2"),
        trace_dir=traces,
    )
    assert outcome.model_id == "vendor.model-1:0"
    assert outcome.region == "us-west-2"
    assert outcome.answer == "the answer"
    assert outcome.latency_s >= 0.0
    assert outcome.trace_path == str(newest)
    assert outcome.note == "loop note"


async def test_run_loop_no_trace_dir_yields_none(tmp_path):
    outcome = await run_loop(
        _FakeLoopAdapter(),
        _ctx(tmp_path),
        "q",
        binding=_binding(),
        trace_dir=tmp_path / "absent",
    )
    assert outcome.trace_path is None
    assert outcome.region is None  # no provider default in band 1


async def test_run_loop_empty_trace_dir_yields_none(tmp_path):
    empty = tmp_path / "traces"
    empty.mkdir()
    outcome = await run_loop(_FakeLoopAdapter(), _ctx(tmp_path), "q", binding=_binding(), trace_dir=empty)
    assert outcome.trace_path is None
