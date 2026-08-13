"""Collapse list-shaped message content to a plain `str`.

Shared by both guarded subclasses, and the reason neither of them needs the
other. Public at module level so a caller who wants the function can reach it;
kept out of the package `__all__` because the chartered concept is the
subclasses, not this.
"""

from __future__ import annotations

from typing import Any


def normalize_content(response: Any) -> Any:  # noqa: ANN401 -- accepts and returns whatever BaseMessage-shaped response the provider SDK hands back
    """Collapse list-shaped message content to a plain `str`.

    Bedrock's Converse API returns `response.content` as a list of content
    blocks (text + reasoning) when the model emits multi-block output (e.g.
    gpt-oss-120b, minimax-m2.5 "thinking" models). Downstream consumers call
    `.strip()` / regex on `.content` and break on a list. The trigger is
    content SHAPE, not model id — so we detect via `isinstance(content, list)`,
    never a model-name check.

    Behavior:
      - Non-list `content` (bare object, str-content message) → return unchanged.
      - List content: bare `str` items and `{'type': 'text', ...}` dicts are
        joined into `response.content`; every other block (reasoning, etc.) is
        preserved on `response.additional_kwargs['reasoning']`.

    Decision LOCKED: reasoning blocks are preserved, never dropped.
    """
    content = getattr(response, "content", None)
    if not isinstance(content, list):
        return response

    text_parts: list[str] = []
    reasoning_blocks: list[Any] = []
    for block in content:
        if isinstance(block, str):
            text_parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        else:
            reasoning_blocks.append(block)

    response.content = "".join(text_parts)
    if reasoning_blocks:
        if response.additional_kwargs is None:
            response.additional_kwargs = {}
        response.additional_kwargs["reasoning"] = reasoning_blocks
    return response
