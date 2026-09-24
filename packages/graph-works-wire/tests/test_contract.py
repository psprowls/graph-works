"""Every public projection is enumerated here, and every one returns plain JSON data.

Adding a `*_payload` function to any wire module fails
`test_every_projection_is_enumerated` until it is added to `EXPECTED` *and*
given at least one sample in the matching `samples_<module>.py`.
"""

from __future__ import annotations

import inspect
import json
import math
from collections.abc import Callable
from types import ModuleType

import pytest
from graph_works_wire import agent_config, code, config, events, util, wiki, work
from samples_agent_config import AGENT_CONFIG
from samples_code import CODE
from samples_config import CONFIG
from samples_events import EVENTS
from samples_util import UTIL
from samples_wiki import WIKI
from samples_work import WORK

MODULES: tuple[ModuleType, ...] = (work, wiki, config, util, agent_config, events, code)

EXPECTED: set[str] = {
    "events.change_event_payload",
    "events.changes_payload",
    "agent_config.agent_config_payload",
    "code.excerpt_payload",
    "config.dispatch_rules_payload",
    "config.hooks_payload",
    "config.projection_payload",
    "config.resolved_list_payload",
    "config.resolved_payload",
    "config.rule_payload",
    "config.schema_read_payload",
    "util.line_endings_payload",
    "util.log_read_payload",
    "util.log_payload",
    "util.platform_payload",
    "util.tokens_payload",
    "wiki.bootstrap_payload",
    "wiki.bootstrap_plan_payload",
    "wiki.drift_brief_payload",
    "wiki.drift_payload",
    "wiki.ingest_brief_payload",
    "wiki.ingest_payload",
    "wiki.lint_payload",
    "wiki.page_payload",
    "wiki.citations_payload",
    "wiki.proposal_decide_payload",
    "wiki.proposal_file_payload",
    "wiki.proposal_payload",
    "wiki.proposals_payload",
    "wiki.query_brief_payload",
    "wiki.query_payload",
    "wiki.scan_apply_payload",
    "wiki.scan_emit_payload",
    "wiki.scan_normal_payload",
    "wiki.stats_payload",
    "wiki.tag_inventory_payload",
    "wiki.tags_undeclared_payload",
    "wiki.wiki_tree_payload",
    "work.advance_payload",
    "work.archive_payload",
    "work.decision_payload",
    "work.descent_payload",
    "work.dispatch_explain_payload",
    "work.dispatch_payload",
    "work.file_payload",
    "work.ingest_queue_payload",
    "work.item_payload",
    "work.lint_payload",
    "work.next_payload",
    "work.normalized_payload",
    "work.open_decisions_payload",
    "work.orchestrate_payload",
    "work.overturn_payload",
    "work.path_mutation_payload",
    "work.placement_payload",
    "work.reconcile_payload",
    "work.regen_index_payload",
    "work.status_payload",
    "work.touch_active_work_payload",
    "work.work_list_payload",
    "work.work_queue_payload",
}

SAMPLES: dict[str, tuple[Callable[[], object], ...]] = {
    **WORK,
    **WIKI,
    **CONFIG,
    **UTIL,
    **AGENT_CONFIG,
    **EVENTS,
    **CODE,
}


def discovered() -> set[str]:
    return {
        f"{module.__name__.rsplit('.', 1)[1]}.{name}"
        for module in MODULES
        for name, value in vars(module).items()
        if name.endswith("_payload")
        and not name.startswith("_")
        and inspect.isfunction(value)
        and value.__module__ == module.__name__
    }


def assert_plain(value: object, where: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            assert isinstance(key, str), f"{where}: non-str key {key!r}"
            assert_plain(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_plain(item, f"{where}[{index}]")
    else:
        assert value is None or type(value) in (str, int, float, bool), f"{where}: {type(value).__name__}"


def assert_round_trip_equal(actual: object, expected: object, where: str = "$") -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{where}: round trip changed dict to {type(actual).__name__}"
        assert actual.keys() == expected.keys(), f"{where}: round trip changed keys"
        for key, item in expected.items():
            assert_round_trip_equal(actual[key], item, f"{where}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{where}: round trip changed list to {type(actual).__name__}"
        assert len(actual) == len(expected), f"{where}: round trip changed list length"
        for index, item in enumerate(expected):
            assert_round_trip_equal(actual[index], item, f"{where}[{index}]")
    elif isinstance(expected, float) and math.isnan(expected):
        assert isinstance(actual, float) and math.isnan(actual), f"{where}: NaN was not preserved"
    else:
        assert actual == expected, f"{where}: {actual!r} != {expected!r}"


def test_every_projection_is_enumerated() -> None:
    assert discovered() == EXPECTED


def test_every_enumerated_projection_has_a_sample() -> None:
    assert set(SAMPLES) == EXPECTED
    assert all(SAMPLES.values())


@pytest.mark.parametrize(
    ("name", "index"),
    [(name, index) for name, thunks in sorted(SAMPLES.items()) for index in range(len(thunks))],
)
def test_projection_is_plain_json_data(name: str, index: int) -> None:
    payload = SAMPLES[name][index]()
    assert_plain(payload)
    assert_round_trip_equal(json.loads(json.dumps(payload)), payload)


def test_orchestration_projects_repository_identity_and_preparations() -> None:
    payload = WORK["work.orchestrate_payload"][0]()
    assert payload["repo"]["path"] == "/code"
    assert payload["dispatches"][0]["repo"] == {"name": "ui", "path": "/ui", "source": "item"}
    preparation = payload["preparations"][0]
    assert preparation["owner_phase"] == "execute"
    assert preparation["owner_path"] == "work/e"
    assert preparation["repo"] == payload["dispatches"][0]["repo"]
    assert preparation["branch"] == "epic/e"
    assert preparation["base_branch"] == "main"
    assert preparation["worktree"]["action"] == "create"
    assert WORK["work.orchestrate_payload"][1]()["preparations"] == []
