"""Dense human renderers and the emit/exit policy for `gw work`.

Plain-data projections live in `graph_works_wire.work`; this module holds the
emit/exit policy and the human renderers.

Human output is bespoke here rather than routed through
`code_graph_io.render`: that module's spine is a graph entity, and a routing
decision, a rollup and a reconciliation context are none of those.

JSON occupies stdout alone; warnings and errors go to stderr in every mode. A
`--json` refusal additionally emits a single-key `{"error": ...}` envelope on
stdout before exiting non-zero (D-004); its shape lives in
`graph_works_wire.errors`. The exit code never becomes zero, and human-mode
output is unaffected.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Never, Protocol, cast

import typer
from graph_works_core.work.commands import NextResult
from graph_works_core.workspace.commits import NOTE_REASONS
from graph_works_wire.errors import REASONS, error_envelope

from graph_works_cli import exit_codes
from graph_works_cli.errors import exit_error
from graph_works_cli.json_output import encode

# ---------------------------------------------------------------------------
# Emit / exit policy
# ---------------------------------------------------------------------------

_JSON_MODE: ContextVar[bool | None] = ContextVar("gw_work_json_mode", default=None)

#: Captured alongside `_JSON_MODE`, at the same `--json` parse-time seam --
#: `typer.Context`/`typer.CallbackParam` wrap this codebase's own vendored
#: click fork (`typer._click`), which keeps a context stack the real `click`
#: package's `get_current_context()` cannot see (see this package's AGENTS.md
#: "typer.core vs click" gotcha); capturing the path here, once, at a point
#: where the context is unambiguously available avoids relying on any such
#: lookup later, inside `fail()`.
_COMMAND_NAME: ContextVar[str] = ContextVar("gw_work_command_name", default="")


def _set_json_mode(ctx: typer.Context, param: typer.CallbackParam, value: bool) -> bool:
    """The shared `--json` option callback: records this invocation's mode
    and command path.

    Click invokes an option's callback even when the option is absent,
    passing the declared default -- so every command built with
    `json_option()` sets this var, to `True` or to `False`. A command that
    hand-rolls its own `--json` leaves it `None`, which `fail()` treats as a
    programming error rather than a silent default.
    """
    _JSON_MODE.set(value)
    _root_name, _, rest = ctx.command_path.partition(" ")
    _COMMAND_NAME.set(rest)
    return value


def reset_json_mode() -> None:
    """Reset the per-invocation JSON-mode var. Called from the root callback,
    which Click runs before the subcommand's own option parsing -- so this
    always executes before that command's `json_option()` callback."""
    _JSON_MODE.set(None)
    _COMMAND_NAME.set("")


def json_option(help: str) -> bool:
    """The one `--json` declaration every `gw work` command must use.

    Replaces a hand-written `typer.Option(False, "--json", help=...)`: same
    surface (default `False`, same help text), plus the mode-tracking
    callback `fail()` depends on. Typed `bool` to match every call site's own
    `json_output: bool = ...` annotation -- `typer.Option()` itself is typed
    `Any` in typer's stubs.
    """
    return cast(bool, typer.Option(False, "--json", help=help, callback=_set_json_mode))


def _envelope(*, reason: str, message: str, code: int, payload: object) -> dict[str, Any]:
    """The single-key refusal document (D-004 §3.2), built by graph-works-wire."""
    assert reason in REASONS, f"fail(): {reason!r} is not in the closed reason vocabulary"
    return error_envelope(command=_COMMAND_NAME.get(), reason=reason, message=message, exit_code=code, payload=payload)


def emit(payload: object) -> None:
    """One JSON document on stdout, and nothing else."""
    typer.echo(encode(payload))


def fail(
    message: str,
    *,
    reason: str,
    code: int = exit_codes.GENERIC,
    cause: BaseException | None = None,
    payload: object | None = None,
) -> Never:
    """Emit a `--json` refusal envelope (when in JSON mode), write the
    human-facing error to stderr in every mode, and stop the current command.

    `reason` is required, not defaulted: a new exit site must name which of
    the closed vocabulary it is, so it can never silently fall through to a
    catch-all. The stderr line and exit code are unchanged from before D-004;
    the envelope is purely additive, and only appears on stdout.
    """
    mode = _JSON_MODE.get()
    assert mode is not None, "gw work command reached fail() without declaring --json via json_option()"
    if mode:
        emit(_envelope(reason=reason, message=message, code=code, payload=payload))
    exit_error(message, code=code, cause=cause)


