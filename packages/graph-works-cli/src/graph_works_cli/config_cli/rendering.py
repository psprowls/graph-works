"""Deterministic output for `gw config`; no path discovery or domain work."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from config_io import Resolved
from graph_works_core.hooks import HooksResult
from graph_works_core.workspace.work_schemas import SchemaRefreshPlan, SchemaRefreshResult
from graph_works_wire.config import (
    hooks_payload,
    projection_payload,
    resolved_list_payload,
    resolved_payload,
    schema_refresh_payload,
)

from graph_works_cli.json_output import encode


def render_projection(path: Path, *, json_output: bool) -> str:
    """Render the path returned by `write_projection`."""
    if json_output:
        return encode(projection_payload(path))
    return f"[ok] projection: {path}"


def render_schema_refresh(plan: SchemaRefreshPlan, result: SchemaRefreshResult | None, *, json_output: bool) -> str:
    """Render a schema refresh preview or its applied result."""
    if json_output:
        return encode(schema_refresh_payload(plan, result))
    lines: list[str] = []
    for write in plan.writes:
        lines.append(f"+ {write.relative} (create)" if write.before is None else f"~ {write.relative} (replace)")
        if write.diff:
            lines.append(write.diff.rstrip("\n"))
    for refusal in plan.refusals:
        lines.append(f"! {refusal.relative} refused ({refusal.reason}): {refusal.detail}")
        if refusal.diff:
            lines.append(refusal.diff.rstrip("\n"))
    lines.append(f"= {len(plan.skipped)} current")
    if plan.provenance is not None:
        lines.append("+ provenance")
    if result is None:
        lines.append("preview only; re-run with --apply to write")
        if any(refusal.reason in ("edited", "unrecorded") for refusal in plan.refusals):
            lines.append("edited/unrecorded files need --force after review")
        if any(refusal.reason == "unsafe" for refusal in plan.refusals):
            lines.append("repair unsafe targets or their parents, then re-run the preview; force cannot bypass them")
    else:
        lines.append(f"[ok] wrote {len(result.written)} file(s)")
        if result.commit is not None:
            lines.append(f"commit: {result.commit.status} {result.commit.sha or result.commit.reason or ''}".rstrip())
    return "\n".join(lines)


def render_resolved(result: Resolved, *, json_output: bool) -> str:
    """Render one effective value, plain or as the wire projection."""
    if json_output:
        return encode(resolved_payload(result))
    lines = [f"{result.key} = {result.value!r}  (origin: {result.origin})"]
    if result.shadowed is not None and result.entry.env_var is not None and result.origin == "env":
        lines.append(f"  note: manifest value {result.shadowed!r} is shadowed by ${result.entry.env_var}")
    elif result.origin == "local" and result.shadowed is not None:
        lines.append(f"  note: workspace.yaml value {result.shadowed!r} is shadowed by workspace.local.yaml")
    return "\n".join(lines)


def render_resolved_list(results: Sequence[Resolved], *, json_output: bool) -> str:
    """Render every catalog row, retaining concrete wildcard expansions."""
    if json_output:
        return encode(resolved_list_payload(results))
    lines: list[str] = []
    for result in results:
        # A fourth origin with no entry here is a KeyError at render time, not
        # a missing glyph — which is why the two move in the same change.
        marker = {"env": "*", "local": "~", "manifest": "+", "default": " "}[result.origin]
        lines.append(f"{marker} {result.key} = {result.value!r}  [{result.origin}; default {result.entry.default!r}]")
        lines.append(f"    {result.entry.description}")
    return "\n".join(lines)


def render_hooks(result: HooksResult, *, json_output: bool) -> str:
    """Render the typed core hook result without inspecting settings content."""
    if json_output:
        return encode(hooks_payload(result))
    lines = [f"[ok] {result.settings_path}"]
    lines.extend(f"  added: {script}" for script in result.added)
    lines.extend(f"  removed: {script}" for script in result.removed)
    lines.extend(f"  already registered: {script}" for script in result.skipped)
    return "\n".join(lines)
