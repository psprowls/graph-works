"""`epic_brief` -- the per-epic carried-context brief (this item's ledger, D-001 to D-003).

For a child of an Epic or Release, at design, plan and execute: the epic
header, the child's own index row, the epic's answered decisions that apply to
it, every landed sibling, and one conflict-flag line saying whether the brief
can stand in for the epic design. `carried.py`'s `_epic_brief` wraps the
result as a `SlotFill`; this module does not import `carried` (it would be
circular).

**Read-only, fail-soft.** A missing referenced file reads as "not found"; any
other `OSError` or a `ValueError` propagates to `assemble_carried`, which turns
it into a slot warning. Git failures are warnings. Unbudgeted: over `SOFT_CAP`
tokens is a warning, never a truncation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Final

from code_graph_io.tokens import count_tokens
from work_tracker_okf import decisions as _decisions
from work_tracker_okf.affects import code_affects
from work_tracker_okf.decisions import Decision, ledger_ref, prose_block
from work_tracker_okf.items import WorkItem

from graph_works_core.guidance.assembly import covered_epic
from graph_works_core.workspace import provenance
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.landed import compare_to_baseline, has_landed
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_item_repo

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None

SOFT_CAP: Final = 4000
NO_FLAGS_LINE: Final = "No conflict flags: this brief stands in for the epic design; do not read it in full."
FLAG_PREFIX: Final = "Read the full epic design before relying on this brief: "
_CELL_SPLIT: Final = re.compile(r"(?<!\\)\|")


def _no_data() -> Mapping[str, JsonValue]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class EpicBrief:
    lines: tuple[str, ...] = ()
    data: Mapping[str, JsonValue] = field(default_factory=_no_data)
    warnings: tuple[str, ...] = ()


def _read(bundle_root: Path, resource: str | None) -> str | None:
    if not resource:
        return None
    try:
        return (bundle_root / resource.lstrip("/")).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _cells(line: str) -> tuple[str, ...]:
    return tuple(cell.strip() for cell in _CELL_SPLIT.split(line.strip().strip("|")))


def _table_under(text: str, heading: str) -> tuple[tuple[str, ...], list[tuple[str, ...]]] | None:
    """The first markdown table directly under *heading* (header row, body rows)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == heading), None)
    if start is None:
        return None
    rows: list[tuple[str, ...]] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("#"):
            break
        if stripped.startswith("|"):
            rows.append(_cells(stripped))
        elif rows:
            break
    if len(rows) < 2:
        return None
    header, _separator, *body = rows
    return header, body


def _row(header: tuple[str, ...], cells: tuple[str, ...]) -> dict[str, str]:
    return {h: c for h, c in zip(header, cells, strict=False) if h and c}


def _index_row(bundle_root: Path, sources: Mapping[str, str], item: WorkItem) -> dict[str, str] | None:
    link = f"](/{item.path}.md)"
    plan = _read(bundle_root, sources.get("plan"))
    if plan is not None and (table := _table_under(plan, "## Children")) is not None:
        header, body = table
        for cells in body:
            if any(link in cell for cell in cells):
                return _row(header, cells)
    design = _read(bundle_root, sources.get("design"))
    if design is not None and (table := _table_under(design, "## Child index")) is not None:
        header, body = table
        if "Title" in header:
            column = header.index("Title")
            for cells in body:
                if column < len(cells) and cells[column] == item.title:
                    return _row(header, cells)
    return None


def _applies(decision: Decision, item: WorkItem) -> bool:
    """Unscoped entries apply to every child; scoped ones only when they name this item."""
    if not decision.affects:
        return True
    names = {item.path, item.path.rsplit("/", 1)[-1]}
    return any(entry.strip().lstrip("/").removesuffix(".md") in names for entry in decision.affects)


def _code_repo(layout: WorkspaceLayout, items: Sequence[WorkItem], item: WorkItem) -> Path | None:
    """The item's code repository (test seam); `WorkspaceError` propagates to the caller."""
    return resolve_item_repo(layout, item, {other.path: other for other in items}).path


def _short(sha: str) -> str:
    return sha[:12]


