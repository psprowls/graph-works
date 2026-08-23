"""`gw archive` — the combined work + wiki sweep (D-031)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from graph_works_cli.cli import app
from graph_works_cli.util_cli import archive as archive_module
from okf_ext.moves import Stranded
from typer.testing import CliRunner

runner = CliRunner()


class _Moves:
    def __init__(self, *, stranded: tuple[Stranded, ...] = ()) -> None:
        self.stranded = stranded


class _Plan:
    def __init__(self, *, ok: bool = True, diff: str = "", stranded: tuple[Stranded, ...] = ()) -> None:
        self.ok = ok
        self._diff = diff
        self.moves = _Moves(stranded=stranded)
        self.move_plan = self.moves
        self.path_mapping: dict[str, str] = {}
        self.refusals = (
            () if ok else (SimpleNamespace(path="work/refused", kind="refused", detail="archive plan was refused"),)
        )

    def diff(self) -> str:
        return self._diff


class _Run:
    def __init__(
        self,
        *,
        plan: _Plan,
        wiki_plan: _Plan,
        conflict: tuple[str, ...] = (),
        archived: tuple[str, ...] = (),
        wiki_archived: tuple[str, ...] = (),
        applied: bool = False,
        wiki_ok: bool = True,
        wiki_refusals: tuple[object, ...] = (),
        wiki_failures: tuple[object, ...] = (),
    ) -> None:
        self.plan = plan
        self.wiki_plan = wiki_plan
        self.conflict = conflict
        plan.path_mapping.update({path: path for path in archived})
        self.result = SimpleNamespace(ok=True, failures=()) if applied else None
        self.wiki = (
            SimpleNamespace(
                archived=wiki_archived,
                ok=wiki_ok,
                refusals=wiki_refusals,
                move=SimpleNamespace(failed=wiki_failures),
            )
            if applied
            else None
        )

    @property
    def ok(self) -> bool:
        return self.plan.ok and self.wiki_plan.ok and not self.conflict


@pytest.fixture
def initialized_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "works"
    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])
    assert result.exit_code == 0
    return root


class _CallsBox(list[dict[str, Any]]):
    """List that also holds a box for controlling the mocked run_archive return value."""

    box: dict[str, _Run]


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> _CallsBox:
    """Record every run_archive call; the default return is an empty successful sweep."""
    recorded = _CallsBox()
    box: dict[str, _Run] = {"run": _Run(plan=_Plan(), wiki_plan=_Plan(), applied=True)}
    recorded.box = box

    def fake_run_archive(layout: object, slugs: object, wiki_slugs: object, *, today: date, dry_run: bool) -> _Run:
        recorded.append({"slugs": slugs, "wiki_slugs": wiki_slugs, "today": today, "dry_run": dry_run})
        return box["run"]

    monkeypatch.setattr(archive_module, "run_archive", fake_run_archive)
    return recorded


def test_both_lanes_sweep_everything_and_dry_run_is_opt_in(calls: _CallsBox, initialized_workspace: Path) -> None:
    """`None` on both lanes is what makes this a sweep; a stray `()` would archive no page."""
    result = runner.invoke(app, ["archive", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["slugs"] is None
    assert calls[0]["wiki_slugs"] is None
    assert calls[0]["dry_run"] is False
    assert isinstance(calls[0]["today"], date)


def test_dry_run_renders_both_plans_and_writes_nothing(calls: _CallsBox, initialized_workspace: Path) -> None:
    work_plan = _Plan()
    work_plan.path_mapping = {"work/a": "work/_archive/a"}
    calls.box["run"] = _Run(plan=work_plan, wiki_plan=_Plan(diff="wiki: b -> archive/b"))

    result = runner.invoke(app, ["archive", "--dry-run", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert calls[0]["dry_run"] is True
    assert "work/a -> work/_archive/a" in result.stdout
    assert "wiki: b -> archive/b" in result.stdout


def test_a_refused_plan_renders_both_diffs_and_exits_non_zero(calls: _CallsBox, initialized_workspace: Path) -> None:
    calls.box["run"] = _Run(plan=_Plan(ok=False, diff="! refused"), wiki_plan=_Plan(diff="wiki plan"))

    result = runner.invoke(app, ["archive", "--workspace", str(initialized_workspace)])

    assert result.exit_code != 0
    assert "! work/refused: refused" in result.stdout
    assert "wiki plan" in result.stdout
    assert "archive plan was refused" in result.stderr


def test_a_cross_lane_conflict_names_the_members(calls: _CallsBox, initialized_workspace: Path) -> None:
    """The conflict guard is the whole reason this verb exists rather than two sweeps."""
    calls.box["run"] = _Run(plan=_Plan(), wiki_plan=_Plan(), conflict=("work/a.md", "concepts/b.md"))

    result = runner.invoke(app, ["archive", "--workspace", str(initialized_workspace)])

    assert result.exit_code != 0
    assert "work/a.md" in result.stderr
    assert "concepts/b.md" in result.stderr


def test_a_real_run_prints_work_tokens_then_wiki_tokens(calls: _CallsBox, initialized_workspace: Path) -> None:
    calls.box["run"] = _Run(
        plan=_Plan(), wiki_plan=_Plan(), archived=("2026-01-01-a",), wiki_archived=("concepts/b",), applied=True
    )

    result = runner.invoke(app, ["archive", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["2026-01-01-a", "concepts/b"]


def test_nothing_to_do_is_a_success(calls: _CallsBox, initialized_workspace: Path) -> None:
    result = runner.invoke(app, ["archive", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert result.stdout.strip() == "nothing to do"


@pytest.mark.parametrize(
    ("wiki_refusals", "wiki_failures", "diagnostic"),
    [
        (
            (SimpleNamespace(path="concepts/a.md", kind="stale", detail="page changed after planning"),),
            (),
            "concepts/a.md: stale -- page changed after planning",
        ),
        (
            (),
            (SimpleNamespace(path="concepts/a.md", kind="commit-error", error="disk full"),),
            "concepts/a.md: commit-error -- disk full",
        ),
    ],
)
def test_a_failed_wiki_apply_exits_nonzero_with_diagnostic(
    calls: _CallsBox,
    initialized_workspace: Path,
    wiki_refusals: tuple[object, ...],
    wiki_failures: tuple[object, ...],
    diagnostic: str,
) -> None:
    calls.box["run"] = _Run(
        plan=_Plan(),
        wiki_plan=_Plan(),
        applied=True,
        wiki_ok=False,
        wiki_refusals=wiki_refusals,
        wiki_failures=wiki_failures,
    )

    result = runner.invoke(app, ["archive", "--workspace", str(initialized_workspace)])

    assert result.exit_code != 0
    assert "wiki archive apply was incomplete" in result.stderr
    assert diagnostic in result.stderr
    assert "nothing to do" not in result.stdout


def test_reports_both_lanes_stranded_counts_separately_and_labelled(
    calls: _CallsBox, initialized_workspace: Path
) -> None:
    """A caller acting on the number needs to know which lane stranded what
    (2026-08-21 spec §4.5) -- the two counts are never merged into one."""
    calls.box["run"] = _Run(
        plan=_Plan(stranded=(Stranded(member="work/citing.md", target="work/a.md", line=3),)),
        wiki_plan=_Plan(stranded=(Stranded(member="tutorials/citing.md", target="tutorials/b.md", line=5),)),
        archived=("2026-01-01-a",),
        wiki_archived=("concepts/b",),
        applied=True,
    )

    result = runner.invoke(app, ["archive", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert "work items: ! 1 inbound [[wikilink]]" in result.stderr
    assert "wiki pages: ! 1 inbound [[wikilink]]" in result.stderr


def test_no_stranded_note_when_neither_lane_stranded_anything(calls: _CallsBox, initialized_workspace: Path) -> None:
    result = runner.invoke(app, ["archive", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert "inbound [[wikilink]]" not in result.stderr


def test_stranded_note_still_prints_on_a_conflict(calls: _CallsBox, initialized_workspace: Path) -> None:
    """Step 4: the note is emitted before the conflict/refusal echo block, so a
    conflict or refusal still reports what was stranded."""
    calls.box["run"] = _Run(
        plan=_Plan(stranded=(Stranded(member="work/citing.md", target="work/a.md", line=3),)),
        wiki_plan=_Plan(),
        conflict=("work/a.md",),
    )

    result = runner.invoke(app, ["archive", "--workspace", str(initialized_workspace)])

    assert result.exit_code != 0
    assert "work items: ! 1 inbound [[wikilink]]" in result.stderr


def test_the_verb_is_registered_at_the_root() -> None:
    """`gw archive` and `gw wiki archive` are different commands; this pins the former."""
    result = runner.invoke(app, ["archive", "--help"])

    assert result.exit_code == 0
