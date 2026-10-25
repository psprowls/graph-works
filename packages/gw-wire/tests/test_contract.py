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
from gw_wire import agent_config, code, config, events, repo, util, wiki, work
from samples_agent_config import AGENT_CONFIG
from samples_code import CODE
from samples_config import CONFIG
from samples_events import EVENTS
from samples_repo import REPO
from samples_util import UTIL
from samples_wiki import WIKI
from samples_work import WORK

MODULES: tuple[ModuleType, ...] = (work, wiki, config, util, agent_config, events, code, repo)

EXPECTED: set[str] = {
    "repo.repo_adopt_payload",
    "repo.repo_add_payload",
    "repo.repo_restore_payload",
    "repo.repo_advance_payload",
    "events.change_event_payload",
    "events.changes_payload",
    "agent_config.agent_config_payload",
    "code.excerpt_payload",
    "code.code_graph_tree_payload",
    "code.code_graph_search_payload",
    "code.code_graph_neighborhood_payload",
    "config.dispatch_rules_payload",
    "config.hooks_payload",
    "config.projection_payload",
    "config.resolved_list_payload",
    "config.resolved_payload",
    "config.rule_payload",
    "config.schema_read_payload",
    "config.schema_refresh_payload",
    "util.line_endings_payload",
    "util.log_read_payload",
    "util.log_payload",
    "util.platform_payload",
    "util.tokens_payload",
    "util.read_index_payload",
    "wiki.bootstrap_payload",
    "wiki.bootstrap_plan_payload",
    "wiki.claims_closure_payload",
    "wiki.claims_refresh_payload",
    "wiki.claims_rows_payload",
    "wiki.drift_brief_payload",
    "wiki.drift_payload",
    "wiki.ingest_brief_payload",
    "wiki.ingest_payload",
    "wiki.lint_payload",
    "wiki.page_payload",
    "wiki.citations_payload",
    "wiki.proposal_checks_payload",
    "wiki.proposal_decide_payload",
    "wiki.proposal_file_payload",
    "wiki.proposal_payload",
    "wiki.proposal_preview_payload",
    "wiki.proposals_payload",
    "wiki.query_brief_payload",
    "wiki.query_payload",
    "wiki.wiki_lint_payload",
    "wiki.repo_scan_payload",
    "wiki.scan_apply_payload",
    "wiki.scan_emit_payload",
    "wiki.scan_normal_payload",
    "wiki.section_write_payload",
    "wiki.stats_payload",
    "wiki.tag_inventory_payload",
    "wiki.tags_undeclared_payload",
    "wiki.wiki_tree_payload",
    "work.accept_integration_payload",
    "work.advance_payload",
    "work.archive_payload",
    "work.ask_answer_payload",
    "work.ask_payload",
    "work.decision_payload",
    "work.descent_payload",
    "work.dispatch_explain_payload",
    "work.dispatch_payload",
    "work.file_payload",
    "work.gate_check_payload",
    "work.gate_run_payload",
    "work.gate_wait_payload",
    "work.ingest_queue_payload",
    "work.integrate_payload",
    "work.item_payload",
    "work.lint_payload",
    "work.next_payload",
    "work.normalized_payload",
    "work.obligation_payload",
    "work.open_decisions_payload",
    "work.orchestrate_payload",
    "work.overturn_payload",
    "work.path_mutation_payload",
    "work.place_payload",
    "work.migrate_placement_payload",
    "work.reconcile_payload",
    "work.regen_index_payload",
    "work.reroute_payload",
    "work.status_payload",
    "work.touch_active_work_payload",
    "work.wait_payload",
    "work.work_affecting_payload",
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
    **REPO,
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


def test_orchestration_projects_repository_identity_and_placement() -> None:
    payload = WORK["work.orchestrate_payload"][0]()
    assert payload["repo"]["path"] == "/code"
    assert payload["dispatches"][0]["repo"] == {"name": "ui", "path": "/ui", "source": "item"}
    assert "preparations" not in payload
    placement = payload["dispatches"][0]["placement"]
    assert placement["repo"] == {"name": "ui", "path": "/ui"}
    assert placement["parent_branch"] == "main"
    assert "workspace_preparations" not in payload
    assert "workspace" not in payload["dispatches"][0]
    assert "preparations" not in WORK["work.orchestrate_payload"][1]()


def test_repo_adopt_payload_shape() -> None:
    assert SAMPLES["repo.repo_adopt_payload"][0]() == {
        "name": "demo",
        "source": "../demo",
        "clone": "okf/repositories/demo/references/git",
        "checkout": ".gw/worktrees/demo/main",
        "track": "main",
        "commit": "0" * 40,
        "checkout_created": True,
        "repaired": ["/ws/.gw/worktrees/legacy/wt0"],
        "paths": ["log.md", "repositories/demo.md", "repositories/index.md", "workspace.yaml"],
        "dry_run": False,
        "ok": True,
        "refusal": None,
        "workspace_commit": {
            "status": "committed",
            "sha": "1" * 40,
            "subject": "workspace: adopt managed repository demo",
            "paths": ["log.md"],
            "reason": None,
        },
        "warnings": [],
    }


def test_schema_refresh_payload_shape() -> None:
    assert SAMPLES["config.schema_refresh_payload"][2]() == {
        "declarations_dir": "/ws/.gw",
        "applied": True,
        "force": False,
        "writes": [{"path": "schema/A.schema.json", "diff": "+{}", "created": True}],
        "refusals": [{"path": "schema/B.schema.json", "reason": "edited", "detail": "d", "diff": "-x"}],
        "skipped": ["schema/C.schema.json"],
        "provenance": True,
        "written": ["a"],
        "commit": {"status": "committed", "sha": "1" * 40, "reason": None},
    }


def test_migrate_placement_payload_explicit_shape() -> None:
    plan, applied, empty = (sample() for sample in WORK["work.migrate_placement_payload"])
    assert plan == {
        "mode": "plan",
        "nothing_to_migrate": False,
        "refusal": None,
        "rows": [
            {
                "item": "work/a",
                "repo": "code",
                "worktree": "/wt/a",
                "branch": "feature/a",
                "action": "remove-worktree",
                "reason": None,
            },
            {
                "item": "work/a",
                "repo": "code",
                "worktree": "/wt/a",
                "branch": "feature/a",
                "action": "delete-branch",
                "reason": "receipt",
            },
            {
                "item": "work/b",
                "repo": "code",
                "worktree": "/wt/b",
                "branch": "feature/b",
                "action": "keep",
                "reason": "dirty",
            },
        ],
        "blockers": [
            {
                "item": "work/c",
                "repo": "code",
                "worktree": "/wt/c",
                "legacy_branch": "orca/c",
                "computed_branch": "feature/c",
                "commands": ["git -C /wt/c branch -m orca/c feature/c", "gw work place work/c"],
            }
        ],
        "orphans": [{"repo": "code", "branch": "psprowls/gw-reader-1"}],
        "strips": ["work/a", "work/b", "work/c"],
        "workspace_stamps": [{"item": "work/c", "worktree": "/ws/c", "branch": "ws/c", "ahead": 3}],
        "result": None,
    }
    assert applied["mode"] == "apply" and applied["result"] == {
        "removed_worktrees": ["/wt/a"],
        "deleted_branches": ["feature/a"],
        "stripped": ["work/a", "work/b"],
        "failed_step": None,
        "commit": {
            "status": "committed",
            "sha": "1" * 40,
            "subject": "workspace: migrate-placement strip retired placement fields",
            "paths": ["okf/work/a.md"],
            "reason": None,
        },
    }
    assert empty["nothing_to_migrate"] is True and empty["rows"] == [] and empty["refusal"] is None


def test_place_payload_explicit_shape() -> None:
    assert WORK["work.place_payload"][0]() == {
        "path": "work/e/children/a",
        "mode": "ensure",
        "dry_run": False,
        "repo": {"name": "code", "path": "/code"},
        "placement": {
            "branch": "feature/a",
            "worktree": "/wt/feature/a",
            "parent_branch": "epic/e",
            "parent_worktree": "/wt/epic/e",
            "owner_path": "work/e",
            "slug": "a",
        },
        "chain": [
            {
                "path": "work/e",
                "repo": "code",
                "branch": "epic/e",
                "worktree": "/wt/epic/e",
                "action": "verified",
                "refresh": None,
                "orca": "already registered",
            },
            {
                "path": "work/e/children/a",
                "repo": "code",
                "branch": "feature/a",
                "worktree": "/wt/feature/a",
                "action": "created",
                "refresh": {"kind": "ff", "from": "old", "to": "new"},
                "orca": "registered",
            },
        ],
        "start_sha": {"before": None, "after": "new"},
        "workspace_commit": {"status": "committed", "sha": "ws-sha", "paths": ["okf/work/a.md"]},
        "dirty_workspace_paths": ["notes.md"],
        "pruned": None,
        "refusal": None,
        "warnings": ["note", "apply: write failed"],
    }


def test_place_payload_refusals_and_predictions() -> None:
    start = WORK["work.place_payload"][1]()
    assert start["mode"] == "start"
    assert start["refusal"] == {"kind": "already-started", "detail": "baseline exists"}
    assert start["start_sha"] == {"before": "old", "after": "old"}
    conflict = WORK["work.place_payload"][2]()
    assert conflict["dry_run"] is True
    assert conflict["chain"][0]["refresh"] == {"kind": "conflict", "from": None, "to": None}
    assert conflict["chain"][0]["orca"] is None
    assert conflict["refusal"] == {"kind": "refresh-conflict", "detail": "conflicts: a.py"}
    assert conflict["warnings"] == ["note"]


def test_place_payload_remove_prune_and_no_repo() -> None:
    removed = WORK["work.place_payload"][3]()
    assert removed["mode"] == "remove"
    assert removed["chain"][0]["action"] == "removed"
    pruned = WORK["work.place_payload"][4]()
    assert pruned["path"] is None
    assert pruned["pruned"] == {
        "removed_worktrees": ["/wt/old"],
        "deleted_branches": ["feature/old"],
        "kept": [
            {"path": "work/e", "repo": "code", "branch": "epic/e", "why": "open"},
            {"path": "work/occupied", "repo": "code", "branch": "feature/occupied", "why": "occupied"},
        ],
    }
    assert WORK["work.place_payload"][5]() == {
        "path": "work/note",
        "mode": "ensure",
        "dry_run": False,
        "repo": None,
        "placement": None,
        "chain": [],
        "start_sha": None,
        "workspace_commit": None,
        "dirty_workspace_paths": [],
        "pruned": None,
        "refusal": None,
        "warnings": ["no repo"],
    }
