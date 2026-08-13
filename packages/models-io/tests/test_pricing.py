"""Unit tests for models_io.pricing.

All tests are deterministic and require no Bedrock access.
"""

from __future__ import annotations

import pytest
from models_io.pricing import PRICES, UnknownModelError, cost_for_usage


def test_nova_pro_cost() -> None:
    """Nova Pro: $0.80/M input + $3.20/M output -> $4.00 for 1M each."""
    result = cost_for_usage(
        "us.amazon.nova-pro-v1:0",
        {"input": 1_000_000, "output": 1_000_000},
    )
    assert result == pytest.approx(4.0)


def test_nova_lite_cost() -> None:
    """Nova Lite: $0.30/M input + $1.20/M output -> $1.50 for 1M each."""
    result = cost_for_usage(
        "us.amazon.nova-lite-v1:0",
        {"input": 1_000_000, "output": 1_000_000},
    )
    assert result == pytest.approx(1.5)


def test_qwen3_cost() -> None:
    """Qwen3-32B: $0.15/M input + $0.60/M output -> $0.75 for 1M each."""
    result = cost_for_usage(
        "qwen.qwen3-32b-v1:0",
        {"input": 1_000_000, "output": 1_000_000},
    )
    assert result == pytest.approx(0.75)


def test_unknown_model_raises() -> None:
    """Unknown model ID raises UnknownModelError."""
    with pytest.raises(UnknownModelError):
        cost_for_usage("unknown-model", {"input": 100, "output": 100})


def test_all_five_bedrock_models_present() -> None:
    """PRICES must contain all 5 Bedrock model IDs required by consumers (eval-harness sweep candidates)."""
    required = {
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "us.anthropic.claude-sonnet-4-6",
        "us.amazon.nova-pro-v1:0",
        "us.amazon.nova-lite-v1:0",
        "qwen.qwen3-32b-v1:0",
    }
    assert required <= PRICES.keys()


def test_nova_micro_fallback_present() -> None:
    """Nova Micro fallback model must be in PRICES."""
    assert "us.amazon.nova-micro-v1:0" in PRICES


def test_zero_tokens_is_zero_cost() -> None:
    """Zero usage yields zero cost for any model."""
    result = cost_for_usage("us.amazon.nova-pro-v1:0", {"input": 0, "output": 0})
    assert result == 0.0


def test_partial_usage_keys() -> None:
    """Missing usage keys default to 0 (only input provided)."""
    result = cost_for_usage("us.amazon.nova-pro-v1:0", {"input": 1_000_000})
    assert result == pytest.approx(0.80)


def test_accepts_any_mapping_not_just_dict() -> None:
    """`usage` is a Mapping so `cost_for_usage` is assignable to subagents_io.PriceLookup."""
    from types import MappingProxyType

    result = cost_for_usage(
        "us.amazon.nova-pro-v1:0",
        MappingProxyType({"input": 1_000_000, "output": 1_000_000}),
    )
    assert result == pytest.approx(4.0)
