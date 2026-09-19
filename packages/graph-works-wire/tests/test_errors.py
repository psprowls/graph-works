"""The one refusal envelope every interface emits (D-004 §3.2)."""

from __future__ import annotations

import json

import pytest
from graph_works_wire.errors import REASONS, error_envelope


def test_the_reason_vocabulary_is_closed() -> None:
    assert (
        frozenset(
            {
                "refused",
                "incomplete-apply",
                "conflict",
                "incomplete",
                "usage",
                "workspace",
                "unresolved",
                "not-a-repo",
                "io",
                "stale-plan",
            }
        )
        == REASONS
    )


def test_the_envelope_is_single_keyed_and_plain_json() -> None:
    doc = error_envelope(command="work next", reason="unresolved", message="m", exit_code=7, payload=None)
    assert doc == {
        "error": {"command": "work next", "reason": "unresolved", "message": "m", "exit_code": 7, "payload": None}
    }
    assert json.loads(json.dumps(doc)) == doc


def test_a_stale_plan_envelope_carries_its_fresh_plan() -> None:
    fresh = {"as_of": "2026-09-18T14:03:07Z", "digest": "sha256:ab", "plan": {}}
    doc = error_envelope(command="work advance", reason="stale-plan", message="m", exit_code=1, payload=fresh)
    assert doc == {
        "error": {
            "command": "work advance",
            "reason": "stale-plan",
            "message": "m",
            "exit_code": 1,
            "payload": fresh,
        }
    }


def test_an_unknown_reason_is_refused() -> None:
    with pytest.raises(ValueError, match="closed reason vocabulary"):
        error_envelope(command="c", reason="oops", message="m", exit_code=1, payload=None)
