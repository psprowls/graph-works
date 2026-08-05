"""Unit tests for code_graph_io.tokens — local o200k_base token counting."""

from __future__ import annotations

from code_graph_io import tokens


def test_count_tokens_empty_is_zero() -> None:
    assert tokens.count_tokens("") == 0


def test_count_tokens_single_common_word() -> None:
    # 'hello' is a single common BPE token in o200k_base.
    assert tokens.count_tokens("hello") == 1


def test_count_tokens_deterministic() -> None:
    snippet = "def foo() -> int:\n    return 1\n"
    assert tokens.count_tokens(snippet) == tokens.count_tokens(snippet)


def test_count_tokens_grows_with_text() -> None:
    assert tokens.count_tokens("a") < tokens.count_tokens("a b c d e f g")


def test_encoder_is_cached() -> None:
    assert tokens._encoder() is tokens._encoder()
