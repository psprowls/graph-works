"""Deterministic output for `gw config`; no path discovery or domain work."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from config_io import Resolved
from graph_works_core.hooks import HooksResult
from graph_works_wire.config import hooks_payload, projection_payload, resolved_list_payload, resolved_payload

from graph_works_cli.json_output import encode


def render_projection(path: Path, *, json_output: bool) -> str:
    """Render the path returned by `write_projection`."""
    if json_output:
        return encode(projection_payload(path))
    return f"[ok] projection: {path}"


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
