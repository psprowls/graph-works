"""Repository lint inspects clone facts; wiki index, stats and tags ignore clone contents."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from graph_works_cli.cli import app
from graph_works_core.lint_drift import lint as lint_core
from helpers import initialized_workspace
from typer.testing import CliRunner

runner = CliRunner()

_PAGE = (
    "---\ntype: ReferenceRepository\ntitle: Demo\ndescription: The demo upstream.\n"
    "url: https://example.com/demo.git\n---\n\n## Summary\n\nDemo.\n"
)
_CLONE = Path("okf/repositories/demo/references/git")


@pytest.fixture(autouse=True)
def _no_semantic_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lint's semantic pass calls a live model and is nondeterministic; refuse it so only the mechanical half runs."""

    def refuse(*args: object, **kwargs: object) -> object:
        raise ValueError("no model in tests")

    monkeypatch.setattr(lint_core, "role_binding", refuse)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = initialized_workspace(tmp_path / "works")
    page = root / "okf" / "repositories" / "demo.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(_PAGE, encoding="utf-8", newline="")
    return root


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / _CLONE).rglob("*") if p.is_file()}


def _plant_clone(root: Path) -> dict[str, bytes]:
    clone = root / _CLONE
    (clone / "docs").mkdir(parents=True)
    (clone / "README.md").write_text("# Upstream\n\nNo frontmatter.\n", encoding="utf-8", newline="")
    (clone / "index.md").write_text("# upstream index\n", encoding="utf-8", newline="")
    tagged = _PAGE.replace("type:", "tags: [clone-only]\ntype:")
    (clone / "docs" / "page.md").write_text(tagged, encoding="utf-8", newline="")
    return _snapshot(root)


def _gw(root: Path, *args: str) -> str:
    result = runner.invoke(app, [*args, "--workspace", str(root)])
    assert result.exit_code in (0, 1), result.output
    return result.output


@pytest.mark.parametrize(
    "command",
    [
        ("wiki", "stats", "--json"),
        ("wiki", "tags", "inventory", "--json"),
        ("wiki", "tags", "gate"),
    ],
    ids=["stats", "tags-inventory", "tags-gate"],
)
def test_the_clone_changes_no_output(workspace: Path, command: tuple[str, ...]) -> None:
    baseline = _gw(workspace, *command)
    _plant_clone(workspace)
    assert _gw(workspace, *command) == baseline


def test_lint_reports_repository_clone_facts(workspace: Path) -> None:
    def lint_payload() -> dict[str, object]:
        payload, _ = json.JSONDecoder().raw_decode(_gw(workspace, "wiki", "lint", "--json"))
        return payload

    def repository_findings(payload: dict[str, object]) -> list[dict[str, object]]:
        return [
            finding
            for lane in payload["mechanical"]
            for finding in lane["findings"]
            if finding["code"].startswith("repository.")
        ]

    def without_repository_facts(payload: dict[str, object]) -> dict[str, object]:
        return {
            **{key: value for key, value in payload.items() if key not in {"ok", "mechanical"}},
            "mechanical": [
                {
                    **lane,
                    "findings": [
                        finding for finding in lane["findings"] if not finding["code"].startswith("repository.")
                    ],
                }
                for lane in payload["mechanical"]
            ],
        }

    before = lint_payload()
    before_repository = repository_findings(before)
    assert [(finding["code"], finding["severity"]) for finding in before_repository] == [
        ("repository.unmaterialized", "warn")
    ]

    _plant_clone(workspace)
    after = lint_payload()
    after_repository = repository_findings(after)
    assert [(finding["code"], finding["severity"]) for finding in after_repository] == [
        ("repository.url-mismatch", "error")
    ]
    assert "clone's origin" in str(after_repository[0]["message"])
    assert without_repository_facts(after) == without_repository_facts(before)


def test_index_creates_the_lane_index_and_nothing_under_the_clone(workspace: Path) -> None:
    before = _plant_clone(workspace)
    _gw(workspace, "wiki", "index")
    lane_index = workspace / "okf" / "repositories" / "index.md"
    assert lane_index.is_file()
    assert "The demo upstream." in lane_index.read_text(encoding="utf-8")
    assert _snapshot(workspace) == before


def test_the_clone_is_ignored_by_the_workspace_gitignore(workspace: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    clone_file = workspace / _CLONE / "README.md"
    clone_file.parent.mkdir(parents=True)
    clone_file.write_text("x\n", encoding="utf-8", newline="")

    def ignored(path: Path) -> bool:
        rel = path.relative_to(workspace).as_posix()
        return subprocess.run(["git", "check-ignore", "-q", rel], cwd=workspace).returncode == 0

    assert ignored(clone_file)
    assert not ignored(workspace / "okf" / "repositories" / "demo.md")
