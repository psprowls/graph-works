import shlex
from pathlib import Path

import pytest
from plugin_fork_io.cli import app
from plugin_fork_io.validation import parse_skill_metadata
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("name", ["fork-skills", "review-fork-update"])
def test_companion_metadata_and_cli_examples(name):
    path = ROOT / "plugins/plugin-fork/skills" / name / "SKILL.md"
    metadata, findings = parse_skill_metadata(path.read_bytes(), path=str(path))
    assert metadata and metadata.name == name and not findings
    examples = [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("plugin-fork ")]
    assert examples
    for example in examples:
        argv = shlex.split(example)
        command = argv[1]
        help_result = CliRunner().invoke(app, [command, "--help"], color=False, terminal_width=160)
        assert help_result.exit_code == 0
        for token in argv[2:]:
            if token.startswith("--"):
                assert token in help_result.stdout, example


@pytest.mark.parametrize("review_kind", ["none", "clean", "findings"])
def test_documented_review_states_match_cli_acceptance(tmp_path, review_kind):
    import json
    import re

    from test_acceptance import prepared

    roots, variant, update = prepared(tmp_path)
    candidate = update.data["candidate_path"]
    runner = CliRunner()
    inspected = runner.invoke(app, ["inspect", candidate, "--json"])
    assert inspected.exit_code == 0
    digest = json.loads(inspected.stdout)["data"]["digest"]
    args = ["accept", variant, "--candidate", update.preview_id, "--state-dir", str(roots.state), "--json"]
    if review_kind != "none":
        review_file = tmp_path / "review.json"
        findings = (
            []
            if review_kind == "clean"
            else [
                {
                    "code": "behavior.reconciled",
                    "severity": "warn",
                    "path": None,
                    "line": None,
                    "message": "Original finding retained after reconciliation",
                }
            ]
        )
        review_file.write_bytes(
            json.dumps(
                {"candidate_digest": digest, "findings": findings, "resolutions": [], "evidence_path": None}
            ).encode()
        )
        args += ["--review", str(review_file)]
    response = runner.invoke(app, args)
    assert response.exit_code == 0, response.stdout
    state = json.loads(response.stdout)["data"]["review_state"]
    for relative in ("plugins/plugin-fork/skills/review-fork-update/SKILL.md", "packages/plugin-fork-io/README.md"):
        documented = set(
            re.findall(r"`(not_requested|completed_[a-z_]+)`", (ROOT / relative).read_text(encoding="utf-8"))
        )
        assert state in documented
