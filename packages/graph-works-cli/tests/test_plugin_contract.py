"""`scripts/plugin_contract.py` — the three assertions and the three-state identity (D-034).

Driven entirely through the injected runner, so nothing here depends on a `gw` binary
being installed, let alone on *which* `gw` is installed — which is exactly the confusion
the three-state identity exists to remove.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import plugin_contract

PAGE = """\
## How to read an entry

<!-- cli-contract
verb: work example-verb
flags: --owner
-->

## The verbs

### archive

<!-- cli-contract
verb: archive
flags: --dry-run, --workspace
-->

### next

<!-- cli-contract
verb: next
flags: --json, --descend, --file
-->

### guidance suggest

<!-- cli-contract
verb: guidance suggest
flags: --role
-->
"""

SURFACE = {
    "schema_version": 1,
    "cli": "graph-works-cli",
    "version": "0.1.0",
    "commands": [
        {"path": ["archive"], "options": [{"opts": ["--dry-run"]}, {"opts": ["--workspace"]}], "commands": []},
        {"path": ["next"], "options": [{"opts": ["--json"]}, {"opts": ["--descend"]}], "commands": []},
    ],
}


def _runner(*, surface: object | None, help_ok: bool = True, version: str = "gw 0.1.0", found: bool = True):
    """Stand in for subprocess: (argv) -> (returncode, stdout)."""

    def run(argv: list[str]) -> tuple[int, str]:
        if not found:
            raise FileNotFoundError(argv[0])
        if argv[1:3] == ["util", "describe-surface"]:
            return (0, json.dumps(surface)) if surface is not None else (2, "")
        if argv[1:2] == ["version"]:
            return (0, version)
        if "--help" in argv:
            return (0, "Usage: gw ...") if help_ok else (2, "")
        return (2, "")

    return run


@pytest.fixture
def page(tmp_path: Path) -> Path:
    target = tmp_path / "contract.md"
    target.write_text(PAGE, encoding="utf-8")
    return target


def test_the_parser_ignores_the_documentation_examples(page: Path) -> None:
    """`work example-verb` is prose, not a requirement; counting it would fail forever."""
    verbs = plugin_contract.parse_contract_page(page.read_text(encoding="utf-8"))

    assert [entry.verb for entry in verbs] == ["archive", "next", "guidance suggest"]


def test_identity_is_pass_named_when_describe_surface_answers(page: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = plugin_contract.main(["--contract-page", str(page)], runner=_runner(surface=SURFACE))

    out = capsys.readouterr().out
    assert code == 0
    assert "pass (graph-works-cli)" in out
    assert "gw 0.1.0" in out


def test_identity_falls_back_to_advisory_for_the_donor_cli(page: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A green result must never silently mean 'agrees with the CLI E7 is replacing'."""
    code = plugin_contract.main(["--contract-page", str(page)], runner=_runner(surface=None))

    out = capsys.readouterr().out
    assert code == 0
    assert "advisory (graph-wiki-cli)" in out
    assert "pass (graph-works-cli)" not in out


def test_identity_is_pending_when_gw_is_absent(page: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = plugin_contract.main(["--contract-page", str(page)], runner=_runner(surface=None, found=False))

    out = capsys.readouterr().out
    assert code == 0
    assert "pending (gw not found)" in out


def test_a_missing_verb_fails_and_a_missing_flag_is_only_advisory(
    page: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    surface = {**SURFACE, "commands": [SURFACE["commands"][1]]}  # drops `archive` entirely
    code = plugin_contract.main(["--contract-page", str(page)], runner=_runner(surface=surface))

    out = capsys.readouterr().out
    assert code != 0
    assert "missing verb: archive" in out
    assert "advisory: next --file" in out


def test_the_deferred_verb_is_named_and_not_counted_as_resolved(page: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = plugin_contract.main(["--contract-page", str(page)], runner=_runner(surface=SURFACE))

    out = capsys.readouterr().out
    assert code == 0
    assert "2/3 verbs resolve" in out
    assert "1 deferred (guidance-okf-port): guidance suggest" in out


def test_a1_and_a3_report_skipped_without_a_plugin_tree(page: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = plugin_contract.main(["--contract-page", str(page)], runner=_runner(surface=SURFACE))

    out = capsys.readouterr().out
    assert code == 0
    assert out.count("skipped (no plugin tree)") == 2


def test_a1_passes_when_a_tree_invocation_matches_a_known_verb(
    page: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = tmp_path / "plugin"
    (tree / "skills" / "demo").mkdir(parents=True)
    (tree / "skills" / "demo" / "SKILL.md").write_text("Run `gw archive --dry-run` to sweep.\n", encoding="utf-8")

    code = plugin_contract.main(
        ["--contract-page", str(page), "--plugin-tree", str(tree)], runner=_runner(surface=SURFACE)
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "A1 every plugin invocation is on the page: ok" in out


def test_a1_advises_on_an_unrecognized_invocation_without_failing(
    page: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = tmp_path / "plugin"
    (tree / "skills" / "demo").mkdir(parents=True)
    (tree / "skills" / "demo" / "SKILL.md").write_text("Run `gw bogus-verb --now` first.\n", encoding="utf-8")

    code = plugin_contract.main(
        ["--contract-page", str(page), "--plugin-tree", str(tree)], runner=_runner(surface=SURFACE)
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "gw bogus-verb" in out


def test_a3_fails_on_a_stale_identifier_or_a_script_reference(
    page: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = tmp_path / "plugin"
    (tree / "skills" / "demo" / "scripts").mkdir(parents=True)
    (tree / "skills" / "demo" / "SKILL.md").write_text("run graph-wiki lint\n", encoding="utf-8")
    (tree / "skills" / "demo" / "scripts" / "old.py").write_text("print()\n", encoding="utf-8")

    code = plugin_contract.main(
        ["--contract-page", str(page), "--plugin-tree", str(tree)], runner=_runner(surface=SURFACE)
    )

    out = capsys.readouterr().out
    assert code != 0
    assert "graph-wiki" in out
    assert "old.py" in out


def test_an_unreadable_contract_page_is_pending(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = plugin_contract.main(["--contract-page", str(tmp_path / "gone.md")], runner=_runner(surface=SURFACE))

    out = capsys.readouterr().out
    assert code == 0
    assert "pending" in out
