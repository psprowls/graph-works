"""`gw util` verbs and the root-level `next` alias (§4.3, §4.5)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any
from typing import Any as _Any

import pytest
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_cli.util_cli import log as log_module
from graph_works_cli.util_cli import main as util_main
from graph_works_cli.util_cli import tokens as tokens_module
from graph_works_cli.util_cli import trace as trace_module
from graph_works_cli.work_cli.main import work_app
from typer.testing import CliRunner

runner = CliRunner()


def write_item(workspace: Path) -> str:
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "Alpha",
            "--kind",
            "Feature",
            "--summary",
            "d",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    return str(json.loads(result.stdout)["path"])


def test_next_is_the_same_callback_as_work_next() -> None:
    """A second implementation would drift; this proves alias, not reimplementation."""
    aliased = util_main.work_next_callback()
    registered = [command for command in work_app.registered_commands if util_main.command_name(command) == "next"]

    assert len(registered) == 1
    assert registered[0].callback is aliased


def test_next_declares_no_file_option() -> None:
    """The contract page lists --file, but nothing in the stack can write a bundle yet."""
    result = runner.invoke(app, ["next", "--help"])

    assert result.exit_code == 0
    assert "--file" not in result.stdout


def test_next_emits_no_guidance_keys(tmp_path: Path) -> None:
    """`gw next`'s guidance half belongs to a different work item; an empty key would lie."""
    workspace = tmp_path / "works"
    bootstrap = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(workspace)])
    assert bootstrap.exit_code == 0, bootstrap.output
    path = write_item(workspace)

    result = runner.invoke(app, ["next", path, "--workspace", str(workspace), "--json"])

    assert result.exit_code == exit_codes.SUCCESS, result.output
    payload = json.loads(result.stdout)
    assert "guidance" not in payload
    assert "guidance_warnings" not in payload
    assert "guidance_file" not in payload


def test_next_and_work_next_produce_identical_json(tmp_path: Path) -> None:
    """The acceptance bar: byte-identical stdout for the same arguments."""
    workspace = tmp_path / "works"
    bootstrap = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(workspace)])
    assert bootstrap.exit_code == 0, bootstrap.output
    path = write_item(workspace)

    via_alias = runner.invoke(app, ["next", path, "--workspace", str(workspace), "--json"])
    via_work = runner.invoke(app, ["work", "next", path, "--workspace", str(workspace), "--json"])

    assert via_alias.exit_code == via_work.exit_code
    assert via_alias.stdout == via_work.stdout


@pytest.fixture
def initialized_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "works"
    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])
    assert result.exit_code == 0
    return root


class _LogResult:
    def __init__(self, *, detail: str | None, written: bool = True) -> None:
        self.path = Path("/w/wiki/log.md")
        self.day = date(2026, 8, 19)
        self.op = "note"
        self.title = "Wave 2 kickoff"
        self.detail = detail
        self.entry = "- **note** Wave 2 kickoff"
        self.written = written


