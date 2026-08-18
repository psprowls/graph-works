"""The capped tool-call loop, exercised against a fake LLM. No model calls."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from graph_works_core.agent_substrate.agent_loop import ToolLoopResult, coerce_tool_name, run_tool_loop
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool


@tool
def echo(text: str) -> str:
    """Echo the text back."""
    return f"echoed {text}"


@tool
def boom() -> str:
    """Always fail."""
    raise RuntimeError("kaboom")


@tool
def payload() -> dict[str, int]:
    """Return a non-string payload."""
    return {"n": 1}


class _Response:
    def __init__(self, content: Any = "", tool_calls: list[Any] | None = None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.additional_kwargs: dict[str, Any] = {}


class _FakeLLM:
    """Returns queued responses, then repeats the last one forever."""

    def __init__(self, responses: list[_Response]):
        self.responses = list(responses)
        self.seen: list[list[Any]] = []
        self._last = _Response("")

    def bind_tools(self, tools: list[Any]) -> _FakeLLM:
        return self

    async def ainvoke(self, messages: list[Any]) -> _Response:
        self.seen.append(list(messages))
        if self.responses:
            self._last = self.responses.pop(0)
        return self._last


def _call(name: str, args: dict[str, Any] | None = None, call_id: str = "c1") -> dict[str, Any]:
    return {"name": name, "args": args or {}, "id": call_id}


def _tool_messages(llm: _FakeLLM) -> list[str]:
    return [str(m.content) for m in llm.seen[-1] if isinstance(m, ToolMessage)]


async def test_text_with_no_tool_calls_is_ok():
    llm = _FakeLLM([_Response("the answer")])
    result = await run_tool_loop(llm=llm, tools=[echo], messages=[HumanMessage("q")], max_iterations=3)
    assert result == ToolLoopResult(status="ok", final_text="the answer")


async def test_an_empty_response_is_a_failure():
    llm = _FakeLLM([_Response("   ")])
    result = await run_tool_loop(llm=llm, tools=[], messages=[], max_iterations=3, cap_label="scan")
    assert result.status == "failed"
    assert "scan returned empty response" in str(result.error)


async def test_a_tool_call_round_trips_then_answers():
    llm = _FakeLLM([_Response("", [_call("echo", {"text": "hi"})]), _Response("done")])
    result = await run_tool_loop(llm=llm, tools=[echo], messages=[], max_iterations=3)
    assert result.status == "ok"
    assert result.final_text == "done"
    assert _tool_messages(llm) == ["echoed hi"]


async def test_an_unknown_tool_is_reported_back_to_the_model():
    llm = _FakeLLM([_Response("", [_call("nope")]), _Response("done")])
    await run_tool_loop(llm=llm, tools=[echo], messages=[], max_iterations=3)
    assert _tool_messages(llm) == ["ERROR: unknown tool 'nope'"]


async def test_a_raising_tool_is_reported_back_to_the_model():
    llm = _FakeLLM([_Response("", [_call("boom")]), _Response("done")])
    await run_tool_loop(llm=llm, tools=[boom], messages=[], max_iterations=3)
    assert _tool_messages(llm) == ["ERROR: kaboom"]


async def test_a_non_string_tool_result_is_stringified():
    llm = _FakeLLM([_Response("", [_call("payload")]), _Response("done")])
    await run_tool_loop(llm=llm, tools=[payload], messages=[], max_iterations=3)
    assert _tool_messages(llm) == ["{'n': 1}"]


async def test_a_namespaced_tool_name_is_coerced_and_written_back():
    # Bedrock accepts a malformed toolUse.name in a response and rejects it on
    # the next request, so the replayed history has to be repaired in place.
    call = _call("functions.echo", {"text": "hi"})
    llm = _FakeLLM([_Response("", [call]), _Response("done")])
    await run_tool_loop(llm=llm, tools=[echo], messages=[], max_iterations=3)
    assert call["name"] == "echo"
    assert _tool_messages(llm) == ["echoed hi"]


async def test_the_cap_after_text_is_ok_with_a_note():
    llm = _FakeLLM([_Response("partial", [_call("echo", {"text": "hi"})])])
    result = await run_tool_loop(llm=llm, tools=[echo], messages=[], max_iterations=2, cap_label="query")
    assert result.status == "ok"
    assert result.final_text == "partial"
    assert "query hit iteration cap (2) after producing text" in str(result.error)


async def test_the_cap_without_text_is_a_failure():
    llm = _FakeLLM([_Response("", [_call("echo", {"text": "hi"})])])
    result = await run_tool_loop(llm=llm, tools=[echo], messages=[], max_iterations=2, cap_label="query")
    assert result.status == "failed"
    assert result.error == "query hit iteration cap (2)"


def test_coerce_leaves_a_known_or_valid_name_alone():
    assert coerce_tool_name("echo", {"echo"}) == "echo"
    assert coerce_tool_name("read_repo_file", set()) == "read_repo_file"


def test_coerce_recovers_the_intended_tool_from_a_namespace():
    assert coerce_tool_name("functions.echo", {"echo"}) == "echo"


def test_coerce_makes_an_unrecoverable_name_charset_valid():
    assert coerce_tool_name("weird name!", set()) == "weird_name_"
    assert coerce_tool_name("!!!", set()) == "___"


async def test_an_empty_final_turn_after_text_returns_the_salvaged_answer():
    # D1: `status == "failed"` no longer implies an empty `final_text`. The
    # answer from turn 1 is still the best thing the caller can be handed.
    llm = _FakeLLM(
        [
            _Response("partial answer", [_call("echo", {"text": "hi"})]),
            _Response("   "),
        ]
    )
    result = await run_tool_loop(llm=llm, tools=[echo], messages=[], max_iterations=5, cap_label="query")
    assert result.status == "failed"
    assert result.final_text == "partial answer"
    assert result.error == "query returned empty response"


async def test_list_shaped_content_is_flattened_rather_than_stringified():
    # A foreign BaseChatModel, i.e. one that is not a models-io guarded
    # subclass, can return Bedrock's multi-block content shape.
    llm = _FakeLLM([_Response([{"type": "text", "text": "flat "}, {"type": "text", "text": "answer"}])])
    result = await run_tool_loop(llm=llm, tools=[], messages=[], max_iterations=3)
    assert result == ToolLoopResult(status="ok", final_text="flat answer")


async def test_a_call_with_no_id_gets_a_synthetic_one_written_back():
    # An empty tool_call_id is the same class of malformed-request rejection
    # the tool-name write-back exists to prevent.
    call = _call("echo", {"text": "hi"}, call_id="")
    llm = _FakeLLM([_Response("", [call]), _Response("done")])
    await run_tool_loop(llm=llm, tools=[echo], messages=[], max_iterations=3)
    assert call["id"] == "call_0_0"
    assert [m.tool_call_id for m in llm.seen[-1] if isinstance(m, ToolMessage)] == ["call_0_0"]


async def test_a_non_dict_call_is_replaced_by_a_well_formed_dict():
    response = _Response("", ["not-a-dict"])
    llm = _FakeLLM([response, _Response("done")])
    result = await run_tool_loop(llm=llm, tools=[echo], messages=[], max_iterations=3)
    assert result.status == "ok"
    assert response.tool_calls == [{"name": "unknown_tool", "args": {}, "id": "call_0_0"}]
    assert _tool_messages(llm) == ["ERROR: unknown tool 'unknown_tool'"]


async def test_every_replayed_tool_use_has_exactly_one_tool_message():
    response = _Response(
        "",
        [_call("echo", {"text": "a"}, call_id="c1"), _call("echo", {"text": "b"}, call_id=""), "not-a-dict"],
    )
    llm = _FakeLLM([response, _Response("done")])
    await run_tool_loop(llm=llm, tools=[echo], messages=[], max_iterations=3)
    replayed = [call["id"] for call in response.tool_calls]
    answered = [m.tool_call_id for m in llm.seen[-1] if isinstance(m, ToolMessage)]
    assert replayed == answered
    assert all(replayed)
    assert len(set(replayed)) == len(replayed)


async def test_two_tool_calls_in_one_turn_run_concurrently():
    # A rendezvous, not a sleep: under sequential invocation the first tool
    # waits on a partner that is never started, and wait_for turns that into a
    # failure rather than a hung suite.
    barrier = asyncio.Barrier(2)

    @tool
    async def rendezvous_a() -> str:
        """Wait for its partner."""
        await barrier.wait()
        return "a"

    @tool
    async def rendezvous_b() -> str:
        """Wait for its partner."""
        await barrier.wait()
        return "b"

    llm = _FakeLLM(
        [
            _Response("", [_call("rendezvous_a", call_id="c1"), _call("rendezvous_b", call_id="c2")]),
            _Response("done"),
        ]
    )
    result = await asyncio.wait_for(
        run_tool_loop(llm=llm, tools=[rendezvous_a, rendezvous_b], messages=[], max_iterations=3),
        timeout=10,
    )
    assert result.status == "ok"
    assert _tool_messages(llm) == ["a", "b"]


async def test_a_blocking_sync_tool_does_not_stall_the_event_loop():
    # What #7 is actually about: ten SubagentPool workers must not serialize on
    # one blocking tool. If the tool ran on the event loop, the opener below
    # could not be scheduled and the tool would time out.
    gate = threading.Event()

    @tool
    def blocks_until_open() -> str:
        """Block the calling thread until the gate is opened."""
        return "unblocked" if gate.wait(timeout=5) else "TIMEOUT"

    async def _open_the_gate() -> None:
        await asyncio.sleep(0.05)
        gate.set()

    llm = _FakeLLM([_Response("", [_call("blocks_until_open")]), _Response("done")])
    result, _ = await asyncio.wait_for(
        asyncio.gather(
            run_tool_loop(llm=llm, tools=[blocks_until_open], messages=[], max_iterations=3),
            _open_the_gate(),
        ),
        timeout=15,
    )
    assert result.status == "ok"
    assert _tool_messages(llm) == ["unblocked"]


async def test_tool_message_order_follows_call_order_not_completion_order():
    @tool
    async def slow() -> str:
        """Finish last."""
        await asyncio.sleep(0.05)
        return "slow"

    @tool
    async def fast() -> str:
        """Finish first."""
        return "fast"

    llm = _FakeLLM([_Response("", [_call("slow", call_id="c1"), _call("fast", call_id="c2")]), _Response("done")])
    await run_tool_loop(llm=llm, tools=[slow, fast], messages=[], max_iterations=3)
    assert _tool_messages(llm) == ["slow", "fast"]


async def test_a_raising_tool_does_not_cancel_its_concurrent_siblings():
    # A bare `gather` would propagate the first exception and cancel the rest;
    # catching per call keeps "a raising tool is content, not a crash".
    llm = _FakeLLM(
        [
            _Response("", [_call("boom", call_id="c1"), _call("echo", {"text": "hi"}, call_id="c2")]),
            _Response("done"),
        ]
    )
    result = await run_tool_loop(llm=llm, tools=[boom, echo], messages=[], max_iterations=3)
    assert result.status == "ok"
    assert _tool_messages(llm) == ["ERROR: kaboom", "echoed hi"]
