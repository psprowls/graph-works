from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from helpers import BUNDLES
from okf_io import bundle
from okf_io.validate import validate

TODAY = date(2026, 8, 3)
FENCE = "# Computation\n\n```sql\nSELECT 1\n```\n"


def report_for(tmp_path: Path, files: dict[str, str]):
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return validate(bundle.load(tmp_path), today=TODAY)


def concept(frontmatter: str, body: str, type_name: str = "Attested Computation") -> str:
    return f"---\ntype: {type_name}\ntitle: T\ndescription: D\n{frontmatter}---\n\n{body}"


def test_runtime_is_required_for_this_type(tmp_path):
    report = report_for(tmp_path, {"a.md": concept("", FENCE)})
    assert report.by_code("computation.runtime-missing")[0].severity == "error"


@pytest.mark.parametrize(
    "type_name",
    [
        "attested computation",
        "  Attested Computation  ",
        "Attested  Computation",
        "Attested   Computation",
    ],
)
def test_the_type_is_matched_case_insensitively_and_whitespace_normalized(tmp_path, type_name):
    report = report_for(tmp_path, {"a.md": concept("", FENCE, type_name)})
    assert report.by_code("computation.runtime-missing") != ()


def test_another_type_is_never_held_to_this_contract(tmp_path):
    """§11: a producer-defined type is not rejected for another type's fields."""
    report = report_for(tmp_path, {"a.md": concept("", "# Definition\n", "Dashboard")})
    assert report.by_code("computation.runtime-missing") == ()
    assert report.by_code("computation.missing") == ()


def test_no_separator_is_not_a_whitespace_variant(tmp_path):
    """AttestedComputation with no space is a different type, not a typo of Attested Computation."""
    report = report_for(tmp_path, {"a.md": concept("", FENCE, "AttestedComputation")})
    assert report.by_code("computation.runtime-missing") == ()
    assert report.by_code("computation.missing") == ()


def test_neither_inline_nor_path_is_an_error(tmp_path):
    report = report_for(tmp_path, {"a.md": concept("runtime: bigquery\n", "# Definition\n")})
    assert report.by_code("computation.missing")[0].severity == "error"


def test_an_indented_block_satisfies_it(tmp_path):
    """§10.2's own worked example uses a four-space indented block."""
    body = "# Computation\n\n    SELECT 1\n"
    report = report_for(tmp_path, {"a.md": concept("runtime: bigquery\n", body)})
    assert report.by_code("computation.missing") == ()


def test_a_block_under_another_heading_does_not_satisfy_it(tmp_path):
    body = "# Notes\n\n```sql\nSELECT 1\n```\n"
    report = report_for(tmp_path, {"a.md": concept("runtime: bigquery\n", body)})
    assert report.by_code("computation.missing") != ()


def test_both_forms_at_once_is_a_duplicate(tmp_path):
    frontmatter = "runtime: bigquery\ncomputation: lib/q.sql\n"
    report = report_for(tmp_path, {"a.md": concept(frontmatter, FENCE), "lib/q.sql": "SELECT 1\n"})
    assert report.by_code("computation.duplicate")[0].severity == "error"


def test_duplicate_is_field_scoped_not_type_scoped(tmp_path):
    frontmatter = "computation: lib/q.sql\n"
    report = report_for(
        tmp_path,
        {"a.md": concept(frontmatter, FENCE, "Metric"), "lib/q.sql": "SELECT 1\n"},
    )
    assert report.by_code("computation.duplicate") != ()


def test_an_incomplete_parameter_names_what_is_missing(tmp_path):
    frontmatter = "runtime: bigquery\nparameters:\n  - { name: year }\n"
    report = report_for(tmp_path, {"a.md": concept(frontmatter, FENCE)})
    finding = report.by_code("computation.parameter-incomplete")[0]
    assert finding.severity == "warn"
    assert "`type`" in finding.message
    assert "parameters[0]" in finding.message


def test_every_path_field_is_resolved(tmp_path):
    frontmatter = (
        "runtime: bigquery\n"
        "executor:\n  resource: skills/gone.md\n"
        "attester:\n  resource: attesters/gone.py\n"
    )
    report = report_for(tmp_path, {"a.md": concept(frontmatter, FENCE)})
    messages = [f.message for f in report.by_code("computation.path-unresolved")]
    assert any("executor.resource" in m for m in messages)
    assert any("attester.resource" in m for m in messages)


def test_a_url_is_not_a_bundle_path(tmp_path):
    frontmatter = "runtime: bigquery\nexecutor:\n  resource: https://wiki.acme/run\n"
    report = report_for(tmp_path, {"a.md": concept(frontmatter, FENCE)})
    assert report.by_code("computation.path-unresolved") == ()


def test_a_resolving_path_is_silent(tmp_path):
    frontmatter = "runtime: bigquery\nexecutor:\n  resource: /skills/run.md\n"
    files = {
        "deep/a.md": concept(frontmatter, FENCE),
        "skills/run.md": "---\ntype: Skill\ntitle: T\ndescription: D\n---\n\n# Steps\n",
    }
    assert report_for(tmp_path, files).by_code("computation.path-unresolved") == ()


def test_the_repair_hint_suggests_the_root_form(tmp_path):
    frontmatter = "runtime: bigquery\nexecutor:\n  resource: skills/run.md\n"
    files = {
        "deep/a.md": concept(frontmatter, FENCE),
        "skills/run.md": "---\ntype: Skill\ntitle: T\ndescription: D\n---\n\n# Steps\n",
    }
    report = report_for(tmp_path, files)
    assert "`/skills/run.md`" in report.by_code("computation.path-unresolved")[0].message


def test_the_acme_expectation_is_asserted_not_assumed():
    """acme_retail's relative executor/attester paths resolve against
    `computations/`, where they do not exist. WARN with a hint, not an error."""
    report = validate(bundle.load(BUNDLES / "acme_retail"), today=TODAY)
    findings = [
        f
        for f in report.by_code("computation.path-unresolved")
        if f.path == "computations/revenue-ytd.md"
    ]
    assert {f.severity for f in findings} == {"warn"}
    assert any("`/skills/run-on-bq.md`" in f.message for f in findings)
    assert report.ok is True
