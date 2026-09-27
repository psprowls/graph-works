"""`gw work ask` and `gw work ask-answer` -- the typed worker ask.

`ask` is the worker's half: it writes the `gw.ask/1` payload and prints the
two strings to pass, unchanged, to the worker's own `orca orchestration ask`.
gw never calls Orca, never holds a worker's capability, and never owns an
ask's timeout or resume. `ask-answer` is the coordinator's half: it checks a
human's answer against the payload, records it, and prints the reply body.

Both write by default and take `--dry-run`, like `gw work file`. This module
reads the clock; core does not. `--question-file` is read with `newline=""`
so the stored question is byte-identical to the file, CRLF included.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import typer
from graph_works_core.orchestrate.asks import run_ask, run_ask_answer
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_wire import work as wire_work

from graph_works_cli import exit_codes
from graph_works_cli.work_cli import rendering
from graph_works_cli.workspace_resolution import resolve_workspace


def _now() -> datetime:
    return datetime.now(UTC)


def _options(raw: list[str]) -> list[tuple[str, str]]:
    """`token=label` pairs; a bare `token` labels itself. Validation is core's."""
    parsed: list[tuple[str, str]] = []
    for entry in raw:
        token, sep, label = entry.partition("=")
        parsed.append((token, label.strip() if sep else token))
    return parsed


def _question(question: str, question_file: str) -> str:
    if bool(question) == bool(question_file):
        rendering.fail("pass exactly one of --question or --question-file", reason="usage")
    if not question_file:
        return question
    try:
        with Path(question_file).open(encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        rendering.fail(f"{question_file}: {exc}", reason="io", cause=exc)


def ask(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    kind: str = typer.Option(..., "--kind", help="spec-review | choice | free."),
    summary: str = typer.Option(..., "--summary", help="One line, at most 300 characters; Orca carries it."),
    question: str = typer.Option("", "--question", help="The full question (markdown), stored verbatim."),
    question_file: str = typer.Option("", "--question-file", help="Read the full question from this file."),
    spec: str = typer.Option("", "--spec", help="spec-review only: the spec, as a path or root-absolute resource."),
    option: list[str] = typer.Option(  # noqa: B008 -- Typer declares CLI options in defaults
        [], "--option", help="choice only, repeatable: <token>=<label>."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan the payload without writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the payload location and the Orca strings as JSON."),
) -> None:
    """Write a typed ask payload; print what to pass to `orca orchestration ask`."""
    layout = resolve_workspace(workspace)
    text = _question(question, question_file)
    try:
        result = run_ask(
            layout,
            path,
            kind=kind,
            summary=summary,
            question=text,
            spec=spec or None,
            options=_options(option),
            created=_now(),
            dry_run=dry_run,
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)

    payload = wire_work.ask_payload(result)
    if not result.ok:
        rendering.fail(
            f"{path}: refused ({', '.join(result.refusals)}); nothing was written", reason="refused", payload=payload
        )
    if json_output:
        rendering.emit(payload)
        return
    typer.echo(f"[ok] {'would write' if dry_run else 'wrote'} {result.resource}")
    typer.echo("question:")
    typer.echo(result.orca_question or "")
    typer.echo(f"options: {result.orca_options}" if result.orca_options else "options: (none -- omit --options)")


def ask_answer(
    payload_path: str = typer.Argument(
        ..., metavar="PAYLOAD", help="The payload file, or the root-absolute resource from its gw-ask: line."
    ),
    choice: str = typer.Option("", "--choice", help="The chosen option token (spec-review, choice)."),
    effort: str = typer.Option("", "--effort", help="spec-review approve: xtra-small|small|medium|large|xtra-large."),
    notes: str = typer.Option("", "--notes", help="Free text; the whole answer for a free ask."),
    by: str = typer.Option(
        "human", "--by", help="Who answered; the coordinator's auto-merge passes policy:auto-merge."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate the answer without recording it."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the recorded answer and reply body as JSON."),
) -> None:
    """Record a human's answer to a typed ask and print the reply body."""
    layout = resolve_workspace(workspace)
    try:
        result = run_ask_answer(
            layout,
            payload_path,
            choice=choice or None,
            effort=effort or None,
            notes=notes or None,
            at=_now(),
            by=by,
            dry_run=dry_run,
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)

    payload = wire_work.ask_answer_payload(result)
    if not result.ok:
        rendering.fail(
            f"{payload_path}: refused ({', '.join(result.refusals)}); nothing was recorded",
            reason="refused",
            payload=payload,
        )
    if json_output:
        rendering.emit(payload)
        return
    verb = "would record" if dry_run and result.changed else "recorded" if result.changed else "already recorded"
    typer.echo(f"[ok] {verb} answer in {result.resource}")
    typer.echo(f"reply: {result.reply_body}")
