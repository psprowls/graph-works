from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_llm_response():
    resp = MagicMock()
    resp.content = "mocked response"
    resp.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    return resp


@pytest.fixture
def fake_llm_response_error():
    resp = MagicMock()
    resp.content = ""
    resp.usage_metadata = None
    return resp


@pytest.fixture
def make_task(fake_llm_response):
    def _make(*, raise_for=frozenset()):
        async def task(item):
            if item in raise_for:
                raise ValueError(f"Intentional failure for item: {item}")
            return fake_llm_response

        return task

    return _make
