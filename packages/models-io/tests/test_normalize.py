"""Direct unit tests for the shared content normalizer.

`normalize_content` is reachable through both provider guards, but two of its
branches are not: `additional_kwargs is None` never happens on a real
`AIMessage`, and bare-`str` content blocks come from providers that neither
guard test simulates. Testing the function directly is what covers them — and
what keeps the 95% floor off the ported tests' shoulders alone.
"""

from __future__ import annotations

from typing import Any

from models_io.normalize import normalize_content

REASONING_BLOCK = {"type": "reasoning_content", "reasoning_content": {"type": "text", "text": "t", "signature": "s"}}


class _Response:
    """The smallest thing `normalize_content` will accept: content plus kwargs."""

    def __init__(self, content: Any, additional_kwargs: Any = None) -> None:
        self.content = content
        self.additional_kwargs = additional_kwargs


def test_non_list_content_is_returned_unchanged():
    response = _Response("plain text", additional_kwargs={})
    assert normalize_content(response) is response
    assert response.content == "plain text"
    assert response.additional_kwargs == {}


def test_an_object_with_no_content_attribute_is_returned_unchanged():
    class _Bare:
        pass

    response = _Bare()
    assert normalize_content(response) is response


def test_bare_string_blocks_are_joined():
    response = _Response(["Hello", " world"], additional_kwargs={})
    assert normalize_content(response).content == "Hello world"
    assert "reasoning" not in response.additional_kwargs


def test_reasoning_blocks_land_on_a_freshly_created_additional_kwargs():
    response = _Response([REASONING_BLOCK, {"type": "text", "text": "hi"}], additional_kwargs=None)
    assert normalize_content(response).content == "hi"
    assert response.additional_kwargs == {"reasoning": [REASONING_BLOCK]}


def test_a_text_block_without_a_text_key_contributes_the_empty_string():
    response = _Response([{"type": "text"}], additional_kwargs={})
    assert normalize_content(response).content == ""