def test_log_forwards_op_title_and_a_supplied_today(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """okf-io never reads the clock; the CLI is the layer that supplies the date."""
    calls: list[tuple[Any, ...]] = []

    def fake_run_log(layout: object, op: str, title: str, detail: str | None, *, today: date) -> _LogResult:
        calls.append((op, title, detail, today))
        return _LogResult(detail=detail)

    monkeypatch.setattr(log_module, "run_log", fake_run_log)

    result = runner.invoke(
        app, ["util", "log", "--op", "note", "--title", "Wave 2 kickoff", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0][:3] == ("note", "Wave 2 kickoff", None)
    assert isinstance(calls[0][3], date)
    assert result.stdout.strip() == "- **note** Wave 2 kickoff"


def test_log_turns_a_blank_detail_into_none(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    """`--detail ''` must not produce a dangling em-dash tail in the entry."""
    seen: list[str | None] = []

    def fake_run_log(layout: object, op: str, title: str, detail: str | None, *, today: date) -> _LogResult:
        seen.append(detail)
        return _LogResult(detail=detail)

    monkeypatch.setattr(log_module, "run_log", fake_run_log)

    result = runner.invoke(
        app,
        ["util", "log", "--op", "note", "--title", "t", "--detail", "", "--workspace", str(initialized_workspace)],
    )

    assert result.exit_code == 0
    assert seen == [None]


def test_log_json_carries_the_seven_result_keys(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    monkeypatch.setattr(log_module, "run_log", lambda *args, **kwargs: _LogResult(detail="work/x.md"))

    result = runner.invoke(
        app,
        [
            "util",
            "log",
            "--op",
            "note",
            "--title",
            "Wave 2 kickoff",
            "--json",
            "--workspace",
            str(initialized_workspace),
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "path": "/w/wiki/log.md",
        "day": "2026-08-19",
        "op": "note",
        "title": "Wave 2 kickoff",
        "detail": "work/x.md",
        "entry": "- **note** Wave 2 kickoff",
        "written": True,
    }


def test_log_rejects_an_unknown_op_through_the_shared_error_helper(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """Validating the op vocabulary is the backing child's job, not the CLI's."""

    def fake_run_log(*args: object, **kwargs: object) -> _LogResult:
        raise ValueError("unknown op: frobnicate")

    monkeypatch.setattr(log_module, "run_log", fake_run_log)

    result = runner.invoke(
        app,
        ["util", "log", "--op", "frobnicate", "--title", "t", "--workspace", str(initialized_workspace)],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "Error: unknown op: frobnicate" in result.stderr


def test_log_treats_a_refused_append_as_a_failure(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    monkeypatch.setattr(log_module, "run_log", lambda *args, **kwargs: _LogResult(detail=None, written=False))

    result = runner.invoke(
        app, ["util", "log", "--op", "note", "--title", "t", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code != 0
    assert "log entry was not written" in result.stderr


def test_log_requires_op_and_title() -> None:
    assert runner.invoke(app, ["util", "log", "--title", "t"]).exit_code != 0
    assert runner.invoke(app, ["util", "log", "--op", "note"]).exit_code != 0


def test_log_json_still_reports_a_refused_write(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    """With --json, the full payload (including written=False) is printed to stdout AND the command exits non-zero."""
    monkeypatch.setattr(log_module, "run_log", lambda *args, **kwargs: _LogResult(detail=None, written=False))

    result = runner.invoke(
        app, ["util", "log", "--op", "note", "--title", "t", "--json", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert payload["written"] is False
    assert "log entry was not written" in result.stderr


def test_log_forwards_a_nonempty_detail_unchanged(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    """A non-empty --detail value is forwarded to run_log unchanged, not collapsed to None."""
    seen: list[str | None] = []

    def fake_run_log(layout: object, op: str, title: str, detail: str | None, *, today: date) -> _LogResult:
        seen.append(detail)
        return _LogResult(detail=detail)

    monkeypatch.setattr(log_module, "run_log", fake_run_log)

    result = runner.invoke(
        app,
        [
            "util",
            "log",
            "--op",
            "note",
            "--title",
            "t",
            "--detail",
            "work/x.md",
            "--workspace",
            str(initialized_workspace),
        ],
    )

    assert result.exit_code == 0
    assert seen == ["work/x.md"]


class _Stamp:
    def __init__(self, page: str, count: int) -> None:
        self.page = page
        self.tokens = count


class _Skipped:
    def __init__(self, page: str, reason: str) -> None:
        self.page = page
        self.reason = reason


class _TokensUpdate:
    def __init__(
        self,
        *,
        updated: tuple[_Stamp, ...] = (),
        unchanged: tuple[_Stamp, ...] = (),
        skipped: tuple[_Skipped, ...] = (),
        dry_run: bool = False,
    ) -> None:
        self.updated = updated
        self.unchanged = unchanged
        self.skipped = skipped
        self.dry_run = dry_run


def test_tokens_dry_run_is_opt_in(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    """Core defaults dry_run=True; a bare `gw util tokens` must actually write."""
    seen: list[bool] = []

    def fake_update(layout: object, *, dry_run: bool) -> _TokensUpdate:
        seen.append(dry_run)
        return _TokensUpdate(dry_run=dry_run)

    monkeypatch.setattr(tokens_module, "run_tokens_update", fake_update)

    assert runner.invoke(app, ["util", "tokens", "--workspace", str(initialized_workspace)]).exit_code == 0
    assert runner.invoke(app, ["util", "tokens", "--dry-run", "--workspace", str(initialized_workspace)]).exit_code == 0
    assert seen == [False, True]


def test_tokens_human_view_caps_each_bucket_at_twenty(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A 400-page vault must not scroll the real result off the screen."""
    updated = tuple(_Stamp(f"concepts/p{index:03d}", index) for index in range(25))
    monkeypatch.setattr(tokens_module, "run_tokens_update", lambda layout, *, dry_run: _TokensUpdate(updated=updated))

    result = runner.invoke(app, ["util", "tokens", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert "updated: 25" in result.stdout
    assert "concepts/p019 (19)" in result.stdout
    assert "concepts/p020" not in result.stdout
    assert "… and 5 more" in result.stdout


def test_tokens_renders_a_skip_reason_next_to_its_page(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    monkeypatch.setattr(
        tokens_module,
        "run_tokens_update",
        lambda layout, *, dry_run: _TokensUpdate(skipped=(_Skipped("concepts/x", "parse-error"),)),
    )

    result = runner.invoke(app, ["util", "tokens", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert "skipped: 1" in result.stdout
    assert "concepts/x (parse-error)" in result.stdout


def test_tokens_json_is_complete_where_the_human_view_is_capped(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    updated = tuple(_Stamp(f"concepts/p{index:03d}", index) for index in range(25))
    monkeypatch.setattr(
        tokens_module,
        "run_tokens_update",
        lambda layout, *, dry_run: _TokensUpdate(
            updated=updated, unchanged=(_Stamp("concepts/u", 7),), skipped=(_Skipped("concepts/x", "unreadable"),)
        ),
    )

    result = runner.invoke(app, ["util", "tokens", "--json", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is False
    assert len(payload["updated"]) == 25
    assert payload["updated"][0] == {"page": "concepts/p000", "tokens": 0}
    assert payload["unchanged"] == [{"page": "concepts/u", "tokens": 7}]
    assert payload["skipped"] == [{"page": "concepts/x", "reason": "unreadable"}]


def test_tokens_surfaces_a_core_failure_through_the_shared_helper(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    def boom(layout: object, *, dry_run: bool) -> _TokensUpdate:
        raise OSError("bundle unreadable")

    monkeypatch.setattr(tokens_module, "run_tokens_update", boom)

    result = runner.invoke(app, ["util", "tokens", "--workspace", str(initialized_workspace)])

    assert result.exit_code != 0
    assert "Error: bundle unreadable" in result.stderr


class _Warning:
    def __init__(self, kind: str, line: int | None = None, observed: int | None = None, detail: str | None = None):
        self.kind = kind
        self.line = line
        self.observed = observed
        self.detail = detail


class _TraceFile:
    def __init__(self, records: tuple[Mapping[str, _Any], ...], warnings: tuple[_Warning, ...] = ()) -> None:
        self.path = Path("t.jsonl")
        self.records = records
        self.warnings = warnings


class _RoleModelTotals:
    def __init__(self, role: str, model_id: str, count: int, cost: float, unknown: int) -> None:
        self.role = role
        self.model_id = model_id
        self.count = count
        self.tokens_in = 10
        self.tokens_out = 5
        self.cost_usd_sum = cost
        self.unknown_cost_count = unknown

    @property
    def fully_unknown(self) -> bool:
        return self.unknown_cost_count == self.count


class _Aggregate:
    def __init__(self, rollup: tuple[_RoleModelTotals, ...]) -> None:
        self.by_role: dict[str, object] = {}
        self.by_role_model: dict[tuple[str, str], _RoleModelTotals] = {
            (item.role, item.model_id): item for item in rollup
        }
        self.total_records = sum(item.count for item in rollup)
        self.total_tokens_in = 100
        self.total_tokens_out = 40
        self._rollup = rollup

    def cost_rollup(self) -> tuple[_RoleModelTotals, ...]:
        return self._rollup


def _install_trace_doubles(
    monkeypatch: pytest.MonkeyPatch,
    *,
    trace_file: _TraceFile,
    aggregate: _Aggregate,
    runs: tuple[tuple[Mapping[str, _Any], ...], ...] = (),
) -> list[str]:
    """Record which renderer each run went through; that split is the whole collapse rule."""
    rendered: list[str] = []
    monkeypatch.setattr(trace_module, "read_trace_records", lambda path: trace_file)
    monkeypatch.setattr(trace_module, "aggregate_trace", lambda records: aggregate)
    monkeypatch.setattr(trace_module, "collapse_runs", lambda records: runs)

    def fake_record(record: Mapping[str, _Any]) -> str:
        rendered.append(f"record:{record['id']}")
        return f"record:{record['id']}"

    def fake_group(records: Sequence[Mapping[str, _Any]]) -> str:
        joined = ",".join(str(item["id"]) for item in records)
        rendered.append(f"group:{joined}")
        return f"group:{joined}"

    monkeypatch.setattr(trace_module, "render_trace_record", fake_record)
    monkeypatch.setattr(trace_module, "render_collapsed_group", fake_group)
    return rendered


def test_trace_refuses_a_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["util", "trace", str(tmp_path / "nope.jsonl")])

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "Error:" in result.stderr


def test_trace_collapses_runs_of_two_or_more_and_renders_singletons_whole(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text("{}\n")
    records = ({"id": 1}, {"id": 2}, {"id": 3})
    rendered = _install_trace_doubles(
        monkeypatch,
        trace_file=_TraceFile(records),
        aggregate=_Aggregate(()),
        runs=((records[0], records[1]), (records[2],)),
    )

    result = runner.invoke(app, ["util", "trace", str(path)])

    assert result.exit_code == 0
    assert rendered == ["group:1,2", "record:3"]


def test_trace_expand_renders_every_record_and_never_collapses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text("{}\n")
    records = ({"id": 1}, {"id": 2})
    collapse_calls: list[object] = []
    rendered = _install_trace_doubles(monkeypatch, trace_file=_TraceFile(records), aggregate=_Aggregate(()))
    monkeypatch.setattr(trace_module, "collapse_runs", lambda records: collapse_calls.append(records) or ())

    result = runner.invoke(app, ["util", "trace", str(path), "--expand"])

    assert result.exit_code == 0
    assert rendered == ["record:1", "record:2"]
    assert collapse_calls == []


def test_trace_writes_warnings_to_stderr_without_changing_the_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Leniency is the point: an unversioned or future-versioned file still renders."""
    path = tmp_path / "t.jsonl"
    path.write_text("{}\n")
    warnings = (_Warning("unversioned"), _Warning("future-version", observed=9))
    _install_trace_doubles(
        monkeypatch, trace_file=_TraceFile(({"id": 1},), warnings), aggregate=_Aggregate(()), runs=(({"id": 1},),)
    )

    result = runner.invoke(app, ["util", "trace", str(path)])

    assert result.exit_code == 0
    assert "unversioned" in result.stderr
    assert "future-version" in result.stderr
    assert "unversioned" not in result.stdout


def test_trace_ends_with_the_summary_then_the_rollup_in_core_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """cost_rollup() owns the sort; re-sorting here would be a second, drifting rule."""
    path = tmp_path / "t.jsonl"
    path.write_text("{}\n")
    rollup = (
        _RoleModelTotals("planner", "opus", 2, 1.5, 0),
        _RoleModelTotals("worker", "sonnet", 3, 0.25, 1),
        _RoleModelTotals("scribe", "haiku", 4, 0.0, 4),
    )
    _install_trace_doubles(
        monkeypatch, trace_file=_TraceFile(({"id": 1},)), aggregate=_Aggregate(rollup), runs=(({"id": 1},),)
    )

    result = runner.invoke(app, ["util", "trace", str(path)])

    assert result.exit_code == 0
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert "Total records: 9" in lines
    assert "Total tokens in: 100" in lines
    assert "Total tokens out: 40" in lines
    rollup_lines = [line for line in lines if line.startswith(("planner", "worker", "scribe"))]
    assert [line.split()[0] for line in rollup_lines] == ["planner", "worker", "scribe"]
    assert "$1.5000" in rollup_lines[0]
    assert "$0.2500 (+1 unknown)" in rollup_lines[1]
    assert "$n/a (4 unknown)" in rollup_lines[2]
