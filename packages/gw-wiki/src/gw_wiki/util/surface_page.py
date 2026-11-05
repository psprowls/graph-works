"""Render and reconcile `docs/reference/gw-cli-surface.md` from the CLI's plain-data surface.

The CLI band owns Typer, so this module never sees it: the caller hands over plain-data
command entries (the shape `gw help --json` and `gw util describe-surface` emit) in
registration order, and gets markdown back. The page is reconciled, not regenerated: the
region from the `## Global options` heading up to the `## See also` heading is generated, and
every other byte -- frontmatter, title, summary, see-also -- is copied through. Nothing here
reads the clock, the terminal width or a path from the environment; `today` is an argument.
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import okf_io
from gw_kernel.workspace.layout import WorkspaceLayout

#: Bundle-relative location of the page.
PAGE_RELPATH = "docs/reference/gw-cli-surface.md"
#: First line of the generated region.
START_HEADING = "## Global options"
#: First line after the generated region, which stays hand-owned.
END_HEADING = "## See also"
#: Typer's own options, which are not part of gw's surface.
TYPER_OPTIONS = frozenset({"--install-completion", "--show-completion"})

PlanStatus = Literal["current", "stale", "refused"]
RefusalReason = Literal["missing-page", "missing-anchor", "duplicate-anchor", "misordered-anchors"]

_LIST_START = re.compile(r"^\s*(\||- |\* |\d+\. |```)", re.MULTILINE)


def _unwrap(help_text: str) -> str:
    """One line per paragraph, except paragraphs holding a list, table or code fence."""
    paragraphs = re.split(r"\n\s*\n", help_text)
    return "\n\n".join(
        paragraph if _LIST_START.search(paragraph) else re.sub(r"\s*\n\s*", " ", paragraph).strip()
        for paragraph in paragraphs
    )


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _default_cell(option: Mapping[str, Any]) -> str:
    default = option["default"]
    if option["secondary_opts"]:
        return f"`{default}`"
    if option["is_flag"]:
        return "on" if default else "off"
    if default is None or default == "" or default == []:
        return ""
    if isinstance(default, list):
        return f"`{json.dumps(default)}`"
    return f"`{default}`"


def _option_name(option: Mapping[str, Any]) -> str:
    opts: Sequence[str] = option["opts"]
    secondary: Sequence[str] = option["secondary_opts"]
    if secondary:
        return f"`{opts[0]}` / `{secondary[0]}`"
    return f"`{opts[0]}`" if len(opts) == 1 else f"`{opts[0]}` / `{opts[-1]}`"


def _tables(entry: Mapping[str, Any], out: list[str]) -> None:
    arguments = entry["arguments"]
    if arguments:
        out.append("| Argument | Required | Variadic |\n|---|---|---|")
        out.extend(
            f"| `{a['name']}` | {'yes' if a['required'] else 'no'} | {'yes' if a['nargs'] == -1 else 'no'} |"
            for a in arguments
        )
        out.append("")
    options = entry["options"]
    if options:
        out.append("| Option | Required | Default | Description |\n|---|---|---|---|")
        for o in options:
            name = _option_name(o) + (" (flag)" if o["is_flag"] else "")
            out.append(f"| {name} | {'yes' if o['required'] else ''} | {_default_cell(o)} | {_cell(o['help'])} |")
        out.append("")


def render_sections(root: Mapping[str, Any], commands: Sequence[Mapping[str, Any]], version: str) -> str:
    """The generated region: global options, top-level commands, command reference.

    *root* is the root help entry; *commands* are every non-root node, depth-first in
    registration order (each entry carries `path`). Ends with a single newline.
    """
    out: list[str] = [START_HEADING, ""]
    global_options = [o for o in root["options"] if not TYPER_OPTIONS & set(o["opts"])]
    out.append("| Option | Default | Description |\n|---|---|---|")
    out.extend(f"| {_option_name(o)} | {_default_cell(o)} | {_cell(o['help'])} |" for o in global_options)
    out.append("")
    out.extend(["## Top-level commands", ""])
    out.extend(f"- `gw {sub['name']}` — {sub['help']}" for sub in root["commands"])
    out.append("")
    out.extend(["## Command reference", "", f"Generated from gw {version}.", ""])
    for entry in commands:
        name = " ".join(("gw", *entry["path"]))
        out.append("#" * (len(entry["path"]) + 2) + f" `{name}`\n")
        if entry["help"]:
            out.append(_unwrap(entry["help"]) + "\n")
        out.append(f"Usage: `{name} {' '.join(entry['usage'])}`\n")
        if entry["commands"]:
            out.append("Subcommands: " + ", ".join(f"`{c}`" for c in entry["commands"]) + "\n")
        _tables(entry, out)
    return "\n".join(out).rstrip("\n") + "\n"


@dataclass(frozen=True, slots=True)
class SurfacePagePlan:
    """What reconciling the page would do. Writes nothing; `apply_surface_page` does."""

    path: Path
    status: PlanStatus
    refusal: RefusalReason | None
    detail: str
    before: str
    after: str
    added: int
    removed: int

    @property
    def changed(self) -> bool:
        return self.status == "stale"


def _refused(path: Path, reason: RefusalReason, detail: str, before: str = "") -> SurfacePagePlan:
    return SurfacePagePlan(path, "refused", reason, detail, before, before, 0, 0)


def page_path(layout: WorkspaceLayout) -> Path:
    return layout.bundle_dir / PAGE_RELPATH


def _anchor_lines(lines: list[str], heading: str) -> list[int]:
    return [i for i, line in enumerate(lines) if line.rstrip("\r\n") == heading]


def plan_surface_page(layout: WorkspaceLayout, sections: str, *, today: date) -> SurfacePagePlan:
    """Compare the page against *sections*. Missing page or anchors refuse; nothing is written."""
    path = page_path(layout)
    try:
        before = path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return _refused(path, "missing-page", f"{path} does not exist")
    lines = before.splitlines(keepends=True)
    starts = _anchor_lines(lines, START_HEADING)
    ends = _anchor_lines(lines, END_HEADING)
    for heading, found in ((START_HEADING, starts), (END_HEADING, ends)):
        if not found:
            return _refused(path, "missing-anchor", f"heading {heading!r} not found", before)
        if len(found) > 1:
            return _refused(path, "duplicate-anchor", f"heading {heading!r} appears {len(found)} times", before)
    if starts[0] >= ends[0]:
        return _refused(path, "misordered-anchors", f"{START_HEADING!r} must precede {END_HEADING!r}", before)

    newline = "\r\n" if "\r\n" in before else "\n"
    generated = sections.replace("\n", newline)
    if not generated.endswith(newline * 2):
        generated += newline
    after = "".join(lines[: starts[0]]) + generated + "".join(lines[ends[0] :])
    if after == before:
        return SurfacePagePlan(path, "current", None, "page is current", before, before, 0, 0)

    document = okf_io.parse(after, path=path)
    document.set("updated", today)
    after = document.serialize()
    counts = _diff_counts(before, after)
    return SurfacePagePlan(path, "stale", None, "page is stale", before, after, *counts)


def _diff_counts(before: str, after: str) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.ndiff(before.splitlines(), after.splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


def apply_surface_page(plan: SurfacePagePlan) -> bool:
    """Write a stale plan's page. Returns whether anything was written."""
    if plan.status != "stale":
        return False
    with plan.path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(plan.after)
    return True
