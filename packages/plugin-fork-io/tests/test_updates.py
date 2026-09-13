import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from helpers import codes, selection, write_skill
from plugin_fork_io import (
    Roots,
    Services,
    SourceSpec,
    apply_preview,
    load_ledger,
    load_preview,
    plan_adopt,
    plan_fork,
    read_snapshot,
)
from plugin_fork_io.records import Adaptation, Component, SourceMapping
from plugin_fork_io.updates import plan_update


def forked(tmp_path):
    source = tmp_path / "source"
    write_skill(source, "review")
    roots = Roots(tmp_path / "content", tmp_path / "state")
    chosen = replace(selection("review"), skills=(Component("skills/review", "local-review"),))
    result = plan_fork(SourceSpec(str(source), "local"), chosen, roots, intent=(), services=Services.local())
    assert result.allowed, result
    assert apply_preview(roots.state, result.preview_id, services=Services.local()).applied
    return roots, result.variant_id


def test_update_retains_local_name_without_advancing_base(tmp_path):
    from plugin_fork_io.updates import plan_update

    roots, variant = forked(tmp_path)
    ledger_before = load_ledger(roots.state, variant)
    local = roots.content / "local-review/SKILL.md"
    local.write_bytes(local.read_bytes() + b"\nKeep the human approval gate.\n")
    before = local.read_bytes()
    incoming = tmp_path / "incoming"
    write_skill(incoming, "review", b"Upstream improvement.\n")
    result = plan_update(
        roots, variant, SourceSpec(str(incoming), "local"), selection=None, mappings={}, services=Services.local()
    )
    assert result.preview_id is not None and not result.applied, result
    candidate = Path(result.data["candidate_path"]) / "local-review/SKILL.md"
    assert b"name: local-review" in candidate.read_bytes()
    assert local.read_bytes() == before
    assert load_ledger(roots.state, variant) == ledger_before
    assert "upstream_diff" in result.data and "candidate_diff" in result.data


def preserved(roots, variant):
    return (
        tuple(
            (p.relative_to(roots.content).as_posix(), p.lstat().st_mode, p.read_bytes() if p.is_file() else None)
            for p in sorted(roots.content.rglob("*"))
        ),
        tuple(
            (p.relative_to(roots.state / "forks").as_posix(), p.read_bytes())
            for p in sorted((roots.state / "forks").rglob("*"))
            if p.is_file()
        ),
    )


def update(roots, variant, incoming, **kwargs):
    before = preserved(roots, variant)
    result = plan_update(
        roots,
        variant,
        SourceSpec(str(incoming), "local"),
        selection=kwargs.pop("selection", None),
        mappings=kwargs.pop("mappings", {}),
        services=kwargs.pop("services", Services.local()),
    )
    assert preserved(roots, variant) == before
    return result


def test_deleted_source_uses_preserved_base_and_keeps_local_extra(tmp_path):
    roots, variant = forked(tmp_path)
    shutil.rmtree(tmp_path / "source")
    extra = roots.content / "local-review/local.txt"
    extra.write_bytes(b"untracked local intent\r\n")
    incoming = tmp_path / "incoming"
    write_skill(incoming, "review", b"incoming\n")
    (incoming / "skills/review/new.txt").write_bytes(b"upstream addition\n")
    result = update(roots, variant, incoming)
    assert result.allowed, result
    candidate = Path(result.data["candidate_path"])
    assert (candidate / "local-review/local.txt").read_bytes() == extra.read_bytes()
    assert (candidate / "local-review/new.txt").read_bytes() == b"upstream addition\n"
    preview = load_preview(roots.state, result.preview_id)
    stage = candidate.parent
    assert (stage / "base.tar.gz").read_bytes() == (roots.state / "forks" / variant / "base.tar.gz").read_bytes()
    assert b"name: review" in next(
        e.content for e in read_snapshot(stage / "incoming.tar.gz").entries if e.path.endswith("SKILL.md")
    )
    (candidate / "local-review/new.txt").write_bytes(b"review edit")
    assert load_preview(roots.state, result.preview_id) == preview
    (stage / "incoming.tar.gz").write_bytes(b"tamper")
    with pytest.raises(ValueError, match="Immutable"):
        load_preview(roots.state, result.preview_id)


