"""Acceptance tests for `scripts/plugin_contract.py`.

Outside the repo's coverage `source` list for the same reason as its siblings:
`scripts/` is repo tooling, not a package, so these do not move the 95% gate.

`main()` carries an injected `runner` seam for exactly this — every case drives the
real `main()` with a fake `gw`, a real contract page and a real plugin tree on disk,
and asserts on the printed report plus the exit code. Nothing here stubs the module's
own parsing: the parsing *is* the thing under test.

Each case is a finding from
`work/bug-plugin-contract-drifted-from-cli`'s design stage, named in its
docstring. A checker with no test is a ledger nobody can trust.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plugin_contract  # noqa: E402
import pytest  # noqa: E402

SURFACE = {
    "schema_version": 1,
    "cli": "graph-works-cli",
    "version": "0.1.0",
    "commands": [
        {"path": ["wiki", "lint"], "options": [{"opts": ["--json"]}, {"opts": ["--workspace"]}]},
        {"path": ["work", "advance"], "options": [{"opts": ["--owner"]}]},
    ],
}


def fake_runner(surface: dict | None = SURFACE, *, version: str = "gw 0.1.0", missing: bool = False):
    """A `gw` that answers `util describe-surface` with *surface*."""

    def run(argv: list[str]) -> tuple[int, str]:
        if missing:
            raise FileNotFoundError(argv[0])
        if argv[1:] == ["version"]:
            return 0, version
        if argv[1:] == ["util", "describe-surface", "--json"]:
            if surface is None:
                return 1, ""
            return 0, json.dumps(surface)
        return 0, ""  # `gw --help`, the donor probe
    return run


def page(tmp_path: Path, *blocks: str) -> str:
    body = ["# contract"]
    for block in blocks:
        body.append(f"### {block.splitlines()[0].partition(': ')[2]}\n\n<!-- cli-contract\n{block}\n-->")
    path = tmp_path / "contract.md"
    path.write_text("\n\n".join(body), encoding="utf-8")
    return str(path)


def tree(tmp_path: Path, **files: str) -> str:
    root = tmp_path / "plugin"
    for name, text in files.items():
        target = root / name.replace("__", "/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def run_main(capsys, contract: str = "", plugin_tree: str = "", runner=None) -> tuple[int, str]:
    code = plugin_contract.main(
        ["--contract-page", contract, "--plugin-tree", plugin_tree],
        runner=runner or fake_runner(),
    )
    return code, capsys.readouterr().out


# --- identity ---------------------------------------------------------------


def test_identity_never_prints_a_bare_pass(tmp_path, capsys):
    """The binary's identity is the load-bearing half of a green result."""
    code, out = run_main(capsys, contract=page(tmp_path, "verb: wiki lint\nflags: --json"))
    assert code == 0
    assert out.splitlines()[0] == "pass (graph-works-cli)  [gw 0.1.0]"


def test_the_donor_cli_is_advisory_not_pass(tmp_path, capsys):
    """A green run must never silently mean 'agrees with the CLI E7 is replacing'."""
    code, out = run_main(capsys, runner=fake_runner(None, version="gw 0.1.1"))
    assert code == 0
    assert out.splitlines()[0] == "advisory (graph-wiki-cli)  [gw 0.1.1]"


def test_absent_gw_is_pending(capsys):
    code, out = run_main(capsys, runner=fake_runner(missing=True))
    assert code == 0
    assert "pending (gw not found)" in out


# --- A2: verbs, and the flag ledger -----------------------------------------


def test_a2_fails_on_a_verb_the_cli_does_not_have(tmp_path, capsys):
    code, out = run_main(capsys, contract=page(tmp_path, "verb: config init\nflags: (none)"))
    assert code == 1
    assert "A2 every page verb exists in the CLI: FAILED" in out
    assert "  missing verb: config init" in out


def test_a2_fails_on_an_undeclared_flag_with_no_owning_item(tmp_path, capsys):
    """The ledger's whole point: unowned drift goes red the day it appears."""
    code, out = run_main(capsys, contract=page(tmp_path, "verb: wiki lint\nflags: --json, --check"))
    assert code == 1
    assert "  undeclared flag: wiki lint --check (no owning work item)" in out


