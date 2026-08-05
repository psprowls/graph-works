"""Local LLM token counting for span-bearing code-graph nodes.

Uses tiktoken's ``o200k_base`` encoding (the GPT-4o-family BPE) as a
deterministic, offline-cacheable proxy for Claude's true token counts. It is
~10-20% off Claude but far closer than a char/byte heuristic, and — unlike the
Anthropic/Bedrock count-tokens API — requires no network call per node (a graph
build has thousands of nodes).
"""

from __future__ import annotations

import functools

import tiktoken


@functools.lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    """Return the process-cached ``o200k_base`` encoder, built once on first use."""
    return tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    """Return the number of ``o200k_base`` tokens in ``text``."""
    return len(_encoder().encode(text))
