"""The acceptance gate: the whole CLI, in the order a human drives it.

The epic's "prove the package works end to end" made mechanical. It is the only
test that exercises `compose.py` through the CLI rather than by calling it, and
the only one that runs every command in one vault.

**Zero errors, not zero warnings** (C6-L). `Report.ok` is error-only by design
(ADR-0008), and that is exactly the property being leaned on: the gate is a
claim about conformance, not about tidiness. The warnings that survive are
named below rather than asserted away.
"""

import json
from pathlib import Path

from typer.testing import CliRunner
from work_tracker_okf.cli import app

runner = CliRunner()

TODAY = "2026-08-11"
SLUG = "2026-08-11-tech-debt-compose-the-cli"
REFERENCES = f"work/{SLUG}/references"


def _run(*args: str) -> None:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, f"{args}\n{result.output}\n{result.exception!r}"


def test_the_whole_surface_ends_at_zero_errors(tmp_path: Path) -> None:
    root = str(tmp_path)

    # init
    _run("init", root, "--today", TODAY)
    assert (tmp_path / "_schema" / "_base.schema.json").is_file()

    # file
    _run(
        "file",
        root,
        "--type",
        "TechDebt",
        "--title",
        "Compose the CLI",
        "--description",
        "Wire the Typer subcommands over a composition layer.",
        "--affects",
        "packages/work-tracker-okf",
        "--tags",
        "port",
        "--today",
        TODAY,
    )
    page = tmp_path / "work" / f"{SLUG}.md"
    assert page.is_file()

    # next -- a filed item enters at design
    result = runner.invoke(app, ["next", root, SLUG, "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["dispatch"] == {"stage": "design", "variant": "exploration"}

    # advance 1: no phase -> design
    _run("advance", root, SLUG, "--today", TODAY)

    # the design stage writes its artifact; the stamp reads its H1
    artifact = tmp_path / REFERENCES / "01-design-spec.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# Design spec — compose the CLI\n\nThe design.\n", encoding="utf-8")

    # advance 2: design -> execute (bug-like, small effort skips planning)
    _run("advance", root, SLUG, "--today", TODAY, "--effort", "small")
    text = page.read_text(encoding="utf-8")
    assert "id: design-spec" in text
    assert f"/{REFERENCES}/01-design-spec.md" in text
    assert "title: Design spec — compose the CLI" in text

    # advance 3: dispatch execute -> in-progress
    _run("advance", root, SLUG, "--today", TODAY, "--owner", "fixture-author")
    # advance 4: execute -> finish
    _run("advance", root, SLUG, "--today", TODAY)
    # advance 5: finish -> done, resolved
    _run("advance", root, SLUG, "--today", TODAY, "--resolved-in", "abc1234")
    text = page.read_text(encoding="utf-8")
    assert "phase: done" in text
    assert "workflow_status: resolved" in text

    # lint, before the sweep
    result = runner.invoke(app, ["lint", root, "--today", TODAY, "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["ok"] is True

    # archive
    _run("archive", root, "--today", TODAY)
    archived = tmp_path / "work" / "_archive" / f"{SLUG}.md"
    assert archived.is_file()
    assert not page.exists()
    assert not (tmp_path / "work" / SLUG).exists()
    # `moves` rewrites `sources.<n>.resource` in frontmatter, so the stamp
    # written before the move still resolves after it.
    assert f"/work/_archive/{SLUG}/references/01-design-spec.md" in archived.read_text(encoding="utf-8")

    # status
    result = runner.invoke(app, ["status", root, "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["total"] == 0

    # the gate
    result = runner.invoke(app, ["lint", root, "--today", TODAY, "--json"])
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert [finding for finding in report["findings"] if finding["severity"] == "error"] == []


def test_the_surviving_warnings_are_named(tmp_path: Path) -> None:
    """§6.2: the warnings that stand are counted, not papered over. If this set
    changes, read the vault before loosening the assertion -- a new warning is a
    fact about the lane, and a silent one is how a gate rots."""
    root = str(tmp_path)
    _run("init", root, "--today", TODAY)
    _run(
        "file",
        root,
        "--type",
        "TechDebt",
        "--title",
        "Compose the CLI",
        "--description",
        "Wire the Typer subcommands over a composition layer.",
        "--affects",
        "packages/work-tracker-okf",
        "--today",
        TODAY,
    )
    _run("advance", root, SLUG, "--today", TODAY)
    _run("advance", root, SLUG, "--today", TODAY, "--effort", "small")

    result = runner.invoke(app, ["lint", root, "--today", TODAY, "--json"])
    assert result.exit_code == 0
    codes = sorted({finding["code"] for finding in json.loads(result.stdout)["findings"]})
    # The stamp is unconditional (C6-G): the spec has not been written, so the
    # pointer dangles, and `provenance.source-uncited` fires because a work
    # item's `sources[]` are attachments rather than citations -- the tier-2
    # question §9 leaves open.
    assert codes == ["provenance.source-uncited", "targets.artifact-missing"]