def test_addition_collides_with_local_extra_and_retains_conflict_evidence(tmp_path):
    roots, variant = forked(tmp_path)
    (roots.content / "local-review/new.bin").write_bytes(b"\0local")
    incoming = tmp_path / "incoming"
    write_skill(incoming, "review")
    (incoming / "skills/review/new.bin").write_bytes(b"\0incoming")
    result = update(roots, variant, incoming)
    assert not result.allowed and "merge.add-add" in codes(result)
    preview = load_preview(roots.state, result.preview_id)
    assert preview.conflicts[0].path == "local-review/new.bin"
    assert preview.conflicts[0].base_hash is None


def test_explicit_root_mapping_and_file_mapping(tmp_path):
    roots, variant = forked(tmp_path)
    incoming = tmp_path / "incoming"
    write_skill(incoming, "review", b"improved\n")
    (incoming / "skills/review").rename(incoming / "skills/moved")
    result = update(roots, variant, incoming, mappings={"skills/review": "skills/moved"})
    assert result.allowed, result
    candidate = Path(result.data["candidate_path"])
    assert b"improved" in (candidate / "local-review/SKILL.md").read_bytes()
    assert load_preview(roots.state, result.preview_id).selection.skills[0].source == "skills/moved"


def test_whole_selection_refusal_and_unknown_origin(tmp_path):
    roots, variant = forked(tmp_path)
    result = update(roots, variant, tmp_path / "source", selection=selection())
    assert not result.allowed and result.preview_id is None
    adopted = Roots(tmp_path / "adopted", tmp_path / "adopt-state")
    write_skill(adopted.content, "review")
    plan = plan_adopt(adopted, selection("review"), base=None, evidence={}, services=Services.local())
    assert apply_preview(adopted.state, plan.preview_id, services=Services.local()).applied
    result = update(adopted, plan.variant_id, tmp_path / "source")
    assert codes(result) == {"origin.unknown"}


def test_ambiguous_replay_blocks_candidate(tmp_path):
    roots, variant = forked(tmp_path)
    incoming = tmp_path / "incoming"
    skill = write_skill(incoming, "review")
    skill.write_bytes(skill.read_bytes().replace(b"name: review", b"# shifted\nname: review"))
    result = update(roots, variant, incoming)
    assert not result.allowed and result.preview_id is not None
    assert "adaptation.ambiguous" in codes(result)


def test_unique_relocation(tmp_path):
    from plugin_fork_io.adaptations import relocate, replay

    edit = Adaptation("resource-path", "file", 0, 6, b"TARGET", b"local", "approved")
    relocated, problems = relocate(b"prefix TARGET suffix", (edit,))
    assert not problems
    assert replay(b"prefix TARGET suffix", relocated) == (b"prefix local suffix", ())


def test_explicit_resources_and_required_reference_validation(tmp_path):
    roots, variant = forked(tmp_path)
    incoming = tmp_path / "incoming"
    write_skill(incoming, "review", b"[helper](../../shared.txt)\n")
    (incoming / "shared.txt").write_bytes(b"helper")
    result = update(roots, variant, incoming)
    assert not result.allowed and "dependency.missing" in codes(result)
    skill = (incoming / "skills/review/SKILL.md").read_bytes()
    start = skill.index(b"../../shared.txt")
    chosen = replace(
        selection("review"),
        skills=(Component("skills/review", "local-review"),),
        resources=(SourceMapping("shared.txt", "shared.txt"),),
        adaptations=(
            Adaptation(
                "resource-path",
                "skills/review/SKILL.md",
                start,
                start + len(b"../../shared.txt"),
                b"../../shared.txt",
                b"../shared.txt",
                "approved new resource",
            ),
        ),
    )
    result = update(roots, variant, incoming, selection=chosen)
    assert result.allowed, result
    preview = load_preview(roots.state, result.preview_id)
    assert "shared.txt" in preview.owned_paths
    assert all(d.satisfied for d in preview.candidate_dependencies if d.required)


