"""`gw util surface-page`: the check/apply verb and the registration-order walker behind it."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

import pytest
import typer
from gw_cli.cli import app
from gw_cli.introspection import TyperCommand
from gw_cli.util_cli.describe import surface_payload, walk_registered
from gw_wiki.util.surface_page import PAGE_RELPATH
from typer.testing import CliRunner

runner = CliRunner()

PAGE = (
    "---\ntype: Reference\ntitle: gw CLI surface\nupdated: 2026-01-01\n---\n\n"
    "# gw CLI surface\n\n## Summary\n\nHand prose.\n\n## Global options\n\nOLD\n\n## See also\n\n- hand link\n"
)


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str | None = PAGE) -> Path:
    root = tmp_path / ".works"
    (root / "okf").mkdir(parents=True)
    (root / "workspace.yaml").write_text("topic: t\nlayout:\n  bundle_dir: okf\n", encoding="utf-8")
    page = root / "okf" / PAGE_RELPATH
    page.parent.mkdir(parents=True)
    if text is not None:
        page.write_bytes(text.encode("utf-8"))
    monkeypatch.setenv("GRAPH_WORKS_DIR", str(root))
    return page


def _run(*args: str, columns: int | None = None):
    env = {"COLUMNS": str(columns)} if columns else None
    return runner.invoke(app, ["util", "surface-page", *args], env=env)


def test_default_reports_stale_and_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = _workspace(tmp_path, monkeypatch)

    result = _run()

    assert result.exit_code == 1
    assert "stale" in result.stdout
    assert page.read_text(encoding="utf-8") == PAGE


def test_apply_writes_then_the_check_is_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = _workspace(tmp_path, monkeypatch)

    applied = _run("--apply")
    again = _run()
    again_apply = _run("--apply")

    assert applied.exit_code == 0 and "wrote" in applied.stdout
    assert again.exit_code == 0 and "current" in again.stdout
    assert again_apply.exit_code == 0
    text = page.read_text(encoding="utf-8")
    assert "Hand prose." in text and "- hand link" in text and "OLD" not in text


def test_json_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _workspace(tmp_path, monkeypatch)

    payload = json.loads(_run("--json").stdout)

    assert set(payload) == {"path", "status", "refusal", "detail", "added", "removed", "applied"}
    assert payload["status"] == "stale" and payload["applied"] is False


@pytest.mark.parametrize("flags", [(), ("--apply",)])
def test_missing_page_and_missing_anchor_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: tuple[str, ...]
) -> None:
    _workspace(tmp_path / "a", monkeypatch, None)
    assert _run(*flags).exit_code == 1

    page = _workspace(tmp_path / "b", monkeypatch, PAGE.replace("## See also", "## Other"))
    result = _run(*flags)
    assert result.exit_code == 1 and "refused" in result.stdout
    assert page.read_text(encoding="utf-8") == PAGE.replace("## See also", "## Other")


def test_every_surface_entry_is_a_heading_once_and_width_does_not_matter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = _workspace(tmp_path, monkeypatch)
    _run("--apply", columns=60)
    narrow = page.read_text(encoding="utf-8")
    page.write_text(PAGE, encoding="utf-8")
    _run("--apply", columns=240)
    wide = page.read_text(encoding="utf-8")

    assert narrow == wide
    headings = re.findall(r"^#{3,} `(gw [^`]+)`$", wide, re.MULTILINE)
    expected = ["gw " + " ".join(entry["path"]) for entry in surface_payload()["commands"]]
    assert sorted(headings) == sorted(expected)
    assert len(headings) == len(set(headings))


def test_walk_registered_matches_the_surface_paths_in_registration_order() -> None:
    root = cast(TyperCommand, typer.main.get_command(app))

    walked = walk_registered(root)

    assert sorted(e["path"] for e in walked) == [e["path"] for e in surface_payload()["commands"]]
    top = [e["path"][0] for e in walked if len(e["path"]) == 1]
    assert top[:3] == ["help", "version", "bootstrap"]  # registration order, not alphabetical
    paths = [tuple(e["path"]) for e in walked]
    assert paths.index(("util",)) < paths.index(("util", "describe-surface")) < paths.index(("util", "line-endings"))
