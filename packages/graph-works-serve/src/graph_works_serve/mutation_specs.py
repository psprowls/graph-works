"""Workspace mutations as `MutationSpec`s: parameters, core call,
projection, and what counts as refused. Each parameter means what its `gw`
flag means; see `mutations.py` for the plan/apply contract itself."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from types import MappingProxyType
from typing import Any

from graph_works_core.archive.commands import ArchiveRun, run_archive
from graph_works_core.orchestrate.stage_advance import StageAdvance, run_stage_advance
from graph_works_core.proposals import Decision, ProposalDecideRun, run_proposal_decide
from graph_works_core.proposals.commands import human_actor
from graph_works_core.scan import RepoScanRun, run_repo_scan
from graph_works_core.wiki_page import SectionWriteRun, run_section_write
from graph_works_core.work.commands import DecisionCommandResult, run_decision_answer
from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_wire.wiki import proposal_decide_payload, repo_scan_payload, section_write_payload
from graph_works_wire.work import advance_payload, archive_payload, decision_payload

from graph_works_serve.mutations import BeforeApply, MutationSpec, Params
from graph_works_serve.params import Param, ParamError, ParamType


def _body(name: str, type_: ParamType, *, required: bool = False, default: object = None) -> Param:
    return Param(name, type_, required=required, default=default, location="body")


def _opt(params: Params, key: str) -> str | None:
    value = params[key]
    return str(value) if value else None


def _run_advance(
    layout: WorkspaceLayout, params: Params, as_of: datetime, dry_run: bool, before_apply: BeforeApply | None = None
) -> StageAdvance:
    released = _opt(params, "released_at")
    return run_stage_advance(
        layout,
        str(params["path"]),
        today=as_of.date(),
        effort=_opt(params, "effort"),
        owner=_opt(params, "owner"),
        resolved_in=_opt(params, "resolved_in"),
        released_at=None if released is None else date.fromisoformat(released),
        worktree=_opt(params, "worktree"),
        branch=_opt(params, "branch"),
        # Serve's cwd says nothing about the item: never infer placement.
        infer_worktree=False,
        start_sha=_opt(params, "start_sha"),
        return_=bool(params["return"]),
        dry_run=dry_run,
        before_apply=before_apply,
    )


def _project_advance(result: object, params: Params, _dry_run: bool) -> dict[str, Any]:
    assert isinstance(result, StageAdvance)
    return advance_payload(result, str(params["path"]))


def _refusal_field(plan: Mapping[str, Any], _params: Params) -> str | None:
    """Refused when the projection's single `refusal` field is set."""
    return "refused" if plan["refusal"] is not None else None


ADVANCE = MutationSpec(
    route="/v1/work/advance",
    command="work advance",
    summary="Advance a work item to its next pipeline stage",
    params=(
        _body("path", "str", required=True),
        _body("effort", "str"),
        _body("owner", "str"),
        _body("resolved_in", "str"),
        _body("released_at", "date"),
        _body("worktree", "str"),
        _body("branch", "str"),
        _body("start_sha", "str"),
        _body("return", "bool", default=False),
    ),
    run=_run_advance,
    project=_project_advance,
    refused=_refusal_field,
)


def _paths(params: Params) -> list[str] | None:
    value = params["paths"]
    return None if value is None else [str(path) for path in value]


def _validate_archive(params: Params) -> None:
    if params["paths"] == []:
        raise ParamError("`paths` must be non-empty; omit it (or send null) to sweep every eligible item")


def _run_archive(
    layout: WorkspaceLayout, params: Params, as_of: datetime, dry_run: bool, before_apply: BeforeApply | None = None
) -> ArchiveRun:
    # `()` keeps the wiki lane out entirely: phase 0 exposes `gw work archive`, not `gw archive`.
    return run_archive(layout, _paths(params), (), today=as_of.date(), dry_run=dry_run, before_apply=before_apply)


def _project_archive(result: object, _params: Params, dry_run: bool) -> dict[str, Any]:
    assert isinstance(result, ArchiveRun)
    return archive_payload(result, dry_run=dry_run)


def _archive_refused(plan: Mapping[str, Any], params: Params) -> str | None:
    """Archive refusal checks, before the mutation engine allows writes."""
    if plan["conflict"]:
        return "conflict"
    if plan["refusals"] or not plan["ok"]:
        return "refused"
    targeted = _paths(params) or []
    if any(path not in plan["path_mapping"] for path in targeted):
        return "incomplete"
    return None


ARCHIVE = MutationSpec(
    route="/v1/work/archive",
    command="work archive",
    summary="Archive terminal work items (named, or a sweep)",
    params=(_body("paths", "list[str]"),),
    run=_run_archive,
    project=_project_archive,
    refused=_archive_refused,
    validate=_validate_archive,
    # The CLI maps a ValueError from archive to `io`, not `unresolved` (work_cli/main.py:574).
    errors=((WorkspaceError, "workspace"), ((OSError, ValueError), "io")),
)


_DISPOSITIONS: Mapping[str, Decision] = MappingProxyType(
    {"approve": "approved", "reject": "rejected", "supersede": "superseded"}
)


