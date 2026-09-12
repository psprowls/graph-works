"""`gw wiki` proposal workflow boundary tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from graph_works_cli.cli import app
from graph_works_cli.wiki_cli import proposals as proposals_module
from graph_works_core.workspace.errors import WorkspaceConfigError
from okf_ext.proposals import Proposal
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def initialized_workspace(tmp_path: Path) -> Path:
    """Create the smallest real initialized workspace for proposal commands."""
    root = tmp_path / "works"
    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])
    assert result.exit_code == 0
    return root


def test_proposals_lists_only_open_proposals_as_the_frozen_json_shape(initialized_workspace: Path) -> None:
    """An unmounted or unfiltered list would make open proposal automation unreliable."""
    result = runner.invoke(app, ["wiki", "proposals", "--json", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


@pytest.mark.parametrize(("command", "decision"), (("approve", "approved"), ("reject", "rejected")))
@pytest.mark.parametrize("target_spelling", (r"\concepts\a.md", "./concepts/a.md", "dir/../concepts/a.md"))
def test_proposal_decisions_find_the_exact_normalized_target(
    monkeypatch: pytest.MonkeyPatch,
    initialized_workspace: Path,
    command: str,
    decision: str,
    target_spelling: str,
) -> None:
    """Using a proposal filename or slug would decide a different target's ledger."""
    bundle = object()
    proposal = Proposal(
        member="proposals/not-the-target-name.md",
        concept_id="proposals/not-the-target-name",
        target="concepts/a.md",
        title="A",
        description="",
        page_status="proposed",
        raw_page_status="proposed",
        sources=(),
        verified=(),
    )
    decided: list[tuple[object, object, object, object, object]] = []

    monkeypatch.setattr(proposals_module, "load_bundle", lambda _root: bundle)
    monkeypatch.setattr(proposals_module, "list_proposals", lambda _bundle: (proposal,))

    def fake_plan_decide(
        actual_bundle: object, actual_proposal: object, actual_decision: object, *, by: object, at: object
    ) -> SimpleNamespace:
        decided.append((actual_bundle, actual_proposal, actual_decision, by, at))
        return SimpleNamespace(ok=True, is_empty=True)

    monkeypatch.setattr(proposals_module, "plan_decide", fake_plan_decide, raising=False)
    monkeypatch.setattr(proposals_module, "apply", lambda *_args: SimpleNamespace(ok=True, written=()), raising=False)

    result = runner.invoke(
        app,
        ["wiki", "proposal", command, target_spelling, "--workspace", str(initialized_workspace)],
    )

    assert result.exit_code == 0
    assert decided[0][:4] == (bundle, proposal, decision, "human")
    assert isinstance(decided[0][4], datetime)
    assert decided[0][4].tzinfo is UTC


