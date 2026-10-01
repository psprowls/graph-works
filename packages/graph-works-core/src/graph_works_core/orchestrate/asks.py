"""`gw work ask` / `gw work ask-answer`: the typed-ask payload file on disk.

gw prepares and the worker sends (design feature-structured-worker-ask Q1):
`run_ask` writes the payload and returns the exact `--question` / `--options`
strings; the worker passes them to its own `orca orchestration ask`, so gw
never holds a worker's capability and never owns a timeout or a resume.

Plan by default (ADR 2026-08-18): `dry_run=True` computes everything and
writes nothing. Reads no clock -- `created` / `at` are inputs. Writes happen
under the item's decision-owner lock, the lock filing and stage advance
share; a payload is not a concept page, so no bundle transaction is involved.
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from work_tracker_okf import asks as _asks
from work_tracker_okf.items import IGNORE, WorkItem, load_items

from graph_works_core.workspace.bundle import load_workspace_bundle
from graph_works_core.workspace.decision_owner import locked_decision_owner
from graph_works_core.workspace.layout import WorkspaceLayout


@dataclass(frozen=True, slots=True)
class AskResult:
    refusals: tuple[str, ...]
    payload_path: Path | None
    resource: str | None
    orca_question: str | None
    orca_options: str | None
    applied: bool

    @property
    def ok(self) -> bool:
        return not self.refusals


@dataclass(frozen=True, slots=True)
class AskAnswerResult:
    refusals: tuple[str, ...]
    payload_path: Path | None
    resource: str | None
    reply_body: str | None
    changed: bool
    applied: bool

    @property
    def ok(self) -> bool:
        return not self.refusals


def _refused(*codes: str) -> AskResult:
    return AskResult(codes, None, None, None, None, False)


def _beneath(layout: WorkspaceLayout, value: str, *, suffix: str | None = None) -> Path | None:
    """*value* as a filesystem path or a root-absolute resource, resolved to a
    file beneath the bundle; `None` when it is neither."""
    bundle = layout.bundle_dir.resolve()
    candidates = [Path(value)]
    if value.startswith("/"):
        candidates.append(bundle / value.lstrip("/"))
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file() and resolved.is_relative_to(bundle) and (suffix is None or resolved.suffix == suffix):
            return resolved
    return None


def _resource(layout: WorkspaceLayout, target: Path) -> str:
    return "/" + target.relative_to(layout.bundle_dir.resolve()).as_posix()


def run_ask(
    layout: WorkspaceLayout,
    path: str,
    *,
    kind: str,
    summary: str,
    question: str,
    spec: str | None,
    options: Sequence[tuple[str, str]],
    created: datetime,
    dry_run: bool = True,
) -> AskResult:
    """Build and (unless *dry_run*) write one `gw.ask/1` payload for *path*."""
    directory = layout.bundle_dir.resolve() / path / "references" / _asks.ASKS_DIR

    def prepare(item: WorkItem) -> tuple[_asks.AskPayload | None, tuple[str, ...]]:
        current_spec = _beneath(layout, spec) if spec else None
        built = _asks.build_ask(
            item=path,
            phase=item.phase,
            kind=kind,
            created=created,
            summary=summary,
            question=question,
            spec=_resource(layout, current_spec) if current_spec is not None else None,
            current_effort=item.effort,
            options=tuple(_asks.AskOption(token, label) for token, label in options),
        )
        return built.payload, built.refusals

    def target(payload: _asks.AskPayload) -> Path | None:
        # A symlink in any component of the ask directory redirects both the
        # directory scan and the eventual write. Refuse it even for dry runs.
        if directory.resolve() != directory or not directory.is_relative_to(layout.bundle_dir.resolve()):
            return None
        existing = sorted(entry.name for entry in directory.iterdir()) if directory.is_dir() else []
        return directory / _asks.next_ask_name(existing, phase=payload.phase, kind=payload.kind)

    items = {item.path: item for item in load_items(load_workspace_bundle(layout, ignore=IGNORE))}
    item = items.get(path)
    if item is None:
        return _refused("unknown-item")
    payload, refusals = prepare(item)
    if payload is None:
        return _refused(*refusals)
    if dry_run:
        chosen = target(payload)
    else:
        try:
            with locked_decision_owner(layout, path) as context:
                current = next((item for item in context.items if item.path == path), None)
                if current is None:
                    return _refused("unknown-item")
                payload, refusals = prepare(current)
                if payload is None:
                    return _refused(*refusals)
                chosen = target(payload)
                if chosen is None:
                    return _refused("payload-invalid")
                directory.mkdir(parents=True, exist_ok=True)
                # Exclusive create: a name taken since `target()` read the
                # directory fails loudly instead of overwriting an ask.
                with chosen.open("xb") as handle:
                    handle.write(payload.to_json().encode("utf-8"))
        except ValueError:
            return _refused("unknown-item")
    if chosen is None:
        return _refused("payload-invalid")
    resource = _resource(layout, chosen)
    return AskResult(
        (), chosen, resource, _asks.orca_question(payload, resource), _asks.orca_options(payload), not dry_run
    )


def _load(target: Path) -> _asks.AskPayload | None:
    try:
        return _asks.parse_payload(target.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def _decide(
    target: Path,
    resource: str,
    loaded: _asks.AskPayload,
    *,
    choice: str | None,
    effort: str | None,
    notes: str | None,
    at: datetime,
    by: str,
) -> tuple[AskAnswerResult, _asks.AskPayload | None]:
    """The answer outcome, plus the payload to write when one must be written."""
    refusals = _asks.validate_answer(loaded, choice=choice, effort=effort, notes=notes, by=by)
    if refusals:
        return AskAnswerResult(refusals, target, resource, None, False, False), None
    if loaded.answer is not None:
        if _asks.same_answer(loaded.answer, choice=choice, effort=effort, notes=notes):
            return AskAnswerResult((), target, resource, _asks.reply_body(resource, loaded.answer), False, False), None
        return AskAnswerResult(("already-answered",), target, resource, None, False, False), None
    updated = _asks.answered(loaded, choice=choice, effort=effort, notes=notes, at=at, by=by)
    assert updated.answer is not None
    return AskAnswerResult((), target, resource, _asks.reply_body(resource, updated.answer), True, False), updated


def run_ask_answer(
    layout: WorkspaceLayout,
    payload: str,
    *,
    choice: str | None,
    effort: str | None,
    notes: str | None,
    at: datetime,
    by: str,
    dry_run: bool = True,
) -> AskAnswerResult:
    """Validate an answer against the payload and (unless *dry_run*) record it.

    An identical replay is a successful no-op returning the same reply body,
    so a duplicated coordinator delivery is harmless; a different answer to an
    answered ask refuses `already-answered`.
    """
    bundle = layout.bundle_dir.resolve()
    candidates = [Path(payload).absolute()]
    if payload.startswith("/"):
        candidates.append(bundle / payload.lstrip("/"))
    target = next(
        (
            candidate
            for candidate in candidates
            if candidate.is_file()
            and candidate.suffix == ".json"
            and candidate.is_relative_to(bundle)
            and candidate.resolve() == candidate
        ),
        None,
    )
    if target is None:
        return AskAnswerResult(("payload-invalid",), None, None, None, False, False)
    resource = _resource(layout, target)
    parts = target.relative_to(bundle).parts
    if len(parts) < 4 or parts[-3:-1] != ("references", _asks.ASKS_DIR):
        return AskAnswerResult(("payload-invalid",), target, resource, None, False, False)
    item_path = "/".join(parts[:-3])
    loaded = _load(target)
    if loaded is None or loaded.item != item_path:
        return AskAnswerResult(("payload-invalid",), target, resource, None, False, False)
    if item_path not in {item.path for item in load_items(load_workspace_bundle(layout, ignore=IGNORE))}:
        return AskAnswerResult(("unknown-item",), target, resource, None, False, False)
    outcome, updated = _decide(target, resource, loaded, choice=choice, effort=effort, notes=notes, at=at, by=by)
    if dry_run or updated is None:
        return outcome
    try:
        lock = locked_decision_owner(layout, item_path)
        with lock:
            # Re-decide under the lock: another answer may have landed since.
            if not target.is_file() or target.resolve() != target:
                return AskAnswerResult(("payload-invalid",), target, resource, None, False, False)
            loaded = _load(target)
            if loaded is None or loaded.item != item_path:
                return AskAnswerResult(("payload-invalid",), target, resource, None, False, False)
            outcome, updated = _decide(
                target, resource, loaded, choice=choice, effort=effort, notes=notes, at=at, by=by
            )
            if updated is None:
                return outcome
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=target.parent, prefix=target.name + ".", delete=False
            ) as handle:
                scratch = Path(handle.name)
                handle.write(updated.to_json().encode("utf-8"))
            try:
                scratch.replace(target)
            finally:
                scratch.unlink(missing_ok=True)
    except ValueError:
        return AskAnswerResult(("unknown-item",), target, resource, None, False, False)
    return AskAnswerResult((), target, resource, outcome.reply_body, True, True)


__all__ = ["AskAnswerResult", "AskResult", "run_ask", "run_ask_answer"]