def test_update_cli_prepares_without_apply(tmp_path):
    import json

    from plugin_fork_io.cli import app
    from typer.testing import CliRunner

    roots, variant = forked(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "update",
            variant,
            "--source",
            str(tmp_path / "source"),
            "--content-dir",
            str(roots.content),
            "--state-dir",
            str(roots.state),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["preview_id"] and not payload["applied"]


def fork_with_helper(tmp_path):
    source = tmp_path / "source"
    write_skill(source, "review")
    (source / "skills/review/helper.txt").write_bytes(b"base\n")
    roots = Roots(tmp_path / "content", tmp_path / "state")
    result = plan_fork(
        SourceSpec(str(source), "local"), selection("review"), roots, intent=(), services=Services.local()
    )
    assert apply_preview(roots.state, result.preview_id, services=Services.local()).applied
    incoming = tmp_path / "incoming"
    shutil.copytree(source, incoming)
    return roots, result.variant_id, incoming


@pytest.mark.parametrize(
    "local,incoming,code,expected",
    [
        (None, None, None, None),
        (b"local\n", None, "merge.modify-delete", b"local\n"),
        (None, b"incoming\n", "merge.modify-delete", None),
        (b"base\n", None, None, None),
    ],
)
def test_update_deletion_matrix(tmp_path, local, incoming, code, expected):
    roots, variant, source = fork_with_helper(tmp_path)
    live = roots.content / "review/helper.txt"
    new = source / "skills/review/helper.txt"
    live.unlink() if local is None else live.write_bytes(local)
    new.unlink() if incoming is None else new.write_bytes(incoming)
    result = update(roots, variant, source)
    assert result.preview_id, result
    if code:
        assert code in codes(result)
    else:
        assert result.allowed, result
    candidate = Path(result.data["candidate_path"]) / "review/helper.txt"
    assert (candidate.read_bytes() if candidate.exists() else None) == expected


def test_file_mapping_aligns_only_with_explicit_join(tmp_path):
    roots, variant, incoming = fork_with_helper(tmp_path)
    (incoming / "skills/review/helper.txt").rename(incoming / "skills/review/renamed.txt")
    result = update(roots, variant, incoming)
    assert "mapping.rename-suggested" in codes(result)
    candidate = Path(result.data["candidate_path"])
    assert not (candidate / "review/helper.txt").exists()
    result = update(roots, variant, incoming, mappings={"skills/review/helper.txt": "skills/review/renamed.txt"})
    assert result.allowed, result
    candidate = Path(result.data["candidate_path"])
    assert (candidate / "review/helper.txt").read_bytes() == b"base\n"
    assert not (candidate / "review/renamed.txt").exists()
    assert (
        SourceMapping("skills/review/renamed.txt", "review/helper.txt")
        in load_preview(roots.state, result.preview_id).source_mappings
    )


def test_missing_explicit_new_resource_refuses(tmp_path):
    roots, variant = forked(tmp_path)
    chosen = replace(
        selection("review"),
        skills=(Component("skills/review", "local-review"),),
        resources=(SourceMapping("absent", "absent"),),
    )
    result = update(roots, variant, tmp_path / "source", selection=chosen)
    assert not result.allowed


def test_permission_update_preserves_local_content(tmp_path):
    roots, variant, incoming = fork_with_helper(tmp_path)
    (roots.content / "review/helper.txt").write_bytes(b"local\n")
    Services.local().filesystem.chmod(incoming / "skills/review/helper.txt", 0o755)
    result = update(roots, variant, incoming)
    assert result.allowed, result
    entry = next(
        e
        for e in read_snapshot(Path(result.data["candidate_path"]).parent / "incoming.tar.gz").entries
        if e.path.endswith("helper.txt")
    )
    assert entry.mode == 0o755
    candidate = Path(result.data["candidate_path"]) / "review/helper.txt"
    assert candidate.read_bytes() == b"local\n"
    assert candidate.stat().st_mode & 0o777 == 0o755


def test_known_adoption_updates_nested_mapping(tmp_path):
    from plugin_fork_io import capture

    roots = Roots(tmp_path / "content", tmp_path / "state")
    write_skill(roots.content, "review")
    source = tmp_path / "source"
    write_skill(source, "review")
    original = capture(SourceSpec(str(source), "local"), (), services=Services.local())
    result = plan_adopt(
        roots,
        selection("review"),
        base=SourceSpec(str(source), "local"),
        evidence={
            "digest": original.digest,
            "declaration": "known original",
            "mappings": [{"source": "skills/review", "destination": "skills/review"}],
        },
        services=Services.local(),
    )
    assert apply_preview(roots.state, result.preview_id, services=Services.local()).applied
    (source / "skills/review/SKILL.md").write_bytes((source / "skills/review/SKILL.md").read_bytes() + b"upstream\n")
    result = update(roots, result.variant_id, source)
    assert result.allowed, result
    assert (Path(result.data["candidate_path"]) / "skills/review/SKILL.md").exists()


def test_new_declared_host_dependency_requires_approval(tmp_path):
    from plugin_fork_io.records import Dependency

    roots, variant = forked(tmp_path)
    chosen = replace(
        selection("review"),
        skills=(Component("skills/review", "local-review"),),
        dependencies=(Dependency("host", "service", "skills/review/SKILL.md", 1, 0, 0, "user", satisfied=False),),
    )
    result = update(roots, variant, tmp_path / "source", selection=chosen)
    assert "dependency.missing" in codes(result)
    approved = replace(chosen, dependencies=(replace(chosen.dependencies[0], satisfied=True),))
    result = update(roots, variant, tmp_path / "source", selection=approved)
    assert result.allowed, result


def test_directory_replaced_upstream_preserves_local_extra(tmp_path):
    roots, variant, incoming = fork_with_helper(tmp_path)
    helper = roots.content / "review/helper.txt"
    helper.unlink()
    helper.mkdir()
    (helper / "extra").write_bytes(b"local extra")
    (incoming / "skills/review/helper.txt").write_bytes(b"upstream change")
    result = update(roots, variant, incoming)
    assert result.preview_id and not result.allowed, result
    assert "merge.kind" in codes(result)
    assert (Path(result.data["candidate_path"]) / "review/helper.txt/extra").read_bytes() == b"local extra"


def test_upstream_directory_deletion_keeps_extra_parent_mode(tmp_path):
    source = tmp_path / "source"
    write_skill(source, "review")
    folder = source / "skills/review/helpers"
    folder.mkdir()
    (folder / "old").write_bytes(b"old")
    Services.local().filesystem.chmod(folder, 0o700)
    roots = Roots(tmp_path / "content", tmp_path / "state")
    prepared = plan_fork(
        SourceSpec(str(source), "local"), selection("review"), roots, intent=(), services=Services.local()
    )
    assert apply_preview(roots.state, prepared.preview_id, services=Services.local()).applied
    (roots.content / "review/helpers/local").write_bytes(b"extra")
    shutil.rmtree(folder)
    result = update(roots, prepared.variant_id, source)
    assert result.allowed, result
    candidate = Path(result.data["candidate_path"])
    assert (candidate / "review/helpers/local").read_bytes() == b"extra"
    assert (candidate / "review/helpers").stat().st_mode & 0o777 == 0o700


def test_recorded_file_mapping_composes_with_later_root_rename(tmp_path):
    from plugin_fork_io.updates import _selection

    roots, variant = forked(tmp_path)
    # Task 7 owns accepting the first update. Model its recorded mapping on a
    # real validated ledger to exercise Task 6's pure selection composition.
    ledger = load_ledger(roots.state, variant)
    ledger = replace(
        ledger, mappings=(*ledger.mappings, SourceMapping("skills/review/new.txt", "local-review/old.txt"))
    )
    chosen, mappings = _selection(ledger, None, {"skills/review": "skills/moved"})
    assert chosen.skills == (Component("skills/moved", "local-review"),)
    assert mappings == (
        SourceMapping("skills/moved", "local-review"),
        SourceMapping("skills/moved/new.txt", "local-review/old.txt"),
    )
    # An unrelated newly requested destination collision is still refused.
    with pytest.raises(ValueError, match="collide"):
        _selection(
            ledger,
            replace(chosen, resources=(*chosen.resources, SourceMapping("unrelated.txt", "local-review/old.txt"))),
            {"skills/review": "skills/moved"},
        )


def test_declared_upstream_skill_requirement_resolves_maintained_name(tmp_path):
    from plugin_fork_io.records import Dependency

    roots, variant = forked(tmp_path)
    chosen = replace(
        selection("review"),
        skills=(Component("skills/review", "local-review"),),
        dependencies=(Dependency("required-skill", "review", "skills/review/SKILL.md", 1, 0, 0, "user"),),
    )
    result = update(roots, variant, tmp_path / "source", selection=chosen)
    assert result.allowed, result
    preview = load_preview(roots.state, result.preview_id)
    declared = [d for d in preview.candidate_dependencies if d.evidence == "user"]
    assert len(declared) == 1
    assert declared[0].target == "local-review" and declared[0].satisfied
    assert preview.dependencies[0].target == "review"