def test_a2_advises_and_stays_green_for_a_flag_the_ledger_owns(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(plugin_contract.FLAG_ADVISORIES, ("wiki lint", "--check"), "some-item")
    code, out = run_main(capsys, contract=page(tmp_path, "verb: wiki lint\nflags: --json, --check"))
    assert code == 0
    assert "  advisory: wiki lint --check (deferred to some-item)" in out


def test_a2_skips_deferred_verbs_entirely(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(plugin_contract.DEFERRED, "guidance suggest", "guidance-okf-port")
    code, out = run_main(capsys, contract=page(tmp_path, "verb: guidance suggest\nflags: --role"))
    assert code == 0
    assert "  1 deferred (guidance-okf-port): guidance suggest" in out
    assert "--role" not in out  # the deferral hides its flag gaps too


def test_the_prose_example_blocks_are_not_verbs(tmp_path, capsys):
    """`## How to read an entry`'s examples sit under a `##`, so they must not count."""
    path = tmp_path / "contract.md"
    path.write_text(
        "## How to read an entry\n\n<!-- cli-contract\nverb: work example-verb\nflags: --owner\n-->\n",
        encoding="utf-8",
    )
    code, out = run_main(capsys, contract=str(path))
    assert code == 0
    assert "  0/0 verbs resolve" in out


def test_an_unreadable_page_is_pending_not_a_failure(tmp_path, capsys):
    code, out = run_main(capsys, contract=str(tmp_path / "absent.md"))
    assert code == 0
    assert "pending (unreadable:" in out


def test_a2_is_skipped_when_only_the_donor_answers(tmp_path, capsys):
    """Nothing authoritative to fail against, so A2 stays informational."""
    code, out = run_main(
        capsys,
        contract=page(tmp_path, "verb: wiki lint\nflags: --json"),
        runner=fake_runner(None),
    )
    assert code == 0
    assert "A2 every page verb exists in the CLI: skipped (no graph-works-cli surface)" in out


# --- A1: the tree sweep -----------------------------------------------------


def test_a1_prints_its_label_line_when_it_has_findings(tmp_path, capsys):
    """Finding D — a run *with* findings used to lose the heading entirely."""
    code, out = run_main(
        capsys,
        contract=page(tmp_path, "verb: wiki lint\nflags: --json"),
        plugin_tree=tree(tmp_path, **{"a.md": "run `gw teleport now` first"}),
    )
    assert code == 0
    lines = out.splitlines()
    label = lines.index("A1 every plugin invocation is on the page: advisory")
    assert lines[label + 1] == "  advisory: invocation not on the page: gw teleport"


def test_a1_ignores_prose_that_is_not_written_as_code(tmp_path, capsys):
    """Finding E — 'a mutation made outside the gw commands' is a sentence, not a call."""
    _, out = run_main(
        capsys,
        contract=page(tmp_path, "verb: wiki lint\nflags: --json"),
        plugin_tree=tree(tmp_path, **{"a.md": "a mutation made outside the gw commands."}),
    )
    assert "A1 every plugin invocation is on the page: ok" in out


def test_a1_does_not_let_a_match_cross_a_newline(tmp_path, capsys):
    """Finding E — a paragraph ending in 'via gw' swallowed the next line's first word."""
    _, out = run_main(
        capsys,
        contract=page(tmp_path, "verb: wiki lint\nflags: --json"),
        plugin_tree=tree(tmp_path, **{"a.md": "```\nresolved via gw\nclaude --help\n```"}),
    )
    assert "gw claude" not in out


def test_a1_treats_a_prefix_of_a_known_verb_as_known(tmp_path, capsys):
    """Bare `gw work` is the group `gw work advance` belongs to, not a missing entry."""
    _, out = run_main(
        capsys,
        contract=page(tmp_path, "verb: work advance\nflags: --owner"),
        plugin_tree=tree(tmp_path, **{"a.md": "`gw work` owns the lane"}),
    )
    assert "A1 every plugin invocation is on the page: ok" in out


def test_a1_honours_a_named_tree_mention(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(plugin_contract.TREE_MENTIONS, "t.sh", "asserts the verb's absence")
    _, out = run_main(
        capsys,
        contract=page(tmp_path, "verb: wiki lint\nflags: --json"),
        plugin_tree=tree(tmp_path, **{"t.sh": 'for f in "gw teleport"; do :; done'}),
    )
    assert "A1 every plugin invocation is on the page: ok" in out


def test_a1_scans_every_line_of_a_non_markdown_file(tmp_path, capsys):
    """A shell script has no fences to sit inside; the whole file is code."""
    _, out = run_main(
        capsys,
        contract=page(tmp_path, "verb: wiki lint\nflags: --json"),
        plugin_tree=tree(tmp_path, **{"t.sh": "gw teleport now"}),
    )
    assert "  advisory: invocation not on the page: gw teleport" in out


def test_a1_and_a3_skip_rather_than_pass_vacuously(tmp_path, capsys):
    """The plugin tree lives in another repository; absence is not agreement."""
    _, out = run_main(capsys, contract=page(tmp_path, "verb: wiki lint\nflags: --json"))
    assert "A1 every plugin invocation is on the page: skipped (no plugin tree)" in out
    assert "A3 no stale identifiers or script references: skipped (no plugin tree)" in out


# --- A3: the port's own invariant -------------------------------------------


def test_a3_fails_on_the_stale_identifier(tmp_path, capsys):
    code, out = run_main(
        capsys,
        contract=page(tmp_path, "verb: wiki lint\nflags: --json"),
        plugin_tree=tree(tmp_path, **{"a.md": "the graph-wiki CLI"}),
    )
    assert code == 1
    assert "  stale identifier graph-wiki: a.md" in out


def test_a3_fails_on_a_resurrected_python_shim(tmp_path, capsys):
    code, out = run_main(
        capsys,
        contract=page(tmp_path, "verb: wiki lint\nflags: --json"),
        plugin_tree=tree(tmp_path, **{"skills__graph-works__scripts__lint_wiki.py": "print(1)"}),
    )
    assert code == 1
    assert "  script reference: skills/graph-works/scripts/lint_wiki.py" in out


def test_a3_survives_a_file_it_cannot_decode(tmp_path, capsys):
    root = Path(tree(tmp_path, **{"a.md": "fine"}))
    (root / "logo.bin").write_bytes(b"\xff\xfe\x00binary")
    code, out = run_main(
        capsys,
        contract=page(tmp_path, "verb: wiki lint\nflags: --json"),
        plugin_tree=str(root),
    )
    assert code == 0
    assert "A3 no stale identifiers or script references: ok" in out


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