def warn(message: str) -> None:
    """One diagnostic line on stderr, in both human and JSON mode."""
    typer.echo(f"[warn] {message}", err=True)


def split_csv(value: str) -> list[str]:
    """Split a comma-separated option value into a trimmed, non-empty list."""
    return [part.strip() for part in value.split(",") if part.strip()]


def echo_wrapped(prefix: str, text: str) -> None:
    """Echo `prefix + text`, hanging-indenting continuation lines under it.

    A multi-edge dependency blocker arrives as one string with its fragments on
    their own lines. Raw, every fragment sits at the string's own indent, well
    left of the first line, and reads as if it belonged to the next item.
    Human rendering only -- JSON is unaffected.
    """
    first, *rest = text.split("\n")
    typer.echo(f"{prefix}{first}")
    pad = " " * len(prefix)
    for line in rest:
        typer.echo(f"{pad}{line.strip()}")


# ---------------------------------------------------------------------------
# next
# ---------------------------------------------------------------------------


def _render_dispatch(profile: dict[str, Any], provenance: dict[str, Any]) -> None:
    typer.echo(
        f"  agent={profile['agent']} model={profile['model'] or 'default'} "
        f"effort={profile['reasoning_effort'] or 'default'}"
    )
    for field, origin in provenance.items():
        rule = origin["rule"]
        typer.echo(
            f"    {field}: {rule['source']} rule {rule['index']} ({rule['name'] or 'unnamed'}): {origin['reason']}"
        )


def render_commit(commit: dict[str, Any] | None) -> None:
    """Report a made commit or the actionable embedded-workspace note."""
    if commit is None:
        return
    if commit["status"] == "committed":
        sha = commit["sha"]
        short_sha = str(sha)[:12] if sha is not None else "unknown"
        typer.echo(f"[ok] committed {short_sha} {commit['subject']}")
    elif commit["status"] == "skipped" and commit["reason"] in NOTE_REASONS:
        advice = {
            "not-own-repo": "; set workflow.workspace_commits: on to commit an embedded workspace",
            "not-a-repo": "; initialize a Git repository to enable workspace commits",
            "git missing": "; install Git to enable workspace commits",
        }
        typer.echo(f"[note] workspace not committed ({commit['reason']}){advice[commit['reason']]}", err=True)


def render_next(result: NextResult, payload: dict[str, Any]) -> None:
    normalized = payload["normalized"]
    if normalized:
        for source in normalized:
            typer.echo(f"[fix] stamped {source['source_id']} for {source['path']}: {source['resource']}")
    for commit in payload["commits"]:
        render_commit(commit)
    typer.echo(
        f"{payload['selected_path']}: kind={payload['kind']} "
        f"work_status={payload['work_status']} phase={payload['phase']}"
    )
    if payload["descent"]:
        typer.echo(f"  descent: {' -> '.join(payload['descent']['path'])}")
    if payload["action"]:
        typer.echo(f"  dispatch: {payload['action']['skill']} — {payload['action']['reason']}")
    if payload["dispatch"]:
        _render_dispatch(payload["dispatch"]["profile"], payload["dispatch"]["provenance"])
    if payload["artifact"]:
        typer.echo(f"  artifact: {payload['artifact']['path']}")
    if payload["guidance"]:
        tokens = result.guidance.tokens if result.guidance is not None else 0
        where = payload["guidance_file"] or "not written"
        typer.echo(f"  guidance: {len(payload['guidance'])} entries, {tokens:,} tokens → {where}")
    else:
        typer.echo("  guidance: none")
    for warning in payload["guidance_warnings"]:
        warn(warning)
    carried = payload["carried_context"]
    with_content = [slot for slot in carried["slots"].values() if slot["lines"]]
    if with_content:
        typer.echo(f"  carried context: {len(with_content)} slot(s) with content")
        for slot in with_content:
            typer.echo(f"    {slot['title']}: {len(slot['lines'])} line(s)")
    else:
        typer.echo("  carried context: none")
    for slot in carried["slots"].values():
        for warning in slot["warnings"]:
            warn(warning)
    for warning in carried["warnings"]:
        warn(warning)
    for blocker in payload["blockers"]:
        echo_wrapped("  blocked: ", blocker)
    for warning in result.warnings:
        warn(warning)


