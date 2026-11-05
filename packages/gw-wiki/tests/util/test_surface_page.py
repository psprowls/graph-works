"""`render_sections` and the page reconcile behind `gw util surface-page`."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from gw_kernel.workspace.layout import layout_for
from gw_wiki.util.surface_page import (
    PAGE_RELPATH,
    apply_surface_page,
    plan_surface_page,
    render_sections,
)


def _opt(opts, *, secondary=(), help="", required=False, default=None, is_flag=False):
    return {
        "name": opts[0].lstrip("-"),
        "opts": list(opts),
        "secondary_opts": list(secondary),
        "help": help,
        "required": required,
        "default": default,
        "is_flag": is_flag,
    }


def _entry(path, *, help="", usage=("[OPTIONS]",), arguments=(), options=(), commands=()):
    return {
        "name": "gw " + " ".join(path),
        "path": list(path),
        "help": help,
        "short_help": help,
        "usage": list(usage),
        "arguments": list(arguments),
        "options": list(options),
        "commands": list(commands),
    }


ROOT = {
    "options": [
        _opt(["--verbose", "-v"], help="Stream a log.", default=0),
        _opt(["--install-completion"], help="Typer's own."),
        _opt(["--show-completion"], help="Typer's own."),
    ],
    "commands": [{"name": "grp", "help": "A group."}, {"name": "leaf", "help": "A leaf."}],
}

COMMANDS = [
    _entry(
        ["grp"],
        help="A group\nwrapped over lines.\n\n- item one\n- item two\n\nTrailing prose\nalso wrapped.",
        commands=["sub"],
    ),
    _entry(
        ["grp", "sub"],
        help="Sub help.",
        usage=["[OPTIONS]", "NAME"],
        arguments=[{"name": "name", "required": True, "nargs": 1}],
        options=[
            _opt(["--json"], help="Emit JSON.", default=False, is_flag=True),
            _opt(["--color"], secondary=["--no-color"], default=True, is_flag=True),
            _opt(["--tag"], help="a | b\nc", default=["x", "y"]),
            _opt(["--empty"], default=[]),
            _opt(["--name"], required=True, default=""),
            _opt(["--out", "-o"], default="here"),
        ],
    ),
    _entry(["leaf"], help="A leaf."),
]


def test_render_pins_the_format() -> None:
    text = render_sections(ROOT, COMMANDS, "9.9.9")

    assert text == (
        "## Global options\n\n"
        "| Option | Default | Description |\n|---|---|---|\n"
        "| `--verbose` / `-v` | `0` | Stream a log. |\n\n"
        "## Top-level commands\n\n- `gw grp` — A group.\n- `gw leaf` — A leaf.\n\n"
        "## Command reference\n\nGenerated from gw 9.9.9.\n\n"
        "### `gw grp`\n\n"
        "A group wrapped over lines.\n\n- item one\n- item two\n\nTrailing prose also wrapped.\n\n"
        "Usage: `gw grp [OPTIONS]`\n\nSubcommands: `sub`\n\n"
        "#### `gw grp sub`\n\nSub help.\n\nUsage: `gw grp sub [OPTIONS] NAME`\n\n"
        "| Argument | Required | Variadic |\n|---|---|---|\n| `name` | yes | no |\n\n"
        "| Option | Required | Default | Description |\n|---|---|---|---|\n"
        "| `--json` (flag) |  | off | Emit JSON. |\n"
        "| `--color` / `--no-color` (flag) |  | `True` |  |\n"
        '| `--tag` |  | `["x", "y"]` | a \\| b c |\n'
        "| `--empty` |  |  |  |\n"
        "| `--name` | yes |  |  |\n"
        "| `--out` / `-o` |  | `here` |  |\n\n"
        "### `gw leaf`\n\nA leaf.\n\nUsage: `gw leaf [OPTIONS]`\n"
    )


PAGE = (
    "---\ntype: Reference\ntitle: gw CLI surface\nupdated: 2026-01-01\n---\n\n"
    "# gw CLI surface\n\n## Summary\n\nHand prose.\n\n## Global options\n\nOLD\n\n## See also\n\n- hand link\n"
)


def _layout(tmp_path: Path, text: str | None = PAGE):
    layout = layout_for(tmp_path / ".works")
    page = layout.bundle_dir / PAGE_RELPATH
    page.parent.mkdir(parents=True)
    if text is not None:
        page.write_bytes(text.encode("utf-8"))
    return layout, page


def test_hand_owned_prose_survives_and_updated_moves(tmp_path: Path) -> None:
    layout, page = _layout(tmp_path)
    sections = render_sections(ROOT, COMMANDS, "1.0.0")

    plan = plan_surface_page(layout, sections, today=date(2026, 11, 5))

    assert plan.status == "stale" and plan.added > 0 and plan.removed > 0
    assert page.read_text(encoding="utf-8") == PAGE  # planning writes nothing
    assert apply_surface_page(plan) is True
    after = page.read_text(encoding="utf-8")
    assert after.startswith("---\ntype: Reference\ntitle: gw CLI surface\nupdated: 2026-11-05\n---\n")
    assert "## Summary\n\nHand prose.\n\n## Global options" in after
    assert after.endswith("\n\n## See also\n\n- hand link\n")
    assert "OLD" not in after and "Generated from gw 1.0.0." in after


def test_second_apply_changes_nothing(tmp_path: Path) -> None:
    layout, page = _layout(tmp_path)
    sections = render_sections(ROOT, COMMANDS, "1.0.0")
    apply_surface_page(plan_surface_page(layout, sections, today=date(2026, 11, 5)))
    written = page.read_bytes()

    again = plan_surface_page(layout, sections, today=date(2027, 1, 1))

    assert again.status == "current" and not again.changed
    assert apply_surface_page(again) is False
    assert page.read_bytes() == written


def test_crlf_page_stays_crlf(tmp_path: Path) -> None:
    layout, page = _layout(tmp_path, PAGE.replace("\n", "\r\n"))

    plan = plan_surface_page(layout, render_sections(ROOT, COMMANDS, "1.0.0"), today=date(2026, 11, 5))
    apply_surface_page(plan)

    raw = page.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n")
    assert b"Hand prose.\r\n" in raw


def test_missing_page_and_bad_anchors_refuse_and_write_nothing(tmp_path: Path) -> None:
    sections = render_sections(ROOT, COMMANDS, "1.0.0")
    layout, _ = _layout(tmp_path / "a", None)
    assert plan_surface_page(layout, sections, today=date(2026, 1, 1)).refusal == "missing-page"

    cases = {
        "missing-anchor": PAGE.replace("## See also", "## Other"),
        "duplicate-anchor": PAGE + "\n## Global options\n",
        "misordered-anchors": PAGE.replace("## Global options", "## Tmp")
        .replace("## See also", "## Global options")
        .replace("## Tmp", "## See also"),
    }
    for index, (reason, text) in enumerate(cases.items()):
        layout, page = _layout(tmp_path / str(index), text)
        plan = plan_surface_page(layout, sections, today=date(2026, 1, 1))
        assert plan.status == "refused" and plan.refusal == reason, reason
        assert apply_surface_page(plan) is False
        assert page.read_bytes() == text.encode("utf-8")
