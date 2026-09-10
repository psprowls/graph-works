"""`gw wiki tags` — inventory, draft, apply, gate."""

from __future__ import annotations

import json

from graph_works_cli.cli import app
from typer.testing import CliRunner

runner = CliRunner()

DISPOSITION = """\
generated: 2026-09-08
tagged_pages: 2
keep:
  - {tag: lint, uses: 2, reason: survivor}
merge:
  - {tag: wiki-lint, uses: 1, reason: semantic, into: lint}
strip:
  - {tag: singleton, uses: 1, reason: below-floor}
"""


def seed(workspace, pages, vocabulary=None):
    """Write concept pages under the workspace bundle, plus an optional
    `.gw/tags.yaml`. Returns the workspace path."""
    for concept_id, tags in pages.items():
        target = workspace / "okf" / f"{concept_id}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        rendered = "".join(f"- {tag}\n" for tag in tags)
        target.write_text(
            f"---\ntype: Reference\ntitle: {concept_id}\ntags:\n{rendered}---\n\n# {concept_id}\n",
            encoding="utf-8",
            newline="",
        )
    if vocabulary is not None:
        (workspace / ".gw").mkdir(parents=True, exist_ok=True)
        (workspace / ".gw" / "tags.yaml").write_text(vocabulary, encoding="utf-8", newline="")
    return workspace


def test_inventory_json_reports_counts(workspace):
    seed(workspace, {"a": ["lint", "singleton"], "b": ["lint"]})
    result = runner.invoke(app, ["wiki", "tags", "inventory", "--json", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["counts"]["lint"] == 2
    assert payload["tagged_pages"] == 2


def test_inventory_human_output_is_counts_descending(workspace):
    seed(workspace, {"a": ["lint", "singleton"], "b": ["lint"]})
    result = runner.invoke(app, ["wiki", "tags", "inventory", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.stdout
    assert result.stdout.index("lint") < result.stdout.index("singleton")


def test_draft_writes_a_loadable_disposition(workspace, tmp_path):
    seed(workspace, {"a": ["lint", "singleton"], "b": ["lint"]})
    out = tmp_path / "disposition.yaml"
    result = runner.invoke(
        app,
        ["wiki", "tags", "draft", str(out), "--as-of", "2026-09-08", "--workspace", str(workspace)],
    )
    assert result.exit_code == 0, result.stdout
    text = out.read_text(encoding="utf-8")
    assert "generated: 2026-09-08" in text
    assert "singleton" in text


def test_draft_requires_as_of(workspace):
    seed(workspace, {"a": ["lint"]})
    result = runner.invoke(app, ["wiki", "tags", "draft", "out.yaml", "--workspace", str(workspace)])
    assert result.exit_code != 0


def test_apply_dry_run_writes_nothing(workspace, tmp_path):
    seed(workspace, {"a": ["lint", "wiki-lint"], "b": ["lint", "singleton"]})
    before = (workspace / "okf" / "a.md").read_bytes()
    disposition = tmp_path / "d.yaml"
    disposition.write_text(DISPOSITION, encoding="utf-8", newline="")
    result = runner.invoke(app, ["wiki", "tags", "apply", str(disposition), "--dry-run", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.stdout
    assert (workspace / "okf" / "a.md").read_bytes() == before


def test_apply_only_merge_leaves_the_strips_alone(workspace, tmp_path):
    seed(workspace, {"a": ["lint", "wiki-lint"], "b": ["lint", "singleton"]})
    disposition = tmp_path / "d.yaml"
    disposition.write_text(DISPOSITION, encoding="utf-8", newline="")
    result = runner.invoke(
        app, ["wiki", "tags", "apply", str(disposition), "--only", "merge", "--workspace", str(workspace)]
    )
    assert result.exit_code == 0, result.stdout
    assert "wiki-lint" not in (workspace / "okf" / "a.md").read_text(encoding="utf-8")
    assert "singleton" in (workspace / "okf" / "b.md").read_text(encoding="utf-8")


def test_apply_runs_both_phases_in_order_by_default(workspace, tmp_path):
    seed(workspace, {"a": ["lint", "wiki-lint"], "b": ["lint", "singleton"]})
    disposition = tmp_path / "d.yaml"
    disposition.write_text(DISPOSITION, encoding="utf-8", newline="")
    result = runner.invoke(app, ["wiki", "tags", "apply", str(disposition), "--workspace", str(workspace)])
    assert result.exit_code == 0, result.stdout
    assert "wiki-lint" not in (workspace / "okf" / "a.md").read_text(encoding="utf-8")
    assert "singleton" not in (workspace / "okf" / "b.md").read_text(encoding="utf-8")


def test_apply_reports_a_malformed_disposition_without_a_traceback(workspace, tmp_path):
    seed(workspace, {"a": ["lint"]})
    disposition = tmp_path / "d.yaml"
    disposition.write_text("generated: 2026-09-08\ntagged_pages: 1\nkept: []\n", encoding="utf-8", newline="")
    result = runner.invoke(app, ["wiki", "tags", "apply", str(disposition), "--workspace", str(workspace)])
    assert result.exit_code != 0
    assert "top-level" in result.stdout + result.stderr


def test_gate_exits_non_zero_on_an_undeclared_tag(workspace):
    seed(
        workspace,
        {"a": ["lint", "rogue"]},
        vocabulary="version: 1\ntags:\n  - name: lint\n    description: The lint machinery.\n",
    )
    result = runner.invoke(app, ["wiki", "tags", "gate", "--workspace", str(workspace)])
    assert result.exit_code != 0
    assert "rogue" in result.stdout + result.stderr


def test_gate_passes_a_fully_declared_vault(workspace):
    seed(
        workspace,
        {"a": ["lint"]},
        vocabulary="version: 1\ntags:\n  - name: lint\n    description: The lint machinery.\n",
    )
    result = runner.invoke(app, ["wiki", "tags", "gate", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.stdout


def test_gate_json_lists_the_undeclared_tags(workspace):
    seed(
        workspace,
        {"a": ["lint", "rogue"]},
        vocabulary="version: 1\ntags:\n  - name: lint\n    description: The lint machinery.\n",
    )
    result = runner.invoke(app, ["wiki", "tags", "gate", "--json", "--workspace", str(workspace)])
    assert json.loads(result.stdout)["undeclared"] == ["rogue"]


def test_gate_reports_a_missing_vocabulary_file(workspace):
    seed(workspace, {"a": ["lint"]})
    result = runner.invoke(app, ["wiki", "tags", "gate", "--workspace", str(workspace)])
    assert result.exit_code != 0
    assert "tags.yaml" in result.stdout + result.stderr