# ---------------------------------------------------------------------------
# advance
# ---------------------------------------------------------------------------


def render_advance(payload: dict[str, Any]) -> None:
    typer.echo(f"[ok] {payload['path']}: phase={payload['phase']} work_status={payload['work_status']}")
    for key, change in payload["changes"].items():
        typer.echo(f"  {key}: {change[0]!r} -> {change[1]!r}")
    for key, value in payload["stamped"].items():
        typer.echo(f"  stamped {key}: {value}")
    if payload["results_path"]:
        typer.echo(f"  results: {payload['results_path']}")
    for blocker in payload["blockers"]:
        echo_wrapped("  blocked: ", blocker)
    if payload["repo_note"]:
        warn(payload["repo_note"])


# ---------------------------------------------------------------------------
# record-placement
# ---------------------------------------------------------------------------


def render_placement(payload: dict[str, Any]) -> None:
    if payload["written"]:
        verb = "recorded"
    elif payload["changed"]:
        verb = "would record"
    else:
        verb = "unchanged"
    after = payload["after"]
    typer.echo(
        f"[ok] {payload['path']}: {verb} worktree={after['worktree']} branch={after['branch']} "
        f"(phase={payload['expected_phase']}, root={payload['root']})"
    )
    before = payload["before"]
    if payload["changed"] and (before["worktree"] or before["branch"]):
        typer.echo(f"  was: worktree={before['worktree']} branch={before['branch']}")
    if payload["repo_note"]:
        warn(payload["repo_note"])


# ---------------------------------------------------------------------------
# record-reader
# ---------------------------------------------------------------------------


def render_reader_receipt(payload: dict[str, Any]) -> None:
    verb = "recorded" if payload["written"] else "replayed" if payload["replayed"] else "would record"
    observation = payload["observation"]
    typer.echo(
        f"[ok] {payload['path']}: {verb} reader worktree={observation['worktree']} "
        f"start_sha={observation['start_sha']} "
        f"(phase={payload['expected_phase']}, root={payload['root']})"
    )
    typer.echo(f"  receipt: {payload['receipt_path']}")


# ---------------------------------------------------------------------------
# file
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# status / lint / regen-index
# ---------------------------------------------------------------------------


def render_status(payload: dict[str, Any]) -> None:
    typer.echo(f"{payload['total']} item(s) under work/")
    for label, key in (
        ("work_status", "by_work_status"),
        ("type", "by_type"),
        ("phase", "by_phase"),
    ):
        rendered = ", ".join(f"{name} {count}" for name, count in payload[key].items())
        typer.echo(f"  by {label}: {rendered or '-'}")
    typer.echo(f"  not started: {payload['not_started']}")
    for path, rolled in payload["children"].items():
        typer.echo(f"  children {path}: {rolled['terminal']}/{rolled['total']} terminal")
    resume = payload["resume"]
    if resume is not None:
        typer.echo(f"  resume: {resume['primary']['path']} — {resume['primary']['title']}")
        for alternative in resume["alternatives"]:
            typer.echo(f"    alt: {alternative['path']} — {alternative['title']}")


def render_ingest_queue(payload: dict[str, Any]) -> None:
    pending = payload["pending"]
    typer.echo(f"{len(pending)} design spec(s) pending ingest")
    for entry in pending:
        typer.echo(f"  {entry['path']} — {entry['work_status']}")
        typer.echo(f"    {entry['resource']}")
    if pending:
        typer.echo("  Drain with: /gw:ingest <resource>")


class _LintFindingView(Protocol):
    code: str
    severity: str
    message: str


class _LintReportView(Protocol):
    findings: tuple[_LintFindingView, ...]


def render_lint(report: object) -> None:
    """Errors to stderr, warnings to stdout -- so a piped lint carries only
    what the reader asked for."""
    view = cast(_LintReportView, report)
    for finding in view.findings:
        line = f"{finding.severity:<6} {finding.code}: {finding.message}"
        typer.echo(line, err=finding.severity == "error")


# ---------------------------------------------------------------------------
# archive
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# decisions
# ---------------------------------------------------------------------------


