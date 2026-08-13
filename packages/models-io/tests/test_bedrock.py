"""Unit tests for models_io.bedrock.

Covers the BedrockAccessDenied error-wrapping path and content normalization.
No real Bedrock calls — all network paths are mocked via a stub `_original_invoke`.
"""

from __future__ import annotations

import botocore.exceptions
import pytest
from models_io import BedrockAccessDenied
from models_io.bedrock import build

# Model ID used for all generic guard tests — chosen to be cheap/fast.
PREFLIGHT_ARN = "qwen.qwen3-32b-v1:0"


def _build_client_error(code: str) -> botocore.exceptions.ClientError:
    return botocore.exceptions.ClientError(
        {"Error": {"Code": code, "Message": "denied"}},
        "InvokeModel",
    )


def test_invoke_wraps_access_denied_with_arn_and_iam_action(monkeypatch):
    """An AccessDeniedException from boto3 becomes a BedrockAccessDenied whose
    message names the attempted ARN AND the bedrock:InvokeModel action AND the
    foundation-model ARN pattern.
    """

    def raise_access_denied(*a, **kw):
        raise _build_client_error("AccessDeniedException")

    llm = build(PREFLIGHT_ARN)
    monkeypatch.setattr(llm, "_original_invoke", raise_access_denied)

    with pytest.raises(BedrockAccessDenied) as exc_info:
        llm.invoke("ping")

    msg = str(exc_info.value)
    assert PREFLIGHT_ARN in msg
    assert "bedrock:InvokeModel" in msg
    assert "arn:aws:bedrock:*::foundation-model/*" in msg


def test_invoke_passes_through_non_access_denied_client_error(monkeypatch):
    """A ClientError whose Code is NOT AccessDeniedException must re-raise unchanged."""

    def raise_validation(*a, **kw):
        raise _build_client_error("ValidationException")

    llm = build(PREFLIGHT_ARN)
    monkeypatch.setattr(llm, "_original_invoke", raise_validation)

    with pytest.raises(botocore.exceptions.ClientError) as exc_info:
        llm.invoke("ping")
    assert exc_info.value.response["Error"]["Code"] == "ValidationException"


def test_invoke_returns_underlying_result_on_success(monkeypatch):
    """When the underlying invoke succeeds, the wrapped invoke returns the same value."""
    sentinel = object()
    llm = build(PREFLIGHT_ARN)
    monkeypatch.setattr(llm, "_original_invoke", lambda *a, **kw: sentinel)

    assert llm.invoke("ping") is sentinel


# ---------------------------------------------------------------------------
# Content-shape normalization at the model boundary.
#
# Bedrock's Converse API returns `response.content` as a list of content blocks
# (reasoning + text) for "thinking" models. `normalize_content` collapses that
# to a plain `str` on `.content` and preserves the dropped reasoning blocks on
# `additional_kwargs["reasoning"]`. The trigger is content SHAPE, not model id.
# ---------------------------------------------------------------------------

_REASONING_BLOCK = {
    "type": "reasoning_content",
    "reasoning_content": {"type": "text", "text": "thinking...", "signature": "sig"},
}


def _list_shaped_message():
    """A real AIMessage whose `.content` is a list of [reasoning, text, text]."""
    from langchain_core.messages import AIMessage

    return AIMessage(
        content=[
            _REASONING_BLOCK,
            {"type": "text", "text": "Hello"},
            {"type": "text", "text": " world"},
        ]
    )


def test_invoke_normalizes_list_content_and_preserves_reasoning(monkeypatch):
    """Sync path: list-shaped content collapses to a concatenated str and the
    reasoning block is preserved on additional_kwargs['reasoning']."""
    llm = build(PREFLIGHT_ARN)
    monkeypatch.setattr(llm, "_original_invoke", lambda *a, **kw: _list_shaped_message())

    result = llm.invoke("ping")
    assert result.content == "Hello world"
    assert result.additional_kwargs["reasoning"] == [_REASONING_BLOCK]


async def test_ainvoke_normalizes_list_content_and_preserves_reasoning(monkeypatch):
    """Async path: ainvoke normalizes the same list-shaped content."""
    llm = build(PREFLIGHT_ARN)

    async def _fake_ainvoke(*a, **kw):
        return _list_shaped_message()

    monkeypatch.setattr(llm, "_original_ainvoke", _fake_ainvoke)

    result = await llm.ainvoke("ping")
    assert result.content == "Hello world"
    assert result.additional_kwargs["reasoning"] == [_REASONING_BLOCK]


def test_invoke_passes_through_string_content_unchanged(monkeypatch):
    """A string-content AIMessage stays a str and gains no 'reasoning' key."""
    from langchain_core.messages import AIMessage

    llm = build(PREFLIGHT_ARN)
    monkeypatch.setattr(llm, "_original_invoke", lambda *a, **kw: AIMessage("plain text"))

    result = llm.invoke("ping")
    assert result.content == "plain text"
    assert isinstance(result.content, str)
    assert "reasoning" not in result.additional_kwargs


async def test_ainvoke_wraps_access_denied_with_arn(monkeypatch):
    """Async path: an AccessDeniedException ClientError becomes a
    BedrockAccessDenied whose message names the attempted ARN."""
    llm = build(PREFLIGHT_ARN)

    async def _raise_access_denied(*a, **kw):
        raise _build_client_error("AccessDeniedException")

    monkeypatch.setattr(llm, "_original_ainvoke", _raise_access_denied)

    with pytest.raises(BedrockAccessDenied) as exc_info:
        await llm.ainvoke("ping")
    assert PREFLIGHT_ARN in str(exc_info.value)
