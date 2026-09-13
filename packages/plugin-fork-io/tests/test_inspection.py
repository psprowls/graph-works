import json

from helpers import write_skill
from plugin_fork_io import Services, SourceSpec, inspect_source
from plugin_fork_io.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def test_inspect_is_read_only_and_reports_skill(tmp_path):
    source = tmp_path / "source"
    write_skill(source, "review")
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}

    result = inspect_source(SourceSpec(str(source), "local"), services=Services.local())

    assert result.allowed and not result.applied
    assert result.data["skills"][0]["name"] == "review"
    assert before == {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    assert not (tmp_path / ".plugin-fork").exists()


def test_inspect_reports_located_yaml_error_and_continues(tmp_path):
    source = tmp_path / "source"
    write_skill(source, "review")
    broken = source / "skills" / "broken" / "SKILL.md"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"---\nname: [broken\ndescription: Broken\n---\n")

    result = inspect_source(SourceSpec(str(source), "local"), services=Services.local())

    assert not result.allowed
    assert [skill["name"] for skill in result.data["skills"]] == ["review"]
    assert [(finding.code, finding.path, finding.line) for finding in result.findings] == [
        ("skill.yaml", "skills/broken/SKILL.md", 3)
    ]


def test_inspect_reports_missing_name_and_description(tmp_path):
    source = tmp_path / "source"
    skill = source / "skills" / "broken" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(b"---\nname: ''\n---\n")

    result = inspect_source(SourceSpec(str(source), "local"), services=Services.local())

    assert [(finding.code, finding.line) for finding in result.findings] == [
        ("skill.name", 2),
        ("skill.description", 2),
    ]


def test_inspect_rejects_nonportable_skill_name(tmp_path):
    source = tmp_path / "source"
    skill = source / "skills" / "broken" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(b"---\nname: Bad_Name\ndescription: Broken\n---\n")

    result = inspect_source(SourceSpec(str(source), "local"), services=Services.local())

    assert [(finding.code, finding.line) for finding in result.findings] == [("skill.name", 2)]


def test_inspect_reports_invalid_utf8_at_its_line_and_continues(tmp_path):
    source = tmp_path / "source"
    write_skill(source, "review")
    broken = source / "skills" / "broken" / "SKILL.md"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"---\nname: broken\ndescription: \xff\n---\n")

    result = inspect_source(SourceSpec(str(source), "local"), services=Services.local())

    assert [(finding.code, finding.line) for finding in result.findings] == [("skill.encoding", 3)]
    assert [skill["name"] for skill in result.data["skills"]] == ["review"]


def test_inspect_reports_frontmatter_shapes(tmp_path):
    source = tmp_path / "source"
    contents = {
        "absent": b"# Skill\n",
        "unclosed": b"---\nname: broken\n",
        "not-mapping": b"---\n- broken\n---\n",
    }
    for name, content in contents.items():
        skill = source / "skills" / name / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_bytes(content)

    result = inspect_source(SourceSpec(str(source), "local"), services=Services.local())

    assert [(finding.code, finding.path, finding.line) for finding in result.findings] == [
        ("skill.frontmatter", "skills/absent/SKILL.md", 1),
        ("skill.yaml", "skills/not-mapping/SKILL.md", 2),
        ("skill.frontmatter", "skills/unclosed/SKILL.md", 1),
    ]


def test_inspect_accepts_crlf_frontmatter_without_rewriting(tmp_path):
    source = tmp_path / "source"
    skill = source / "skills" / "review" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    original = b"---\r\nname: review\r\ndescription: Review changes.\r\n---\r\n\r\nKeep local intent.\r\n"
    skill.write_bytes(original)

    result = inspect_source(SourceSpec(str(source), "local"), services=Services.local())

    assert result.allowed
    assert result.data["skills"][0]["name"] == "review"
    assert skill.read_bytes() == original


def test_inspect_does_not_treat_prefixed_hyphens_as_a_closing_fence(tmp_path):
    source = tmp_path / "source"
    skill = source / "skills" / "broken" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(b"---\nname: broken\ndescription: Broken\n---junk\n")

    result = inspect_source(SourceSpec(str(source), "local"), services=Services.local())

    assert [(finding.code, finding.line) for finding in result.findings] == [("skill.frontmatter", 1)]


def test_inspect_git_requires_explicit_revision():
    result = inspect_source(SourceSpec("https://example.test/skills.git", "git"), services=Services.local())

    assert not result.allowed
    assert [finding.code for finding in result.findings] == ["git.revision"]


def test_json_refusal_is_valid_json(tmp_path):
    missing = tmp_path / "missing"

    invocation = runner.invoke(app, ["inspect", str(missing), "--json"])

    assert invocation.exit_code == 3
    payload = json.loads(invocation.stdout)
    assert payload["operation"] == "inspect"
    assert payload["allowed"] is False
    assert payload["findings"][0]["code"] == "source.missing"
    assert invocation.stderr == ""


def test_internal_error_is_stderr_and_exit_four(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr("plugin_fork_io.cli.inspect_source", fail)

    invocation = runner.invoke(app, ["inspect", str(tmp_path), "--json"])

    assert invocation.exit_code == 4
    payload = json.loads(invocation.stdout)
    assert payload["operation"] == "inspect"
    assert payload["allowed"] is False
    assert payload["applied"] is False
    assert payload["findings"][0]["code"] == "internal.error"
    assert "disk unavailable" in invocation.stderr


def test_text_output_uses_the_same_result(tmp_path):
    invocation = runner.invoke(app, ["inspect", str(tmp_path)])

    assert invocation.exit_code == 0
    heading, details = invocation.stdout.split("\n", 1)
    assert heading == "inspect: allowed"
    machine = runner.invoke(app, ["inspect", str(tmp_path), "--json"])
    assert json.loads(details) == json.loads(machine.stdout)["data"]


def test_inspection_preserves_link_evidence_without_following(tmp_path):
    write_skill(tmp_path, "review")
    link = tmp_path / "skills" / "alias"
    link.symlink_to("review", target_is_directory=True)
    result = inspect_source(SourceSpec(str(tmp_path), "local"), services=Services.local())
    assert [skill["name"] for skill in result.data["skills"]] == ["review"]
    assert next(e for e in result.data["entries"] if e["path"] == "skills/alias")["kind"] == "symlink"