def render_decision_write(payload: dict[str, Any], verb: str) -> None:
    resolved = f" (requested {payload['requested_path']})" if payload["requested_path"] != payload["owner_path"] else ""
    typer.echo(f"[ok] ledger: {payload['owner_path']}{resolved}")
    if payload["entry"]:
        typer.echo(f"[ok] {verb} {payload['entry']['id']}  status={payload['entry']['status']}")
    if payload["superseded"]:
        typer.echo(f"[ok] superseded {payload['superseded']}")
    follow_up = payload.get("follow_up")
    if follow_up:
        typer.echo(f"[ok] follow-up {follow_up['path']}: {follow_up['page_path']}")


def render_decision_list(payload: dict[str, Any]) -> None:
    resolved = f" (requested {payload['requested_path']})" if payload["requested_path"] != payload["owner_path"] else ""
    typer.echo(f"[ok] ledger: {payload['owner_path']}{resolved}  {payload['ledger_path']}")
    for entry in payload["entries"]:
        question = f" — {entry['question']}" if entry["question"] else ""
        shape = f"  [{entry['hold']} at {entry['phase'] or '-'}]" if entry.get("hold") else ""
        typer.echo(f"  {entry['id']}  {entry['status'] or '(no status)'}{shape}{question}")
    typer.echo("  counts: " + ", ".join(f"{key}={value}" for key, value in payload["counts"].items()))


def render_obligations(payload: dict[str, Any]) -> None:
    mode = "[ok]" if payload["applied"] else "[dry run]"
    typer.echo(f"{mode} finish_obligations: {payload['path']}")
    for entry in payload["obligations"]:
        typer.echo(f"  - [{entry['origin']}] {entry['text']} (recorded {entry['recorded']})")
    if not payload["applied"] and payload["changed"]:
        typer.echo("  (dry run; pass --apply to write)")


# ---------------------------------------------------------------------------
# orchestrate
# ---------------------------------------------------------------------------


def render_orchestrate(payload: dict[str, Any]) -> None:
    free = payload["slots_free"]
    max_p = payload["max_parallel"]
    header = (
        f"{payload['path']}: terminal={payload['terminal']} slots_free={free}/{max_p}"
        f" attend_slots_free={payload['attend_slots_free']}/{payload['max_attend']}"
    )
    if payload["supervise_merges"]:
        header += " supervise_merges=True"
    typer.echo(header)
    for dispatch in payload["dispatches"]:
        _render_dispatch(dispatch, dispatch["provenance"])
        typer.echo(
            f"  dispatch {dispatch['key']}: {dispatch['skill']} mode={dispatch['mode']} "
            f"model={dispatch['model']} worktree={dispatch['worktree']['action']}"
            f"{(' workspace=' + dispatch['workspace']['branch']) if dispatch['workspace'] else ''}"
        )
    for preparation in payload["workspace_preparations"]:
        typer.echo(
            f"  workspace-prepare {preparation['owner_path']} ({preparation['owner_phase']}): "
            f"{preparation['branch']} at {preparation['worktree']} from {preparation['base_branch']}"
        )
    for advance in payload["advances"]:
        typer.echo(f"  advance {advance['path']} (mode={advance['mode']}): {advance['reason']}")
    for blocked in payload["blocked"]:
        echo_wrapped(f"  blocked {blocked['path']} ({blocked['kind']}): ", blocked["reason"])
    for entry in payload["decisions"]["open"]:
        typer.echo(f"  open decision {entry['id']}: {entry['question']}")
    for hold in payload["holds"]:
        decision = hold["decision"]
        shape = decision["hold"] or "question"
        typer.echo(
            f"  hold {hold['path']} {decision['id']} ({shape} at {decision['phase'] or '-'}): {decision['question']}"
        )
    for warning in payload["warnings"]:
        warn(warning)


# ---------------------------------------------------------------------------
# reconcile-context
# ---------------------------------------------------------------------------


def render_reconcile(payload: dict[str, Any]) -> None:
    typer.echo(f"{payload['path']}: owner={payload['owner_path'] or '-'}")
    typer.echo(f"  spec: {payload['spec_path']}")
    typer.echo(f"  anchor: {payload['spec_anchor_commit'] or '-'} ({payload['anchor_source']})")
    typer.echo(f"  range: {payload['commit_range'] or '-'}")
    typer.echo(f"  touched: {', '.join(payload['touched_paths']) or '-'}")
    for sibling in payload["landed_siblings"]:
        typer.echo(f"  landed: {sibling['path']} resolved_in {sibling['resolved_in']}")
    for commit in payload["commits_since"]:
        typer.echo(f"  commit: {commit['sha'][:8]} {commit['subject']}")
    for cited in payload["cited_decisions"]:
        typer.echo(f"  cites: {cited['id']} status={cited['status']}")
    for conflict in payload["contradictions"]:
        typer.echo(f"  CONTRADICTION: {conflict['id']} is superseded — hold, do not advance")
    if payload["has_open_decision"]:
        typer.echo("  held: an open decision already names this item")
    if payload["diff_command"]:
        typer.echo(f"  diff: {payload['diff_command']}")
    for warning in payload["warnings"]:
        warn(warning)


