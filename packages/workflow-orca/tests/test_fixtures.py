"""Every fixture is a complete envelope that still parses."""

from __future__ import annotations

import json

import pytest
from orca_fakes import FIXTURES, FakeRunner, fixture

#: Captured refusals: a complete `{"id","ok","error"}` envelope with `ok: false`.
ERROR_FIXTURES = frozenset({"check_consumer_fenced"})
NAMES = sorted(p.stem for p in FIXTURES.glob("*.json") if p.stem not in ERROR_FIXTURES)


def test_the_fixture_walk_found_something():
    # Guards the guard: an empty NAMES would make the next test vacuous.
    assert len(NAMES) >= 13


@pytest.mark.parametrize("name", NAMES)
def test_every_fixture_is_a_complete_envelope(name):
    # Captured verbatim means the whole `{"id","ok","result"}` wrapper, not a
    # bare result block — `unwrap` is what the suite exercises, and it needs
    # the envelope to have something to open.
    body = json.loads(fixture(name))
    assert set(body) >= {"id", "ok", "result"}
    assert body["ok"] is True


@pytest.mark.parametrize("name", sorted(ERROR_FIXTURES))
def test_every_error_fixture_is_a_complete_refusal(name):
    body = json.loads(fixture(name))
    assert set(body) >= {"id", "ok", "error"}
    assert body["ok"] is False
    assert isinstance(body["error"].get("code"), str)


def test_the_fake_runner_refuses_an_unrouted_call():
    runner = FakeRunner([(("run-list",), "run_list")])
    with pytest.raises(AssertionError, match="no route"):
        runner(["orca", "orchestration", "task-list", "--json"])


def test_the_fake_runner_matches_a_subsequence_and_records_the_call():
    runner = FakeRunner([(("run-list",), "run_list")])
    runner(["orca", "orchestration", "run-list", "--limit", "50", "--json"])
    assert runner.calls_matching("run-list", "--limit")
