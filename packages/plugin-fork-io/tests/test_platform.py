"""Cross-platform bytes, path evidence and native filesystem contracts."""

import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from helpers import selection, write_skill
from plugin_fork_io import Roots, Services, SourceSpec, apply_preview, plan_fork
from plugin_fork_io.records import SourceLink
from plugin_fork_io.references import scan_markdown
from plugin_fork_io.updates import plan_update


def test_round_counter_template_is_not_a_skill_invocation():
    dependencies, _ = scan_markdown(b"`Task <N>: fix round <R>/5` then use `/review`.\n", "SKILL.md")
    assert [dependency.target for dependency in dependencies] == ["review"]


@pytest.mark.parametrize("case", ["approved", "retargeted", "new-unapproved"])
def test_source_link_evidence_survives_update_without_implicit_reapproval(tmp_path, case):
    source = tmp_path / "source"
    write_skill(source, "review")
    (source / "helper.bin").write_bytes(b"\x00original\xff")
    (source / "skills/review/helper.bin").symlink_to("../../helper.bin")
    roots = Roots(tmp_path / "content", tmp_path / "state")
    chosen = replace(selection("review"), source_links=(SourceLink("skills/review/helper.bin", "materialize"),))
    fork = plan_fork(SourceSpec(str(source), "local"), chosen, roots, intent=(), services=Services.local())
    assert fork.allowed, fork.findings
    assert apply_preview(roots.state, fork.preview_id, services=Services.local()).applied
    incoming = tmp_path / "incoming"
    shutil.copytree(source, incoming, symlinks=True)
    (incoming / "helper.bin").write_bytes(b"\x00incoming\xff")
    if case == "retargeted":
        (incoming / "other.bin").write_bytes(b"other")
        (incoming / "skills/review/helper.bin").unlink()
        (incoming / "skills/review/helper.bin").symlink_to("../../other.bin")
    elif case == "new-unapproved":
        (incoming / "skills/review/extra.bin").symlink_to("../../helper.bin")
    update = plan_update(
        roots,
        fork.variant_id,
        SourceSpec(str(incoming), "local"),
        selection=None,
        mappings={},
        services=Services.local(),
    )
    if case == "approved":
        assert update.allowed, update.findings
        target = Path(update.data["candidate_path"]) / "review/helper.bin"
        assert target.read_bytes() == b"\x00incoming\xff" and not target.is_symlink()
    else:
        assert not update.allowed
        assert {"adaptation.ambiguous", "source.link-unapproved"} & {f.code for f in update.findings}
    assert (roots.content / "review/helper.bin").read_bytes() == b"\x00original\xff"


@pytest.mark.parametrize(
    "payload",
    [
        b"\xef\xbb\xbf---\r\nname: review\r\ndescription: Example\r\n---\r\nKeep\r\n",
        b"---\nname: review\ndescription: Example\n---\nKeep\n",
    ],
)
def test_native_roundtrip_preserves_bom_line_endings_binary_and_modes(tmp_path, payload):
    source = tmp_path / "source"
    skill = write_skill(source, "review")
    skill.write_bytes(payload)
    binary = skill.parent / "inert.bin"
    binary.write_bytes(b"\x00\xff\r\n\xef\xbb\xbf")
    binary.chmod(0o755)
    roots = Roots(tmp_path / "content", tmp_path / "state")
    preview = plan_fork(
        SourceSpec(str(source), "local"), selection("review"), roots, intent=(), services=Services.local()
    )
    assert preview.allowed, preview.findings
    assert apply_preview(roots.state, preview.preview_id, services=Services.local()).applied
    assert (roots.content / "review/SKILL.md").read_bytes() == payload
    assert (roots.content / "review/inert.bin").read_bytes() == binary.read_bytes()
    assert (roots.content / "review/inert.bin").stat().st_mode & 0o777 == binary.stat().st_mode & 0o777


@pytest.mark.parametrize("replace_side", ["incoming", "local"])
def test_file_directory_collision_preserves_local_subtree_and_requires_parent_resolution(tmp_path, replace_side):
    source = tmp_path / "source"
    write_skill(source, "review")
    original = source / "skills/review/parent"
    original.mkdir()
    (original / "child.bin").write_bytes(b"base")
    roots = Roots(tmp_path / "content", tmp_path / "state")
    fork = plan_fork(SourceSpec(str(source), "local"), selection("review"), roots, intent=(), services=Services.local())
    assert apply_preview(roots.state, fork.preview_id, services=Services.local()).applied
    incoming = tmp_path / "incoming"
    shutil.copytree(source, incoming)
    local_parent = roots.content / "review/parent"
    incoming_parent = incoming / "skills/review/parent"
    if replace_side == "incoming":
        (local_parent / "new-local.bin").write_bytes(b"keep local addition")
        shutil.rmtree(incoming_parent)
        incoming_parent.write_bytes(b"new upstream file")
    else:
        shutil.rmtree(local_parent)
        local_parent.write_bytes(b"keep local replacement")
        (incoming_parent / "child.bin").write_bytes(b"upstream changed child")
    update = plan_update(
        roots,
        fork.variant_id,
        SourceSpec(str(incoming), "local"),
        selection=None,
        mappings={},
        services=Services.local(),
    )
    assert not update.allowed
    expected_path, expected_code = (
        ("review/parent", "merge.kind")
        if replace_side == "incoming"
        else ("review/parent/child.bin", "merge.modify-delete")
    )
    assert any(c["path"] == expected_path and c["code"] == expected_code for c in update.data["conflicts"])
    staged = Path(update.data["candidate_path"]) / "review/parent"
    if replace_side == "incoming":
        assert (staged / "new-local.bin").read_bytes() == b"keep local addition"
        assert (staged / "child.bin").read_bytes() == b"base"
    else:
        assert staged.read_bytes() == local_parent.read_bytes() == b"keep local replacement"


def test_inspection_retains_nonregular_skill_evidence_and_declared_host_requirements(tmp_path):
    from plugin_fork_io import inspect_source

    source = tmp_path / "source"
    definition = write_skill(source, "review")
    (source / "SKILL.md").symlink_to("skills/review/SKILL.md")
    metadata = definition.parent / "agents/openai.yaml"
    metadata.parent.mkdir()
    metadata.write_bytes(b"dependencies:\n  tools:\n    - value: external-tool\n")
    result = inspect_source(SourceSpec(str(source), "local"), services=Services.local())
    assert result.allowed
    assert "source.link" in {f.code for f in result.findings}
    assert any(d["target"] == "external-tool" and d["kind"] == "host" for d in result.data["dependencies"])
    assert (source / "SKILL.md").is_symlink()