def render_prepare_workspace(payload: dict[str, Any]) -> None:
    """Render the ordered workspace preparation plan or applied steps."""
    prefix = "" if payload["applied"] else "would "
    for step in payload["steps"]:
        typer.echo(
            f"[ok] {step['owner_path']}: {prefix}{step['action']} {step['branch']} "
            f"at {step['worktree']} (from {step['base_branch']})"
        )


def render_gate_run(payload: dict[str, Any]) -> None:
    """One line for a `gw work gate run` result."""
    for warning in payload["warnings"]:
        warn(warning)
    match = payload["match"]
    if payload["status"] == "satisfied" and match is not None:
        typer.echo(f"satisfied by {match['owner']} run {match['run_id']}")
    else:
        typer.echo(f"{payload['status']} {payload['run_id']}: {payload['command']} (log: {payload['log_path']})")


def render_gate_wait(payload: dict[str, Any]) -> None:
    """One line for a `gw work gate wait` result, plus the log tail when red."""
    if payload["status"] == "finished":
        recorded = "recorded" if payload["recorded"] else "not recorded"
        typer.echo(f"finished exit {payload['exit']} ({recorded}) log: {payload['log_path']}")
        if payload["exit"] != 0 and payload["log_tail"]:
            typer.echo(payload["log_tail"])
    elif payload["status"] == "running":
        typer.echo(f"running {payload['run_id']} log: {payload['log_path']}")
    else:
        typer.echo(f"{payload['status']} {payload['run_id']} log: {payload['log_path']}")


def render_gate_check(payload: dict[str, Any]) -> None:
    """One line for a `gw work gate check` result."""
    for warning in payload["warnings"]:
        warn(warning)
    match = payload["match"]
    if payload["status"] == "satisfied" and match is not None:
        typer.echo(f"satisfied by {match['owner']} run {match['run_id']}")
    else:
        typer.echo(f"unsatisfied: {payload['reason']} (tree {payload['tree']})")


def render_integrate(payload: dict[str, Any]) -> None:
    """Render the planned or applied code-repository integration and its receipt."""
    verb = {"planned": "would integrate", "integrated": "integrated", "already-integrated": "already integrated"}[
        payload["outcome"]
    ]
    typer.echo(
        f"[ok] {payload['path']}: {verb} {payload['source_branch']} into {payload['target_branch']} "
        f"in {payload['repo']} ({payload['strategy']}, from {payload['strategy_source']})"
    )
    if payload["result_commit"]:
        typer.echo(f"  result: {payload['result_commit']}")
    if payload["receipt_path"]:
        typer.echo(f"  receipt: {payload['receipt_path']}")


def render_accept_integration(payload: dict[str, Any]) -> None:
    """Render planned or recorded attested integration evidence."""
    verb = "accepted" if payload["applied"] else "would accept"
    typer.echo(
        f"[ok] {payload['path']}: {verb} {payload['evidence']} as {payload['source_branch']} integrated into "
        f"{payload['target_branch']} in {payload['repo']} (attested by {payload['accepted_by']})"
    )
    if payload["decision_id"]:
        typer.echo(f"  decision: {payload['decision_id']} in {payload['ledger_path']}")
    if payload["receipt_path"]:
        typer.echo(f"  receipt: {payload['receipt_path']}")


def render_merge_workspace(payload: dict[str, Any]) -> None:
    """Render the planned or applied integration and its receipt."""
    action = "merged" if payload["applied"] else "would merge"
    typer.echo(f"[ok] {payload['path']}: {action} {payload['source_branch']} into {payload['target_branch']}")
    if payload["merge_commit"]:
        typer.echo(f"  merge commit: {payload['merge_commit']}")
    if payload["receipt_path"]:
        typer.echo(f"  receipt: {payload['receipt_path']}")
