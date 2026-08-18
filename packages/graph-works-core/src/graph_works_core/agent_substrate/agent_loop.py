"""The capped LangChain tool-call loop every vertical runs.

Three of its properties are load-bearing and easy to lose in an edit:

- **`coerce_tool_name` does two jobs.** It recovers the intended tool from a
  namespaced name (`functions.read_repo_file`) *and* writes the coerced name
  back into the call dict so the replayed history stays valid. Bedrock accepts
  a malformed `toolUse.name` in a response and rejects it on the next request,
  so skipping the write-back aborts the whole loop one turn later.
- **The two cap outcomes stay distinct.** Hitting the iteration cap after
  producing text is `ok` *with* an error note; hitting it having produced
  nothing is `failed`. A caller that collapses them loses a usable answer.
- **`failed` does not imply an empty `final_text`.** A final turn that comes
  back with no tool calls and no text is `failed`, but it carries whatever text
  an earlier turn produced. Same reasoning as the cap outcomes; the
  cap-without-text path still returns `""` because it has nothing to carry.
- **Every replayed `toolUse` is answered exactly once.** The turn's calls are
  repaired in one ordered pass — valid name, non-empty id, a well-formed dict
  substituted for anything that is not one — before the response is appended,
  and only then are they invoked. An unanswered `toolUse` in the replayed
  history is the same rejection class as a malformed name.

`llm` is typed `BaseChatModel`, since langchain-core is a declared dependency.
The runnable `bind_tools` returns stays `Any` — langchain's own type does not
narrow usefully, and inventing a `Protocol` for it is more surface than
`--strict` is asking for.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from models_io.normalize import normalize_content

_VALID_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")


@dataclass(frozen=True)
class ToolLoopResult:
    status: str
    final_text: str
    error: str | None = None


def coerce_tool_name(raw_name: str, known_names: set[str]) -> str:
    """Coerce a model-emitted tool name to satisfy Bedrock's `[a-zA-Z0-9_-]+` rule.

    gpt-oss-class models occasionally echo a namespaced or malformed tool name
    (e.g. `functions.read_repo_file`). We first try to recover the intended
    tool by stripping a leading namespace; failing that we just make the name
    charset-valid so the existing unknown-tool path can guide the model.
    """
    if raw_name in known_names or _VALID_TOOL_NAME.match(raw_name):
        return raw_name
    candidate = re.split(r"[./:]", raw_name)[-1]
    candidate = re.sub(r"[^a-zA-Z0-9_-]", "_", candidate)
    if candidate in known_names:
        return candidate
    return candidate or "unknown_tool"


def tool_call_parts(call: Any) -> tuple[str, dict[str, Any], str]:  # noqa: ANN401 -- a model-emitted call is whatever the provider sent
    if not isinstance(call, dict):
        return "", {}, ""
    name = str(call.get("name", ""))
    args = call.get("args", {})
    if not isinstance(args, dict):
        args = {}
    call_id = str(call.get("id", ""))
    return name, args, call_id


async def _invoke(agent_tool: BaseTool | None, call_args: dict[str, Any], call_name: str) -> str:
    """One tool call, as content.

    An unknown tool and a raising tool both come back as an `ERROR: …` string
    rather than an exception, so a single failure cannot cancel the siblings
    `asyncio.gather` is running alongside it.
    """
    if agent_tool is None:
        return f"ERROR: unknown tool {call_name!r}"
    try:
        output = await agent_tool.ainvoke(call_args)
    except Exception as exc:  # a tool failure is content for the model, not a crash
        return f"ERROR: {exc}"
    return output if isinstance(output, str) else str(output)


async def run_tool_loop(
    *,
    llm: BaseChatModel,
    tools: list[BaseTool],
    messages: list[Any],
    max_iterations: int,
    cap_label: str = "tool loop",
) -> ToolLoopResult:
    bound_llm: Any = llm.bind_tools(tools) if tools else llm
    tool_by_name = {agent_tool.name: agent_tool for agent_tool in tools}
    loop_messages = list(messages)
    last_text = ""

    for iteration in range(max_iterations):
        response = normalize_content(await bound_llm.ainvoke(loop_messages))
        text = getattr(response, "content", "") or ""
        if text and str(text).strip():
            last_text = str(text)
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            if not str(text).strip():
                return ToolLoopResult(
                    status="failed", final_text=last_text, error=f"{cap_label} returned empty response"
                )
            return ToolLoopResult(status="ok", final_text=str(text))

        known_names = set(tool_by_name)
        repaired: list[Any] = []
        substituted = False
        plans: list[tuple[BaseTool | None, dict[str, Any], str]] = []
        call_ids: list[str] = []
        for index, call in enumerate(tool_calls):
            raw_name, call_args, call_id = tool_call_parts(call)
            call_name = coerce_tool_name(raw_name, known_names)
            if not call_id:
                call_id = f"call_{iteration}_{index}"
            if isinstance(call, dict):
                # Repair the replayed history: Bedrock accepts a malformed
                # toolUse in a response and rejects it on the next request.
                call["name"] = call_name
                call["id"] = call_id
                repaired.append(call)
            else:
                repaired.append({"name": call_name, "args": call_args, "id": call_id})
                substituted = True
            plans.append((tool_by_name.get(call_name), call_args, call_name))
            call_ids.append(call_id)
        if substituted:
            response.tool_calls = repaired
        loop_messages.append(response)

        outputs = await asyncio.gather(*(_invoke(*plan) for plan in plans))
        loop_messages.extend(
            ToolMessage(content=output, tool_call_id=call_id) for output, call_id in zip(outputs, call_ids, strict=True)
        )

    if last_text:
        return ToolLoopResult(
            status="ok",
            final_text=last_text,
            error=f"{cap_label} hit iteration cap ({max_iterations}) after producing text",
        )
    return ToolLoopResult(
        status="failed",
        final_text="",
        error=f"{cap_label} hit iteration cap ({max_iterations})",
    )


__all__ = ["ToolLoopResult", "coerce_tool_name", "run_tool_loop", "tool_call_parts"]
