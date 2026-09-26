"""Frozen C4 wiki command names, option names, and shipped documentation."""

from __future__ import annotations

import json
from pathlib import Path

from graph_works_cli.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def _help(*command_path: str) -> dict[str, object]:
    """Load an authoritative help payload through the public JSON helper."""
    result = runner.invoke(app, ["help", "--json", *command_path])
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


def _command_names(payload: dict[str, object]) -> list[str]:
    """Extract the public command ordering from one help payload."""
    return [str(entry["name"]) for entry in payload["commands"]]  # type: ignore[index]


def _option_names(payload: dict[str, object]) -> set[str]:
    """Extract every public long/short option spelling from one help payload."""
    return {
        option
        for entry in payload["options"]  # type: ignore[index]
        for option in [*entry["opts"], *entry["secondary_opts"]]  # type: ignore[index]
    }


def _section(readme: str, heading: str) -> str:
    """Return one level-2 README section, subsections included, heading to heading."""
    body = readme.split(f"\n{heading}\n", 1)[1]
    return body.split("\n## ", 1)[0]


def test_help_json_freezes_the_existing_root_and_complete_c4_wiki_surface() -> None:
    """A command or flag drift would break callers that rely on the C4 contract."""
    root = _help()
    assert _command_names(root) == [
        "help",
        "version",
        "bootstrap",
        "scan",
        "ingest",
        "query",
        "archive",
        "next",
        "config",
        "agent-config",
        "graph",
        "wiki",
        "work",
        "util",
    ]
    assert _option_names(root) == {"--verbose", "-v", "--install-completion", "--show-completion"}

    expected_options = {
        ("bootstrap",): {"--topic", "--workspace", "--repo-root", "--dry-run", "--json"},
        ("scan",): {
            "--no-narrate",
            "--emit-worklist",
            "--apply",
            "--results-dir",
            "--short-head",
            "--json",
            "--workspace",
        },
        ("ingest",): {"--source", "--json", "--workspace", "--backend"},
        ("query",): {"--query", "--limit", "--backend", "--json", "--workspace"},
        ("archive",): {"--dry-run", "--json", "--workspace"},
        ("wiki", "lint"): {"--json", "--workspace"},
        ("wiki", "drift"): {
            "--backend",
            "--only",
            "--dry-run",
            "--no-dry-run",
            "--json",
            "--workspace",
            "--repo-name",
        },
        ("wiki", "stats"): {"--top", "--json", "--workspace"},
        ("wiki", "index"): {"--workspace"},
        ("wiki", "archive"): {"--dry-run", "--json", "--workspace"},
        ("wiki", "tags", "inventory"): {"--json", "--workspace"},
        ("wiki", "tags", "draft"): {"--as-of", "--floor", "--ceiling", "--workspace"},
        ("wiki", "tags", "apply"): {"--only", "--dry-run", "--workspace"},
        ("wiki", "tags", "gate"): {"--json", "--workspace"},
        ("wiki", "proposals"): {"--json", "--workspace"},
        ("wiki", "proposal", "file"): {
            "--lane",
            "--title",
            "--description",
            "--id",
            "--resource",
            "--rationale",
            "--evidence",
            "--dry-run",
            "--json",
            "--workspace",
        },
        ("wiki", "proposal", "approve"): {"--dry-run", "--json", "--workspace"},
        ("wiki", "proposal", "reject"): {"--dry-run", "--json", "--workspace"},
        ("wiki", "claims", "refresh"): {"--force", "--json", "--workspace"},
        ("wiki", "claims", "show"): {"--include-superseded", "--json", "--workspace"},
        ("wiki", "claims", "closure"): {"--include-superseded", "--json", "--workspace"},
    }
    for command_path, expected in expected_options.items():
        assert _option_names(_help(*command_path)) == expected

    wiki = _help("wiki")
    proposal = _help("wiki", "proposal")
    # typer.main.get_group_from_info always lists every plain `registered_command` before
    # every `registered_group` (sub-Typer) at a given level, regardless of source order —
    # the same reason `proposal` (a group) already sorts after `proposals` (a command).
    # `tags` is a group (four subcommands), so it lands after every plain wiki command too.
    assert _command_names(wiki) == [
        "lint",
        "drift",
        "stats",
        "index",
        "archive",
        "proposals",
        "tags",
        "proposal",
        "claims",
    ]
    assert _command_names(proposal) == ["file", "approve", "reject"]
    assert _option_names(wiki) == set()
    assert _option_names(proposal) == set()
    assert _command_names(_help("wiki", "tags")) == ["inventory", "draft", "apply", "gate"]
    assert _option_names(_help("wiki", "tags")) == set()
    assert _command_names(_help("wiki", "claims")) == ["refresh", "show", "closure"]
    assert _option_names(_help("wiki", "claims")) == set()

    assert not {"--tool", "--force"} & _option_names(_help("bootstrap"))
    assert not {"--limit", "--all"} & _option_names(_help("ingest"))
    assert not {"--stale-days", "--log-gap-days", "--check"} & _option_names(_help("wiki", "lint"))
    assert not {"--model"} & _option_names(_help("query"))
    for command_path in (("wiki", "proposal", "file"), ("wiki", "proposal", "approve"), ("wiki", "proposal", "reject")):
        assert {"--dry-run", "--json"} <= _option_names(_help(*command_path))
    assert not {"show", "promote"} & set(_command_names(proposal))


def test_readme_documents_the_shipped_c4_surface_without_future_commands() -> None:
    """The package README is the supported human entry point for C4 users."""
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    for text in (
        "## Wiki command surface",
        "gw bootstrap",
        "gw scan",
        "gw ingest",
        "gw query",
        "gw wiki lint",
        "gw wiki stats",
        "gw wiki index",
        "gw wiki archive",
        "gw wiki tags inventory",
        "gw wiki tags draft",
        "gw wiki tags apply",
        "gw wiki tags gate",
        "gw wiki proposals",
        "gw wiki proposal file",
        "gw wiki proposal approve",
        "gw wiki proposal reject",
        "gw wiki claims refresh",
        "gw wiki claims show",
        "gw wiki claims closure",
        "gw scan --emit-worklist",
        "gw scan --apply",
        "`gw bootstrap --dry-run`",
        "`gw wiki archive --dry-run`",
        "| 0 |",
        "| 1 |",
        "| 2 |",
        "| 3 |",
        "| 4 |",
        "| 5 |",
    ):
        assert text in readme
    assert "gw util describe-surface" not in _section(readme, "## Wiki command surface")
