"""Fixtures the ingest-vertical suites share: a fake graph reader, a fake chat
model, and a bundle builder.

No live model call anywhere in this vertical's tests, and no SQLite either --
`code_graph_io.GraphReader` is reached through four methods, and a stub that
implements exactly those four is a sharper contract than a real database.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from okf_ext.bundle import SCHEMA_DIRNAME, SECTIONS_DIRNAME
from okf_ext.schemas import load_schemas
from okf_ext.shape import load_sections

TODAY = date(2026, 8, 13)
AT = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


class FakeReader:
    """The four `GraphReader` methods this vertical calls, and nothing else."""

    def __init__(
        self,
        *,
        by_path: dict[str, tuple[str, str]] | None = None,
        by_name: dict[str, list[tuple[str, str, str]]] | None = None,
    ) -> None:
        self.by_path = by_path or {}
        self.by_name = by_name or {}
        self.closed = False

    def package_for_file(self, *, path: str) -> tuple[str, str] | None:
        return self.by_path.get(path)

    def entity_by_name(self, *, name: str, kinds: tuple[str, ...] = ()) -> list[tuple[Any, ...]]:
        return [row for row in self.by_name.get(name, []) if not kinds or row[2] in kinds]

    def close(self) -> None:
        self.closed = True


class FakeResponse:
    def __init__(self, content: Any, tool_calls: list[dict[str, Any]] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


class FakeLLM:
    """A chat model that replays a scripted list of responses.

    `bind_tools` returns `self` so the same fake serves `agent_loop.run_tool_loop`
    and a bare `ainvoke` call site.
    """

    def __init__(self, *responses: Any, fail: bool = False) -> None:
        self.responses = list(responses)
        self.fail = fail
        self.calls: list[list[Any]] = []

    def bind_tools(self, tools: list[Any]) -> FakeLLM:
        self.bound = tools
        return self

    async def ainvoke(self, messages: list[Any]) -> FakeResponse:
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("model unavailable")
        item = self.responses.pop(0) if self.responses else ""
        return item if isinstance(item, FakeResponse) else FakeResponse(item)


def declarations(root: Path) -> tuple[Any, Any]:
    """The bundle's `schema/` and `sections/`, loaded."""
    return load_schemas(root / SCHEMA_DIRNAME), load_sections(root / SECTIONS_DIRNAME)


def json_fence(payload: Any) -> str:
    """A model response that wraps its JSON in a markdown fence, as they do."""
    return "```json\n" + json.dumps(payload, indent=2) + "\n```"