def epic_brief(layout: WorkspaceLayout, items: Sequence[WorkItem], item: WorkItem) -> EpicBrief:
    epic = covered_epic(items, item)
    if epic is None:
        return EpicBrief()
    root = layout.bundle_dir
    warnings: list[str] = []
    flags: list[str] = []
    sources = {s.id: s.resource for s in epic.sources if s.id and s.resource}
    design = sources.get("design")
    ledger = ledger_ref(epic.path)
    design_link = f"[design]({design})" if design else "none"
    lines = [f"Epic: [{epic.title}](/{epic.path}.md) — design: {design_link}; ledger: [ledger]({ledger.resource})"]

    row = _index_row(root, sources, item)
    if row is None:
        lines.append("Own index row: not found.")
    else:
        lines.append("Own index row:")
        lines.extend(f"- {header}: {cell}" for header, cell in row.items())

    decisions: list[JsonValue] = []
    missing = 0
    for decision in _decisions.load(ledger.path(root)).entries:
        if decision.status != "answered" or not _applies(decision, item):
            continue
        answer = prose_block(decision, "Answer")
        if not answer:
            missing += 1
            continue
        answer = " ".join(answer.split())
        decisions.append({"id": decision.id, "question": decision.question, "answer": answer})
    if missing:
        warnings.append(f"epic_brief: answered epic decisions with no **Answer:** paragraph, skipped: {missing}")
    lines.append("Epic decisions (answered):" if decisions else "No answered epic decision applies.")
    lines.extend(f"- {d['id']} — {d['question']}: {d['answer']}" for d in decisions if isinstance(d, dict))

    baseline = item.spec_baseline
    code = baseline.code if baseline is not None else None
    own = set(code_affects(item.affects))
    landed = [o for o in items if o.parent_path == item.parent_path and o.path != item.path and has_landed(o)]
    repo: Path | None = None
    if code is not None and landed:
        try:
            repo = _code_repo(layout, items, item)
        except WorkspaceError as exc:
            warnings.append(f"epic_brief: {exc}")
        else:
            if repo is None:
                warnings.append("epic_brief: no code repository resolved; baseline markers omitted")
    siblings: list[JsonValue] = []
    flagged: list[str] = []
    lines.append("Landed siblings:" if landed else "No sibling has landed.")
    for sibling in landed:
        ref = sibling.resolved_in or ""
        overlaps = bool(own & set(code_affects(sibling.affects)))
        new: bool | None = None
        if code is not None and repo is not None:
            comparison = compare_to_baseline(repo, ref, code)
            new = comparison.new
            if comparison.missing:
                warnings.append(f"epic_brief: {sibling.path} resolved_in {_short(ref)} is not a commit in {repo}")
            elif new is None:
                warnings.append(
                    f"epic_brief: could not compare {sibling.path} resolved_in {_short(ref)} "
                    f"with the baseline ({comparison.cause})"
                )
        marker = "; new since your baseline" if new is True else ""
        lines.append(
            f"- [{sibling.path}](/{sibling.path}.md) — {sibling.description}; resolved in {_short(ref)}; "
            f"affects overlap: {'yes' if overlaps else 'no'}{marker}"
        )
        siblings.append({"path": sibling.path, "resolved_in": ref, "overlaps": overlaps, "new_since_baseline": new})
        if overlaps and (code is None or new is not False):
            flagged.append(sibling.path)
    if flagged:
        flags.append(f"overlapping landed sibling(s): {', '.join(flagged)}")

    workspace = baseline.workspace if baseline is not None else None
    watched = [str(root / r.lstrip("/")) for r in (design, ledger.resource) if r]
    if workspace is not None:
        out = provenance.run_git(layout.root, "rev-list", "--count", f"{workspace}..HEAD", "--", *watched)
        if out is None or not out.strip().isdigit():
            warnings.append(f"epic_brief: could not count workspace commits since {_short(workspace)}")
        elif (count := int(out.strip())) > 0:
            flags.append(f"{count} workspace commit(s) touched the epic design or ledger since {_short(workspace)}")
    if row is None:
        flags.append("own index row not found")
    lines.append(FLAG_PREFIX + "; ".join(flags) + "." if flags else NO_FLAGS_LINE)

    try:
        tokens = count_tokens("\n".join(lines))
    except (OSError, ValueError) as exc:
        warnings.append(f"epic_brief: token count skipped: {exc}")
    else:
        if tokens > SOFT_CAP:
            warnings.append(f"epic_brief: {tokens} tokens, over the {SOFT_CAP}-token soft cap")

    data: dict[str, JsonValue] = {
        "epic": epic.path,
        "index_row": dict(row) if row is not None else None,
        "decisions": decisions,
        "siblings": siblings,
        "flags": list(flags),
    }
    return EpicBrief(lines=tuple(lines), data=MappingProxyType(data), warnings=tuple(warnings))


__all__ = ["FLAG_PREFIX", "NO_FLAGS_LINE", "SOFT_CAP", "EpicBrief", "epic_brief"]
