"""dispatch-rules.md's tables equal the code they describe (item D-002).

Tables are located by heading and compared as parsed data: match cells are
YAML flow mappings and stage cells YAML flow sequences, so spacing never
matters. A missing heading or table fails naming the heading.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from graph_works_core.workspace.pipeline import ATTEND_TAIL, EXECUTE_TAIL, PACKAGED_PIPELINE, WORKSPACE_COMMIT_TAIL
from ruamel.yaml import YAML
from work_tracker_okf.pipeline import ATTRIBUTES, PACKAGED_DEFINITION, VARIANT_REPLACEMENTS

DOC = Path(__file__).resolve().parents[1] / "docs" / "dispatch-rules.md"
TAIL_NAMES = {ATTEND_TAIL: "ATTEND_TAIL", WORKSPACE_COMMIT_TAIL: "WORKSPACE_COMMIT_TAIL", EXECUTE_TAIL: "EXECUTE_TAIL"}
VARIANT_SECTIONS = {"## Retired `variant`", "## Initialization and explicit cutover"}


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _headed_lines(text: str) -> list[tuple[str | None, str | None, str]]:
    """Each line outside a fence with its enclosing `##` and nearest heading."""
    section: str | None = None
    nearest: str | None = None
    fenced = False
    out: list[tuple[str | None, str | None, str]] = []
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        if not fenced and line.startswith("#"):
            nearest = line.strip()
            if line.startswith("## "):
                section = nearest
            continue
        if not fenced:
            out.append((section, nearest, line))
    return out


def _headings(text: str) -> set[str]:
    """Heading lines outside fenced blocks."""
    found: set[str] = set()
    fenced = False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("#"):
            found.add(line.strip())
    return found


def _is_separator(line: str) -> bool:
    return set(line.replace("|", "").strip()) <= {"-", ":", " "}


def _table(text: str, heading: str) -> list[list[str]]:
    """Data rows of the first table directly under *heading*."""
    assert heading in _headings(text), f"{heading}: heading not found"
    lines = [line for _, nearest, line in _headed_lines(text) if nearest == heading]
    rows = [line for line in lines if line.lstrip().startswith("|")]
    assert rows, f"{heading}: no table under this heading"
    body = [r for r in rows[1:] if not _is_separator(r)]
    return [[cell.strip() for cell in r.strip().strip("|").split("|")] for r in body]


def _code(cell: str) -> str:
    assert cell.startswith("`") and cell.endswith("`"), f"expected a code cell, got {cell!r}"
    return cell[1:-1]


def _codes(cell: str) -> list[str]:
    return cell.split("`")[1::2]


def _yaml(cell: str) -> object:
    return YAML(typ="safe").load(_code(cell))


def _plain(value: object) -> object:
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    return value


def check_attributes(text: str) -> None:
    documented: dict[str, frozenset[str] | None] = {}
    for row in _table(text, "### Match attributes"):
        values = None if "YAML booleans" in row[1] else frozenset(_codes(row[1]))
        for name in _codes(row[0]):
            documented[name] = values
    assert documented == dict(ATTRIBUTES)


def check_packaged_rules(text: str) -> None:
    documented = [
        (_code(r[0]), _yaml(r[1]), _code(r[2]), _code(r[3]), None if r[4] == "—" else _code(r[4]))
        for r in _table(text, "## Packaged rules")
    ]
    expected = [
        (p.name, _plain(p.match), p.skill, p.mode, None if p.prompt_tail is None else TAIL_NAMES[p.prompt_tail])
        for p in PACKAGED_PIPELINE
    ]
    assert documented == expected


def check_path(text: str) -> None:
    documented = [(_code(r[0]), _yaml(r[1]), _yaml(r[2])) for r in _table(text, "## Pipeline path")]
    expected = [(r.name, _plain(r.match), list(r.stages)) for r in PACKAGED_DEFINITION.path_rules]
    assert documented == expected


def check_artifacts(text: str) -> None:
    documented = [(_code(r[0]), _code(r[1]), _code(r[2]), _yaml(r[3])) for r in _table(text, "## Stage artifacts")]
    expected = [(a.stage, a.file, a.source, a.required) for a in PACKAGED_DEFINITION.artifacts.values()]
    assert documented == expected


def check_replacements(text: str) -> None:
    documented = {_code(r[0]): r[1] for r in _table(text, "## Retired `variant`")}
    assert documented == dict(VARIANT_REPLACEMENTS)


def check_variant_confined(text: str) -> None:
    stray = [line for section, _, line in _headed_lines(text) if section not in VARIANT_SECTIONS and "variant" in line]
    assert not stray, f"`variant` outside {sorted(VARIANT_SECTIONS)}: {stray}"


CHECKS: dict[str, Callable[[str], None]] = {
    "### Match attributes": check_attributes,
    "## Packaged rules": check_packaged_rules,
    "## Pipeline path": check_path,
    "## Stage artifacts": check_artifacts,
    "## Retired `variant`": check_replacements,
}


@pytest.mark.parametrize("check", [*CHECKS.values(), check_variant_confined])
def test_doc_matches_code(check: Callable[[str], None]) -> None:
    check(_doc())


def _drop_last_row(text: str, heading: str) -> str:
    lines = text.splitlines()
    start = lines.index(heading)
    first = next(i for i in range(start, len(lines)) if lines[i].startswith("|"))
    last = next(i for i in range(first, len(lines)) if not lines[i].startswith("|")) - 1
    return "\n".join(lines[:last] + lines[last + 1 :])


@pytest.mark.parametrize("heading", list(CHECKS))
def test_a_dropped_row_fails(heading: str) -> None:
    with pytest.raises(AssertionError):
        CHECKS[heading](_drop_last_row(_doc(), heading))


@pytest.mark.parametrize("heading", list(CHECKS))
def test_missing_heading_is_named(heading: str) -> None:
    doctored = _doc().replace(heading + "\n", heading + " (renamed)\n", 1)
    with pytest.raises(AssertionError, match=heading.lstrip("#").strip().replace("`", ".")):
        CHECKS[heading](doctored)


def test_separator_rows_with_colons_are_skipped() -> None:
    text = (
        "## Stage artifacts\n\n| Stage | File | Source | Required |\n"
        "| :--- | ---: | :-: | --- |\n| `a` | `b` | `c` | `true` |\n"
    )
    assert _table(text, "## Stage artifacts") == [["`a`", "`b`", "`c`", "`true`"]]


def test_fenced_hash_lines_are_not_headings() -> None:
    text = (
        "## Pipeline path\n\n```yaml\n# dispatch.yaml\n```\n\n"
        "| Name | Match | Stages |\n| --- | --- | --- |\n| `x` | `{}` | `[finish]` |\n"
    )
    assert _table(text, "## Pipeline path") == [["`x`", "`{}`", "`[finish]`"]]


def test_variant_outside_its_sections_fails() -> None:
    with pytest.raises(AssertionError, match="variant"):
        doctored = _doc().replace("## Pipeline path\n", "## Pipeline path\n\nmatch: {variant: branch}\n", 1)
        check_variant_confined(doctored)