def _validate_decide(params: Params) -> None:
    decision = params["decision"]
    if decision not in _DISPOSITIONS:
        raise ParamError("`decision` must be `approve`, `reject` or `supersede`")
    if decision == "supersede" and not params["superseded_by"]:
        raise ParamError("`supersede` needs `superseded_by`")
    if decision != "supersede" and params["superseded_by"] is not None:
        raise ParamError("`superseded_by` is only allowed with `supersede`")


def _run_decide(
    layout: WorkspaceLayout, params: Params, as_of: datetime, dry_run: bool, before_apply: BeforeApply | None = None
) -> ProposalDecideRun:
    # `by` is left to core: the same `human:<handle>` from git the CLI records. Stamp the reviewed
    # instant, including during apply.
    return run_proposal_decide(
        layout,
        str(params["target"]),
        _DISPOSITIONS[str(params["decision"])],
        at=as_of,
        superseded_by=_opt(params, "superseded_by"),
        note=_opt(params, "note"),
        dry_run=dry_run,
        before_apply=before_apply,
    )


def _project_decide(result: object, _params: Params, _dry_run: bool) -> dict[str, Any]:
    assert isinstance(result, ProposalDecideRun)
    return proposal_decide_payload(result)


def _decide_refused(plan: Mapping[str, Any], _params: Params) -> str | None:
    return "refused" if plan["refusals"] else None


DECIDE = MutationSpec(
    route="/v1/wiki/proposal/decide",
    command="wiki proposal decide",
    summary="Approve, reject or supersede a wiki proposal",
    params=(
        _body("target", "str", required=True),
        _body("decision", "str", required=True),
        _body("superseded_by", "str"),
        _body("note", "str"),
    ),
    run=_run_decide,
    project=_project_decide,
    refused=_decide_refused,
    validate=_validate_decide,
)


def _run_decision_answer(
    layout: WorkspaceLayout, params: Params, as_of: datetime, dry_run: bool, before_apply: BeforeApply | None = None
) -> DecisionCommandResult:
    return run_decision_answer(
        layout,
        str(params["path"]),
        str(params["id"]),
        answer=str(params["answer"]),
        rationale=_opt(params, "rationale"),
        on=as_of.date(),
        decided_by=_opt(params, "decided_by") or human_actor(layout.root),
        dry_run=dry_run,
        before_apply=before_apply,
    )


def _project_decision_answer(result: object, _params: Params, _dry_run: bool) -> dict[str, Any]:
    assert isinstance(result, DecisionCommandResult)
    return decision_payload(result)


DECISION_ANSWER = MutationSpec(
    route="/v1/work/decision/answer",
    command="work decision answer",
    summary="Answer an open decision in a work item's ledger",
    params=(
        _body("path", "str", required=True),
        _body("id", "str", required=True),
        _body("answer", "str", required=True),
        _body("rationale", "str"),
        _body("decided_by", "str"),
    ),
    run=_run_decision_answer,
    project=_project_decision_answer,
    refused=_refusal_field,
)


def _run_section(
    layout: WorkspaceLayout, params: Params, as_of: datetime, dry_run: bool, before_apply: BeforeApply | None = None
) -> SectionWriteRun:
    return run_section_write(
        layout,
        str(params["id"]),
        str(params["heading"]),
        str(params["body"]),
        today=as_of.date(),
        dry_run=dry_run,
        before_apply=before_apply,
    )


def _project_section(result: object, _params: Params, _dry_run: bool) -> dict[str, Any]:
    assert isinstance(result, SectionWriteRun)
    return section_write_payload(result)


SECTION_WRITE = MutationSpec(
    route="/v1/wiki/section",
    command="wiki section write",
    summary="Replace one prose-owned ## section of a wiki page",
    params=(
        _body("id", "str", required=True),
        _body("heading", "str", required=True),
        _body("body", "str", required=True),
    ),
    run=_run_section,
    project=_project_section,
    refused=_refusal_field,
)


def _run_scan(
    layout: WorkspaceLayout, params: Params, as_of: datetime, dry_run: bool, before_apply: BeforeApply | None = None
) -> RepoScanRun:
    # Structural only (D-001): serve never narrates, so no model is ever built here.
    return run_repo_scan(
        layout,
        load_workspace_config(layout),
        repo=str(params["repo"]),
        at=as_of,
        today=as_of.date(),
        dry_run=dry_run,
        before_apply=before_apply,
    )


def _project_scan(result: object, _params: Params, _dry_run: bool) -> dict[str, Any]:
    assert isinstance(result, RepoScanRun)
    return repo_scan_payload(result)


def _scan_refused(plan: Mapping[str, Any], _params: Params) -> str | None:
    """A refusal, or a plan whose structural pass already reports errors, is not applied."""
    if plan["refusal"] is not None or plan["errors"]:
        return "refused"
    return None


SCAN = MutationSpec(
    route="/v1/scan",
    command="scan",
    summary="Structural scan of one repository (no narration)",
    params=(_body("repo", "str", required=True),),
    run=_run_scan,
    project=_project_scan,
    refused=_scan_refused,
)