def test_proposal_decision_refuses_a_root_escaping_target_without_matching_an_empty_target(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A root escape must not turn into the malformed proposal's empty identity."""
    malformed = Proposal(
        member="proposals/malformed.md",
        concept_id="proposals/malformed",
        target="",
        title="Malformed",
        description="",
        page_status=None,
        raw_page_status="proposed",
        sources=(),
        verified=(),
        malformed="missing target",
    )
    monkeypatch.setattr(proposals_module, "load_bundle", lambda _root: SimpleNamespace(root=Path("/bundle")))
    monkeypatch.setattr(proposals_module, "list_proposals", lambda _bundle: (malformed,))
    monkeypatch.setattr(
        proposals_module,
        "plan_decide",
        lambda *_args, **_kwargs: pytest.fail("a root-escaping target must not be planned"),
    )

    result = runner.invoke(
        app,
        ["wiki", "proposal", "approve", "../../concepts/a.md", "--workspace", str(initialized_workspace)],
    )

    assert result.exit_code == 1
    assert "no proposal targets" in result.stderr


@pytest.mark.parametrize(("plan_ok", "apply_ok"), ((False, True), (True, False)))
def test_proposal_decision_refusal_or_incomplete_apply_exits_one(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path, plan_ok: bool, apply_ok: bool
) -> None:
    """A decision is durable only when its plan and write both succeed."""
    proposal = Proposal(
        member="proposals/a.md",
        concept_id="proposals/a",
        target="concepts/a.md",
        title="A",
        description="",
        page_status="proposed",
        raw_page_status="proposed",
        sources=(),
        verified=(),
    )
    applied: list[object] = []
    monkeypatch.setattr(proposals_module, "load_bundle", lambda _root: object())
    monkeypatch.setattr(proposals_module, "list_proposals", lambda _bundle: (proposal,))
    monkeypatch.setattr(
        proposals_module, "plan_decide", lambda *_args, **_kwargs: SimpleNamespace(ok=plan_ok, is_empty=False)
    )

    def fake_apply(*_args: object) -> SimpleNamespace:
        applied.append(_args)
        return SimpleNamespace(ok=apply_ok, written=())

    monkeypatch.setattr(proposals_module, "apply", fake_apply)

    result = runner.invoke(
        app, ["wiki", "proposal", "approve", "concepts/a.md", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 1
    assert len(applied) == (0 if not plan_ok else 1)


def test_proposal_file_builds_the_ordered_source_mapping_and_timestamps_it(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """Dropping optional provenance or reordering evidence would corrupt the proposal argument."""
    bundle = object()
    config = SimpleNamespace(declarations_dir=Path("/declarations"))
    schemas = object()
    lanes = object()
    filed: list[dict[str, object]] = []

    monkeypatch.setattr(proposals_module, "load_bundle", lambda _root: bundle)
    monkeypatch.setattr(proposals_module, "load_workspace_config", lambda _layout: config, raising=False)
    monkeypatch.setattr(proposals_module, "load_schemas", lambda _path: schemas, raising=False)
    monkeypatch.setattr(proposals_module, "lane_set", lambda actual_schemas: lanes, raising=False)

    def fake_plan_file(
        actual_bundle: object,
        actual_lanes: object,
        *,
        lane: str,
        title: str,
        description: str,
        source: dict[str, object],
        by: str,
        at: datetime,
    ) -> SimpleNamespace:
        filed.append(
            {
                "bundle": actual_bundle,
                "lanes": actual_lanes,
                "lane": lane,
                "title": title,
                "description": description,
                "source": source,
                "by": by,
                "at": at,
            }
        )
        return SimpleNamespace(ok=True, is_empty=True)

    monkeypatch.setattr(proposals_module, "plan_file", fake_plan_file, raising=False)

    result = runner.invoke(
        app,
        [
            "wiki",
            "proposal",
            "file",
            "--lane",
            "explanation",
            "--title",
            "Typed CLI",
            "--description",
            "Make proposal filing explicit.",
            "--id",
            "source-1",
            "--resource",
            "sources/one.md",
            "--rationale",
            "This removes ambiguity.",
            "--evidence",
            "first",
            "--evidence",
            "second",
            "--workspace",
            str(initialized_workspace),
        ],
    )

    assert result.exit_code == 0
    assert result.stdout == "nothing to do\n"
    assert filed[0] == {
        "bundle": bundle,
        "lanes": lanes,
        "lane": "explanation",
        "title": "Typed CLI",
        "description": "Make proposal filing explicit.",
        "source": {
            "id": "source-1",
            "resource": "sources/one.md",
            "rationale": "This removes ambiguity.",
            "evidence": ["first", "second"],
        },
        "by": "agent:graph-works-cli",
        "at": filed[0]["at"],
    }
    assert isinstance(filed[0]["at"], datetime)
    assert filed[0]["at"].tzinfo is UTC


def test_proposals_uses_the_open_filter_and_renders_target_status_and_malformed_state(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A list that leaks closed proposals or hides malformed state misleads reviewers."""
    proposal = Proposal(
        member="proposals/a.md",
        concept_id="proposals/a",
        target="concepts/a.md",
        title="A",
        description="",
        page_status="proposed",
        raw_page_status="proposed",
        sources=({"resource": "sources/a.md"},),
        verified=(),
        malformed="missing metadata",
    )
    called: list[object] = []
    monkeypatch.setattr(proposals_module, "load_bundle", lambda _root: object())

    def fake_list_proposals(actual_bundle: object, *, page_status: str | None = None) -> tuple[Proposal, ...]:
        called.append((actual_bundle, page_status))
        return (proposal,)

    monkeypatch.setattr(proposals_module, "list_proposals", fake_list_proposals)

    json_result = runner.invoke(app, ["wiki", "proposals", "--json", "--workspace", str(initialized_workspace)])
    human_result = runner.invoke(app, ["wiki", "proposals", "--workspace", str(initialized_workspace)])

    assert json_result.exit_code == 0
    assert json.loads(json_result.stdout) == [
        {
            "member": "proposals/a.md",
            "target": "concepts/a.md",
            "title": "A",
            "description": "",
            "page_status": "proposed",
            "sources": [{"resource": "sources/a.md"}],
            "verified": [],
            "malformed": "missing metadata",
        }
    ]
    assert human_result.exit_code == 0
    assert "concepts/a.md" in human_result.stdout
    assert "proposed" in human_result.stdout
    assert "missing metadata" in human_result.stdout
    assert [entry[1] for entry in called] == ["proposed", "proposed"]


def test_proposal_file_omits_unprovided_optional_source_fields(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """Blank optional flags must not become false provenance records in sources[]."""
    bundle = object()
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(proposals_module, "load_bundle", lambda _root: bundle)
    monkeypatch.setattr(
        proposals_module,
        "load_workspace_config",
        lambda _layout: SimpleNamespace(declarations_dir=Path("/declarations")),
    )
    monkeypatch.setattr(proposals_module, "load_schemas", lambda _path: object())
    monkeypatch.setattr(proposals_module, "lane_set", lambda _schemas: object())

    def fake_plan_file(*_args: object, source: dict[str, object], **_kwargs: object) -> SimpleNamespace:
        captured.append(source)
        return SimpleNamespace(ok=True, is_empty=True)

    monkeypatch.setattr(proposals_module, "plan_file", fake_plan_file)

    result = runner.invoke(
        app,
        [
            "wiki",
            "proposal",
            "file",
            "--lane",
            "explanation",
            "--title",
            "T",
            "--id",
            "source-1",
            "--resource",
            "sources/one.md",
            "--workspace",
            str(initialized_workspace),
        ],
    )

    assert result.exit_code == 0
    assert captured == [{"id": "source-1", "resource": "sources/one.md"}]


@pytest.mark.parametrize(("plan_ok", "apply_ok"), ((False, True), (True, False)))
def test_proposal_file_refusal_or_incomplete_apply_exits_one(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path, plan_ok: bool, apply_ok: bool
) -> None:
    """A refused or partial filing must never report success to an automation caller."""
    applied: list[object] = []
    monkeypatch.setattr(proposals_module, "load_bundle", lambda _root: object())
    monkeypatch.setattr(
        proposals_module,
        "load_workspace_config",
        lambda _layout: SimpleNamespace(declarations_dir=Path("/declarations")),
    )
    monkeypatch.setattr(proposals_module, "load_schemas", lambda _path: object())
    monkeypatch.setattr(proposals_module, "lane_set", lambda _schemas: object())
    monkeypatch.setattr(
        proposals_module, "plan_file", lambda *_args, **_kwargs: SimpleNamespace(ok=plan_ok, is_empty=False)
    )

    def fake_apply(*_args: object) -> SimpleNamespace:
        applied.append(_args)
        return SimpleNamespace(ok=apply_ok, written=())

    monkeypatch.setattr(proposals_module, "apply", fake_apply)

    result = runner.invoke(
        app,
        [
            "wiki",
            "proposal",
            "file",
            "--lane",
            "explanation",
            "--title",
            "T",
            "--id",
            "source-1",
            "--resource",
            "sources/one.md",
            "--workspace",
            str(initialized_workspace),
        ],
    )

    assert result.exit_code == 1
    assert len(applied) == (0 if not plan_ok else 1)


@pytest.mark.parametrize(
    ("missing", "value"), (("--lane", "explanation"), ("--title", "T"), ("--id", "s1"), ("--resource", "r"))
)
def test_proposal_file_requires_each_filing_identity_field(missing: str, value: str) -> None:
    """A missing lane, title, id, or resource leaves the planner without a safe identity."""
    options = {
        "--lane": "explanation",
        "--title": "T",
        "--id": "s1",
        "--resource": "r",
    }
    options.pop(missing)
    args = ["wiki", "proposal", "file"]
    for flag, option_value in options.items():
        args.extend((flag, option_value))

    result = runner.invoke(app, args)

    assert result.exit_code == 2
    assert missing in result.stderr


@pytest.mark.parametrize("flag", ("--kind", "--target-slug", "--origin", "--dry-run", "--json"))
def test_proposal_file_rejects_removed_flags(flag: str) -> None:
    """Retired proposal flags must not be accepted as inert compatibility surface."""
    result = runner.invoke(
        app,
        [
            "wiki",
            "proposal",
            "file",
            "--lane",
            "explanation",
            "--title",
            "T",
            "--id",
            "s1",
            "--resource",
            "r",
            flag,
            "x",
        ],
    )

    assert result.exit_code == 2
    assert f"No such option: {flag}" in result.stderr


def _proposal(target: str, member: str = "proposals/a.md") -> Proposal:
    """Build the smallest well-formed open proposal for a target."""
    return Proposal(
        member=member,
        concept_id=member.removesuffix(".md"),
        target=target,
        title="A",
        description="",
        page_status="proposed",
        raw_page_status="proposed",
        sources=(),
        verified=(),
    )


def _file_args(workspace: Path) -> list[str]:
    """The minimal complete `wiki proposal file` invocation."""
    return [
        "wiki",
        "proposal",
        "file",
        "--lane",
        "explanation",
        "--title",
        "Typed CLI",
        "--id",
        "source-1",
        "--resource",
        "sources/one.md",
        "--workspace",
        str(workspace),
    ]


def test_proposal_decision_refuses_a_blank_target_before_reading_any_proposal(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A blank target normalizes to the malformed empty identity; matching it would decide the wrong page."""
    monkeypatch.setattr(proposals_module, "load_bundle", lambda _root: SimpleNamespace(root=Path("/bundle")))
    monkeypatch.setattr(
        proposals_module, "list_proposals", lambda *_args, **_kwargs: pytest.fail("a blank target must not be scanned")
    )

    result = runner.invoke(app, ["wiki", "proposal", "approve", "   ", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 1
    assert "no proposal targets" in result.stderr


def test_proposal_decision_scans_past_non_matching_proposals_before_refusing(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """Stopping at the first proposal would refuse targets that are present later in the bundle."""
    monkeypatch.setattr(proposals_module, "load_bundle", lambda _root: SimpleNamespace(root=Path("/bundle")))
    monkeypatch.setattr(
        proposals_module,
        "list_proposals",
        lambda _bundle: (_proposal("concepts/b.md", "proposals/b.md"), _proposal("concepts/c.md", "proposals/c.md")),
    )
    monkeypatch.setattr(
        proposals_module, "plan_decide", lambda *_args, **_kwargs: pytest.fail("an absent target must not be planned")
    )

    result = runner.invoke(
        app, ["wiki", "proposal", "approve", "concepts/a.md", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 1
    assert "no proposal targets 'concepts/a.md'" in result.stderr


def test_proposals_reports_an_unreadable_bundle_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """Listing is the first command an operator runs; it must fail legibly."""

    def fail(*_args: object, **_kwargs: object) -> object:
        raise OSError("proposals/ is unreadable")

    monkeypatch.setattr(proposals_module, "load_bundle", fail)

    result = runner.invoke(app, ["wiki", "proposals", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error: proposals/ is unreadable" in result.stderr


def test_proposals_says_so_when_no_proposal_is_open(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """Empty stdout reads as a broken command rather than an empty queue."""
    monkeypatch.setattr(proposals_module, "load_bundle", lambda _root: object())
    monkeypatch.setattr(proposals_module, "list_proposals", lambda *_args, **_kwargs: ())

    result = runner.invoke(app, ["wiki", "proposals", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert result.stdout == "no open proposals\n"


def test_proposal_decision_reports_an_unreadable_bundle_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A decision that cannot read the bundle must not look like a missing proposal."""

    def fail(*_args: object, **_kwargs: object) -> object:
        raise ValueError("bundle root is not a directory")

    monkeypatch.setattr(proposals_module, "load_bundle", fail)

    result = runner.invoke(
        app, ["wiki", "proposal", "reject", "concepts/a.md", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 1
    assert "Error: bundle root is not a directory" in result.stderr


def test_proposal_decision_reports_a_refused_plan_construction(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """`plan_decide` refuses an already-decided proposal by raising; that reason must reach the operator."""
    proposal = _proposal("concepts/a.md")

    def fail(*_args: object, **_kwargs: object) -> object:
        raise ValueError("proposal is already approved")

    monkeypatch.setattr(proposals_module, "load_bundle", lambda _root: object())
    monkeypatch.setattr(proposals_module, "list_proposals", lambda _bundle: (proposal,))
    monkeypatch.setattr(proposals_module, "plan_decide", fail)

    result = runner.invoke(
        app, ["wiki", "proposal", "approve", "concepts/a.md", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 1
    assert "Error: proposal is already approved" in result.stderr


def test_proposal_decision_reports_a_failed_write_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A half-written ledger must be reported, not raised."""
    proposal = _proposal("concepts/a.md")

    def fail(*_args: object, **_kwargs: object) -> object:
        raise OSError("proposals/a.md is read-only")

    monkeypatch.setattr(proposals_module, "load_bundle", lambda _root: object())
    monkeypatch.setattr(proposals_module, "list_proposals", lambda _bundle: (proposal,))
    monkeypatch.setattr(
        proposals_module, "plan_decide", lambda *_args, **_kwargs: SimpleNamespace(ok=True, is_empty=False)
    )
    monkeypatch.setattr(proposals_module, "apply", fail)

    result = runner.invoke(
        app, ["wiki", "proposal", "approve", "concepts/a.md", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 1
    assert "Error: proposals/a.md is read-only" in result.stderr


def test_proposal_decision_echoes_every_written_member(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A decision that writes silently leaves no record of which pages moved."""
    proposal = _proposal("concepts/a.md")
    monkeypatch.setattr(proposals_module, "load_bundle", lambda _root: object())
    monkeypatch.setattr(proposals_module, "list_proposals", lambda _bundle: (proposal,))
    monkeypatch.setattr(
        proposals_module, "plan_decide", lambda *_args, **_kwargs: SimpleNamespace(ok=True, is_empty=False)
    )
    monkeypatch.setattr(
        proposals_module,
        "apply",
        lambda *_args: SimpleNamespace(ok=True, written=("proposals/a.md", "proposals/index.md")),
    )

    result = runner.invoke(
        app, ["wiki", "proposal", "approve", "concepts/a.md", "--workspace", str(initialized_workspace)]
    )

    assert result.exit_code == 0
    assert result.stdout == "proposals/a.md\nproposals/index.md\n"


def test_proposal_file_reports_an_unloadable_lane_schema(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """Filing against an unreadable schema must name the schema, not raise."""

    def fail(*_args: object, **_kwargs: object) -> object:
        raise WorkspaceConfigError("declarations dir is missing")

    monkeypatch.setattr(proposals_module, "load_bundle", lambda _root: object())
    monkeypatch.setattr(proposals_module, "load_workspace_config", fail, raising=False)

    result = runner.invoke(app, _file_args(initialized_workspace))

    assert result.exit_code == 1
    assert "Error: declarations dir is missing" in result.stderr


def test_proposal_file_reports_an_unknown_lane(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    """An unknown lane is user error; it must read as a message, not a KeyError traceback."""

    def fail(*_args: object, **_kwargs: object) -> object:
        raise KeyError("explanation")

    monkeypatch.setattr(proposals_module, "load_bundle", lambda _root: object())
    monkeypatch.setattr(
        proposals_module,
        "load_workspace_config",
        lambda _layout: SimpleNamespace(declarations_dir=Path("/d")),
        raising=False,
    )
    monkeypatch.setattr(proposals_module, "load_schemas", lambda _path: object(), raising=False)
    monkeypatch.setattr(proposals_module, "lane_set", lambda _schemas: object(), raising=False)
    monkeypatch.setattr(proposals_module, "plan_file", fail, raising=False)

    result = runner.invoke(app, _file_args(initialized_workspace))

    assert result.exit_code == 1
    assert "explanation" in result.stderr


def test_proposal_file_reports_a_failed_write_and_echoes_written_members(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """Filing must report a failed write and, on success, name every page it touched."""
    monkeypatch.setattr(proposals_module, "load_bundle", lambda _root: object())
    monkeypatch.setattr(
        proposals_module,
        "load_workspace_config",
        lambda _layout: SimpleNamespace(declarations_dir=Path("/d")),
        raising=False,
    )
    monkeypatch.setattr(proposals_module, "load_schemas", lambda _path: object(), raising=False)
    monkeypatch.setattr(proposals_module, "lane_set", lambda _schemas: object(), raising=False)
    monkeypatch.setattr(
        proposals_module, "plan_file", lambda *_args, **_kwargs: SimpleNamespace(ok=True, is_empty=False), raising=False
    )

    def fail(*_args: object, **_kwargs: object) -> object:
        raise OSError("proposals/ is read-only")

    monkeypatch.setattr(proposals_module, "apply", fail)
    failed = runner.invoke(app, _file_args(initialized_workspace))

    assert failed.exit_code == 1
    assert "Error: proposals/ is read-only" in failed.stderr

    monkeypatch.setattr(
        proposals_module, "apply", lambda *_args: SimpleNamespace(ok=True, written=("proposals/typed-cli.md",))
    )
    written = runner.invoke(app, _file_args(initialized_workspace))

    assert written.exit_code == 0
    assert written.stdout == "proposals/typed-cli.md\n"
