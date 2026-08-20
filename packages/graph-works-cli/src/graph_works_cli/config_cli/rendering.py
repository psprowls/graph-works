"""Deterministic output for `gw config`; no path discovery or domain work."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from config_io import Resolved
from graph_works_core.hooks import HooksResult


def render_projection(path: Path, *, json_output: bool) -> str:
    """Render the path returned by `write_projection`."""
    if json_output:
        return json.dumps({"projection": path}, indent=2, default=str)
    return f"[ok] projection: {path}"


def render_resolved(result: Resolved, *, json_output: bool) -> str:
    """Render one effective value in the spec's plain or `asdict` JSON shape."""
    if json_output:
        return json.dumps(asdict(result), indent=2, default=str)
    lines = [f"{result.key} = {result.value!r}  (origin: {result.origin})"]
    if result.shadowed is not None and result.entry.env_var is not None:
        lines.append(f"  note: manifest value {result.shadowed!r} is shadowed by ${result.entry.env_var}")
    return "\n".join(lines)


def render_resolved_list(results: Sequence[Resolved], *, json_output: bool) -> str:
    """Render every catalog row, retaining concrete wildcard expansions."""
    if json_output:
        return json.dumps([asdict(result) for result in results], indent=2, default=str)
    lines: list[str] = []
    for result in results:
        marker = {"env": "*", "manifest": "+", "default": " "}[result.origin]
        lines.append(f"{marker} {result.key} = {result.value!r}  [{result.origin}; default {result.entry.default!r}]")
        lines.append(f"    {result.entry.description}")
    return "\n".join(lines)


def render_hooks(result: HooksResult, *, json_output: bool) -> str:
    """Render the typed core hook result without inspecting settings content."""
    if json_output:
        return json.dumps(asdict(result), indent=2, default=str)
    lines = [f"[ok] {result.settings_path}"]
    lines.extend(f"  added: {script}" for script in result.added)
    lines.extend(f"  removed: {script}" for script in result.removed)
    lines.extend(f"  already registered: {script}" for script in result.skipped)
    return "\n".join(lines)
