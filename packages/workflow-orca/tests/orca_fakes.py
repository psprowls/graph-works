"""The test transport: replay captured JSON, record the argv that asked for it.

Argv is the actual contract with Orca, so the suite asserts it directly rather
than asserting a façade's method calls. `FakeRunner` matches an argv
subsequence — `("task-list",)` matches any `task-list` call, and
`("worker-start", "--worktree", "new-child")` matches only that one — so a
test states the discriminating flags and stays silent about the rest.

An unmatched call is an `AssertionError` naming the argv, never a default
empty response: a silently-defaulted call is how a wrong command passes a
test that was written to catch it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from workflow_orca._cli import OrcaResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name: str) -> str:
    """The raw text of a fixture, parsed once to prove it still parses."""
    text = (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
    json.loads(text)  # a fixture that no longer parses fails here, loudly
    return text


def fixture_result(name: str) -> dict:
    """The `result` block of a fixture, for tests that assert against it."""
    return json.loads(fixture(name))["result"]


class FakeRunner:
    """Replay fixtures by argv subsequence and record every call."""

    def __init__(self, routes: Sequence[tuple[Sequence[str], str]]) -> None:
        #: (argv subsequence, fixture name). First match wins, so a caller
        #: puts the more specific route first.
        self.routes = [(tuple(pattern), name) for pattern, name in routes]
        self.calls: list[tuple[str, ...]] = []
        self.stderr = ""

    def __call__(self, argv: Sequence[str]) -> OrcaResult:
        argv = tuple(argv)
        self.calls.append(argv)
        for pattern, name in self.routes:
            if _is_subsequence(pattern, argv):
                return OrcaResult(returncode=0, stdout=fixture(name), stderr=self.stderr)
        raise AssertionError(f"FakeRunner has no route for {argv!r}")

    def calls_matching(self, *tokens: str) -> list[tuple[str, ...]]:
        return [c for c in self.calls if _is_subsequence(tokens, c)]

    def argv_after(self, flag: str, call: tuple[str, ...]) -> str:
        return call[call.index(flag) + 1]


def _is_subsequence(pattern: Sequence[str], argv: Sequence[str]) -> bool:
    """Every token in `pattern`, in order, somewhere in `argv`."""
    it = iter(argv)
    return all(token in it for token in pattern)
