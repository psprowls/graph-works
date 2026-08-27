"""`gw util platform` — what am I actually getting on this machine?

Formatting only. Every fact comes from `build_report()`, which derives each
answer from the machinery that owns it (ADR-0013: all logic stays in core).

Two contracts are stated here so nobody later "fixes" them:

* **Exit is always 0**, whatever is unavailable and whatever a probe found.
  This is a report, not a check; consumers gate on `--json` fields.
* **A probed value is rendered beside its declaration, never in place of it.**
  A box that declares `workflow-orca` and probes "orca not on PATH" must show
  both — that disagreement is the single most useful thing this verb can say.

`--workspace` is resolved only under `--probe`. The default run must work on a
machine with no workspace at all: a user diagnosing "why does gw not start
here?" is exactly the caller who has not got a working workspace.

The module name does not shadow the stdlib `platform` module — Python 3
imports are absolute.
"""

from __future__ import annotations

import json
from typing import Any

import typer
from graph_works_core.util.platform import PlatformReport, build_report

from graph_works_cli.workspace_resolution import resolve_workspace


def _payload(report: PlatformReport) -> dict[str, Any]:
    return {
        "schema_version": report.schema_version,
        "platform": report.platform,
        "python": report.python,
        "capabilities": [
            {
                "name": capability.name,
                "value": capability.value,
                "status": capability.status,
                "detail": capability.detail,
                "guarantees": list(capability.guarantees),
                "provider": capability.provider,
            }
            for capability in report.capabilities
        ],
        "probes": [
            {
                "capability": probe.capability,
                "status": probe.status,
                "detail": probe.detail,
                "agrees_with_declared": probe.agrees_with_declared,
            }
            for probe in report.probes
        ],
        "unavailable": list(report.unavailable),
    }


def _human(report: PlatformReport) -> str:
    lines = [f"platform: {report.platform}", f"python: {report.python}", ""]
    for capability in report.capabilities:
        lines.append(f"{capability.name}: {capability.value} ({capability.status})")
        lines.append(f"  {capability.detail}")
        lines.extend(f"  guarantee: {guarantee}" for guarantee in capability.guarantees)
        lines.append(f"  provider: {capability.provider}")
        for probe in report.probes:
            if probe.capability != capability.name:
                continue
            suffix = "" if probe.agrees_with_declared else " [disagrees with declared]"
            lines.append(f"  probed: {probe.status} — {probe.detail}{suffix}")
    lines.append("")
    lines.append(f"unavailable: {', '.join(report.unavailable) or 'none'}")
    return "\n".join(lines)


def platform(
    probe: bool = typer.Option(False, "--probe", help="Also run liveness checks. Requires a workspace."),
    workspace: str = typer.Option(
        "", "--workspace", help="Workspace root; defaults to discovery. Only read under --probe."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit the whole report as JSON."),
) -> None:
    """Report the platform, durability tier, dispatch backend and unavailable components."""
    layout = resolve_workspace(workspace) if probe else None
    report = build_report(layout=layout, probe=probe)
    typer.echo(json.dumps(_payload(report), indent=2) if json_output else _human(report))
