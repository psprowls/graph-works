"""The dispatch table: packaged defaults and workspace overrides by attributes."""

from __future__ import annotations


def test_the_quoted_log_tail_length_is_the_code_default():
    import inspect

    from gw_orchestrate.gate_receipts import sanitize_tail

    assert inspect.signature(sanitize_tail).parameters["lines"].default == 40
