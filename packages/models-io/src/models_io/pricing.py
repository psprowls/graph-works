"""Hardcoded model pricing for Bedrock models. Update manually when AWS changes prices.

Prices are USD per million tokens, current as of 2026-05-14.
Ported from lattice-evals/pricing.py and extended with Bedrock model IDs.

2026-05-29 additions from bedrock-models-considering.json: new sweep candidates and
updated judge model (Mistral Large 3). Cache keys omitted for non-Claude Bedrock models.
"""

from __future__ import annotations

from collections.abc import Mapping


class UnknownModelError(KeyError):
    pass


# USD per 1M tokens; verified against AWS pricing page and Anthropic pricing on 2026-05-14.
# Bedrock non-Claude models (Nova, Qwen) do not support prompt caching — no cache keys.
PRICES: dict[str, dict[str, float]] = {
    # Claude models via direct Anthropic API (from lattice-evals port, cache keys included)
    "claude-opus-4-7": {
        "input": 15.0,
        "output": 75.0,
        "cache_read": 1.50,
        "cache_write": 18.75,
    },
    "claude-sonnet-4-6": {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.30,
        "cache_write": 3.75,
    },
    "claude-haiku-4-5": {
        "input": 1.0,
        "output": 5.0,
        "cache_read": 0.10,
        "cache_write": 1.25,
    },
    # Claude models via AWS Bedrock cross-region inference profiles
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": {
        "input": 1.0,
        "output": 5.0,
    },
    "us.anthropic.claude-sonnet-4-6": {
        "input": 3.0,
        "output": 15.0,
    },
    # Amazon Nova models via Bedrock (no prompt caching)
    "us.amazon.nova-pro-v1:0": {
        "input": 0.80,
        "output": 3.20,
    },
    "us.amazon.nova-lite-v1:0": {
        "input": 0.30,
        "output": 1.20,
    },
    "us.amazon.nova-micro-v1:0": {
        "input": 0.035,
        "output": 0.14,
    },
    # Qwen3 via Bedrock (no prompt caching)
    "qwen.qwen3-32b-v1:0": {
        "input": 0.15,
        "output": 0.60,
    },
    # --- 2026-05-29 additions from bedrock-models-considering.json ---
    # Anthropic Haiku via global inference profile (replaces us. prefix in sweep)
    "global.anthropic.claude-haiku-4-5-20251001-v1:0": {
        "input": 1.0,
        "output": 5.0,
    },
    # Mistral models via Bedrock (new judge_a + sweep candidates)
    "mistral.mistral-large-3-675b-instruct": {
        "input": 0.50,
        "output": 1.50,
    },
    "mistral.ministral-3-14b-instruct": {
        "input": 0.20,
        "output": 0.20,
    },
    "mistral.devstral-2-123b": {
        "input": 0.40,
        "output": 2.00,
    },
    # Qwen3 extended models via Bedrock
    "qwen.qwen3-next-80b-a3b": {
        "input": 0.14,
        "output": 1.20,
    },
    "qwen.qwen3-vl-235b-a22b": {
        "input": 0.53,
        "output": 2.66,
    },
    "qwen.qwen3-coder-30b-a3b-v1:0": {
        "input": 0.15,
        "output": 0.60,
    },
    "qwen.qwen3-coder-next": {
        "input": 0.50,
        "output": 1.20,
    },
    # DeepSeek models via Bedrock
    "deepseek.v3.2": {
        "input": 0.62,
        "output": 1.85,
    },
    "us.deepseek.r1-v1:0": {
        "input": 1.35,
        "output": 5.40,
    },
    # Moonshot / Kimi models via Bedrock
    "moonshotai.kimi-k2.5": {
        "input": 0.60,
        "output": 3.00,
    },
    "moonshot.kimi-k2-thinking": {
        "input": 0.60,
        "output": 2.50,
    },
    # ZAI / GLM models via Bedrock
    "zai.glm-5": {
        "input": 1.0,
        "output": 3.20,
    },
    "zai.glm-4.7-flash": {
        "input": 0.07,
        "output": 0.40,
    },
    # OpenAI OSS models via Bedrock
    "openai.gpt-oss-120b-1:0": {
        "input": 0.15,
        "output": 0.60,
    },
    "openai.gpt-oss-20b-1:0": {
        "input": 0.07,
        "output": 0.30,
    },
    # MiniMax models via Bedrock
    "minimax.minimax-m2.5": {
        "input": 0.30,
        "output": 1.20,
    },
}


def cost_for_usage(model: str, usage: Mapping[str, int]) -> float:
    """Return USD cost for token usage on `model`.

    `usage` keys: input, output, cache_read, cache_write (all int token counts).
    Missing keys default to 0.

    `usage` is a `Mapping`, not a `dict`, so this function is assignable to
    `subagents_io.PriceLookup` — callable parameters are contravariant, and a
    function taking `dict` is not.

    Raises:
        UnknownModelError: if model is not in PRICES.
    """
    if model not in PRICES:
        raise UnknownModelError(f"unknown model {model!r}; update models_io/pricing.py")
    p = PRICES[model]
    return sum(usage.get(k, 0) * p[k] / 1_000_000 for k in p)
