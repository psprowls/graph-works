"""The scaffold regime: validity, not identity, and a re-run that is a no-op."""

import sys
from datetime import date

from okf_ext.bundle import SCAFFOLD_MEMBERS, apply, plan_scaffold

_TODAY = date(2026, 1, 1)
# Tied to the exported constant, not a locally hardcoded tuple: `plan_install`
# refuses any member in `SCAFFOLD_MEMBERS`, and `plan_scaffold`'s loop is the
# thing that constant describes -- asserting the loop's actual output against
# the import (rather than a copy of it) is what keeps the two from drifting
# apart silently.
_MEMBERS = SCAFFOLD_MEMBERS


def test_scaffold_creates_the_three_files_into_an_empty_directory(tmp_path):
    root = tmp_path / "bundle"
    plan = plan_scaffold(root, today=_TODAY)
    assert [planned.member for planned in plan.writes] == list(_MEMBERS)
    assert plan.ok and not plan.is_empty
    assert not root.exists()  # planning writes nothing

    result = apply(plan)
    assert result.ok
    assert result.written == _MEMBERS
    for member in _MEMBERS:
        assert (root / member).is_file()


def test_scaffold_is_a_no_op_the_second_time(tmp_path):
    """The whole reason this sits at tier 2: a second package's scaffold pass
    must not refuse a bundle the first package created."""
    root = tmp_path / "bundle"
    apply(plan_scaffold(root, today=_TODAY))
    before = {member: (root / member).read_bytes() for member in _MEMBERS}

    plan = plan_scaffold(root, today=_TODAY)
    assert plan.is_empty
    assert plan.ok
    assert {item.path for item in plan.skipped} == set(_MEMBERS)
    assert {item.reason for item in plan.skipped} == {"already-present"}

    result = apply(plan)
    assert result.written == ()
    assert len(result.skipped) == 3
    assert {member: (root / member).read_bytes() for member in _MEMBERS} == before


def test_an_unparseable_index_is_a_foreign_content_refusal_not_a_crash(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    content = "---\nfoo: [1, 2\n---\n"
    (root / "index.md").write_text(content, encoding="utf-8")

    plan = plan_scaffold(root, today=_TODAY)
    assert not plan.ok
    refusal = next(f for f in plan.refusals if f.path == "index.md")
    assert refusal.kind == "foreign-content"
    assert "does not parse" in refusal.error
    assert "index.md" in refusal.error

    # The other two are still this plan's to write -- one refusal does not
    # take its neighbours down with it.
    assert [planned.member for planned in plan.writes] == ["log.md", "_tags.yaml"]
    result = apply(plan)
    assert result.written == ("log.md", "_tags.yaml")
    assert not result.ok
    assert (root / "index.md").read_text(encoding="utf-8") == content


def test_an_index_without_okf_version_is_not_a_bundle_root(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "index.md").write_text("# Some other directory\n", encoding="utf-8")

    plan = plan_scaffold(root, today=_TODAY)
    refusal = next(f for f in plan.refusals if f.path == "index.md")
    assert refusal.kind == "foreign-content"
    assert "okf_version" in refusal.error


def test_a_log_with_real_entries_is_skipped_never_compared(tmp_path):
    """`log.md` gains an entry per sync, so a template comparison would report
    a difference on every bundle that has ever been used."""
    root = tmp_path / "bundle"
    apply(plan_scaffold(root, today=_TODAY))
    lived_in = (root / "log.md").read_text(encoding="utf-8") + "\n- a later entry\n"
    (root / "log.md").write_text(lived_in, encoding="utf-8")

    plan = plan_scaffold(root, today=_TODAY)
    assert plan.ok and plan.is_empty
    apply(plan)
    assert (root / "log.md").read_text(encoding="utf-8") == lived_in


def test_a_malformed_tags_file_is_refused_but_a_list_shaped_one_names_its_shape(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "_tags.yaml").write_text("- one\n- two\n", encoding="utf-8")

    plan = plan_scaffold(root, today=_TODAY)
    refusal = next(f for f in plan.refusals if f.path == "_tags.yaml")
    assert refusal.kind == "foreign-content"
    assert "not a mapping" in refusal.error


def test_declarations_dir_relocates_only_the_declaration_member(tmp_path):
    root = tmp_path / "bundle"
    elsewhere = tmp_path / "declarations"
    plan = plan_scaffold(root, today=_TODAY, declarations_dir=elsewhere)
    apply(plan)

    assert (root / "index.md").is_file()
    assert (root / "log.md").is_file()
    assert (elsewhere / "_tags.yaml").is_file()
    assert not (root / "_tags.yaml").exists()


def test_the_scaffold_log_entry_names_no_package(tmp_path):
    """Tier 2 must not name a tier-3 package: in a shared bundle, scaffolding
    is the one act none of them owns."""
    root = tmp_path / "bundle"
    apply(plan_scaffold(root, today=_TODAY))
    text = (root / "log.md").read_text(encoding="utf-8")
    assert "## 2026-01-01" in text
    assert "bundle scaffold created" in text
    assert "code-wiki-okf" not in text


def test_genuinely_malformed_tags_yaml_is_refused(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "_tags.yaml").write_text("tags: {a: 1", encoding="utf-8")

    plan = plan_scaffold(root, today=_TODAY)
    refusal = next(f for f in plan.refusals if f.path == "_tags.yaml")
    assert refusal.kind == "foreign-content"
    assert "not valid YAML" in refusal.error


def test_deeply_nested_tags_yaml_does_not_crash(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    # Derived from the live limit rather than hard-coded: the depth that blows
    # the parser's stack moves with `sys.setrecursionlimit` and with how many
    # frames ruamel spends per level.
    depth = sys.getrecursionlimit() * 3
    pathological = "tags: " + "[" * depth + "]" * depth
    (root / "_tags.yaml").write_text(pathological, encoding="utf-8")

    plan = plan_scaffold(root, today=_TODAY)
    assert not plan.ok
    refusal = next(f for f in plan.refusals if f.path == "_tags.yaml")
    assert refusal.kind == "foreign-content"
    assert "not valid YAML" in refusal.error


def test_unparseable_log_is_refused(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "log.md").write_text("---\nfoo: [1, 2\n---\n", encoding="utf-8")

    plan = plan_scaffold(root, today=_TODAY)
    refusal = next(f for f in plan.refusals if f.path == "log.md")
    assert refusal.kind == "foreign-content"
    assert "does not parse" in refusal.error


def test_unwritable_file_is_refused(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "index.md").mkdir()

    plan = plan_scaffold(root, today=_TODAY)
    refusal = next(f for f in plan.refusals if f.path == "index.md")
    assert refusal.kind == "unwritable"
