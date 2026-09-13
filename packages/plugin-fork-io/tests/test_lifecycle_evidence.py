"""Public regressions for durable supply evidence and observed installations."""

import shutil
from dataclasses import replace

import pytest
from helpers import selection, write_skill
from plugin_fork_io import (
    Roots,
    Services,
    SourceSpec,
    load_ledger,
    plan_fork,
    plan_rollback,
    read_status,
)
from plugin_fork_io.acceptance import plan_accept
from plugin_fork_io.records import Dependency
from plugin_fork_io.updates import plan_update
from test_installation import applied, install
from test_updates import forked


def supplied_fork(tmp_path):
    source = tmp_path / "source"
    write_skill(source, "review", b"Run /external-review.\n")
    supplied = Dependency(
        "required-skill", "external-review", "skills/review/SKILL.md", 5, 0, 0, "user", satisfied=True
    )
    chosen = replace(selection("review"), dependencies=(supplied,))
    roots = Roots(tmp_path / "content", tmp_path / "state")
    preview = plan_fork(SourceSpec(str(source), "local"), chosen, roots, intent=(), services=Services.local())
    applied(roots, preview)
    return roots, preview.variant_id, supplied


def test_supplied_requirement_survives_install_default_update_accept_and_portable_rollback(tmp_path):
    roots, variant, supplied = supplied_fork(tmp_path)
    applied(roots, install(roots, variant, tmp_path))
    incoming = tmp_path / "incoming"
    write_skill(incoming, "review", b"Run /external-review.\nNew upstream instruction.\n")
    update = plan_update(
        roots, variant, SourceSpec(str(incoming), "local"), selection=None, mappings={}, services=Services.local()
    )
    assert update.allowed, update
    accept = plan_accept(
        roots, variant, update.preview_id, review=None, intent=(), resolutions=(), services=Services.local()
    )
    applied(roots, accept)
    assert load_ledger(roots.state, variant).dependencies == (supplied,)
    assert load_ledger(roots.state, variant).unresolved == ()
    copied = applied(roots, install(roots, variant, tmp_path, mode="copy", project=tmp_path / "copy"))
    copy_id = copied.data["created_variants"][0]
    assert load_ledger(roots.state, copy_id).dependencies == (supplied,)
    # A portable transfer carries neither machine bindings nor journals.
    moved = tmp_path / "moved"
    moved.mkdir()
    shutil.move(roots.content, moved / "content")
    shutil.move(roots.state, moved / "state")
    roots = Roots(moved / "content", moved / "state")
    shutil.rmtree(roots.state / "bindings")
    shutil.rmtree(roots.state / "transactions")
    undo = plan_rollback(roots, variant, services=Services.local())
    applied(roots, undo)
    assert load_ledger(roots.state, variant).dependencies == (supplied,)
    assert read_status(roots, variant, services=Services.local()).allowed
    applied(roots, install(roots, variant, tmp_path, project=tmp_path / "new-client"))


def test_supply_does_not_satisfy_a_new_unrelated_requirement(tmp_path):
    roots, variant, _ = supplied_fork(tmp_path)
    incoming = tmp_path / "incoming"
    write_skill(incoming, "review", b"Run /external-review and /missing-review.\n")
    update = plan_update(
        roots, variant, SourceSpec(str(incoming), "local"), selection=None, mappings={}, services=Services.local()
    )
    assert not update.allowed
    assert any(f.code == "dependency.missing" and "missing-review" in f.message for f in update.findings)


@pytest.mark.parametrize("damage", ["missing", "retargeted", "foreign"])
def test_status_observes_shared_binding_damage_and_preserves_it(tmp_path, damage):
    roots, variant = forked(tmp_path)
    applied(roots, install(roots, variant, tmp_path))
    link = tmp_path / "client/.claude/skills/local-review"
    link.unlink()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "note").write_bytes(b"human work")
    if damage == "retargeted":
        Services.local().filesystem.link(str(foreign), link)
    elif damage == "foreign":
        link.mkdir()
        (link / "note").write_bytes(b"human work")
    status = read_status(roots, variant, services=Services.local())
    assert not status.allowed
    assert any(f.code == "binding." + damage and f.path == str(link) for f in status.findings)
    assert (foreign / "note").read_bytes() == b"human work"
    if damage == "missing":
        applied(roots, install(roots, variant, tmp_path))
        assert read_status(roots, variant, services=Services.local()).allowed
    else:
        assert not install(roots, variant, tmp_path).allowed
        assert (link / "note").read_bytes() == b"human work"


def test_adoption_reconciliation_retains_supply_and_unresolved_evidence(tmp_path):
    from plugin_fork_io.adoption import plan_adopt
    from plugin_fork_io.snapshots import capture

    roots = Roots(tmp_path / "content", tmp_path / "state")
    write_skill(roots.content, "review", b"Run /external-review.\n")
    supplied = Dependency(
        "required-skill", "external-review", "skills/review/SKILL.md", 5, 0, 0, "user", satisfied=True
    )
    missing = replace(supplied, target="missing", satisfied=False)
    chosen = replace(selection("review"), dependencies=(supplied, missing))
    adopted = plan_adopt(roots, chosen, base=None, evidence={}, services=Services.local())
    applied(roots, adopted)
    source = tmp_path / "source"
    write_skill(source, "review", b"Run /external-review.\n")
    spec = SourceSpec(str(source), "local")
    evidence = {
        "digest": capture(spec, (), services=Services.local()).digest,
        "declaration": "This is my original copy.",
        "mappings": [{"source": "skills/review", "destination": "skills/review"}],
    }
    reconciled = plan_adopt(
        roots,
        selection("review"),
        variant_id=adopted.variant_id,
        base=spec,
        evidence=evidence,
        services=Services.local(),
    )
    applied(roots, reconciled)
    ledger = load_ledger(roots.state, adopted.variant_id)
    assert ledger.dependencies == (supplied, missing)
    assert ledger.unresolved == (missing,)
    status = read_status(roots, adopted.variant_id, services=Services.local())
    assert status.data["unresolved"][0]["target"] == "missing"
    assert not install(roots, adopted.variant_id, tmp_path).allowed


def test_direct_binding_checks_missing_skill_file_and_ignores_siblings(tmp_path):
    roots, variant = forked(tmp_path)
    content = tmp_path / "client/.agents/skills"
    content.parent.mkdir(parents=True)
    shutil.move(roots.content, content)
    roots = Roots(content, roots.state)
    applied(roots, install(roots, variant, tmp_path, agents=("codex",)))
    sibling = content / "unrelated"
    sibling.mkdir()
    (sibling / "note").write_bytes(b"human work")
    assert read_status(roots, variant, services=Services.local()).allowed
    (content / "local-review/SKILL.md").unlink()
    status = read_status(roots, variant, services=Services.local())
    assert not status.allowed
    assert any(f.code == "binding.missing" and f.path.endswith("SKILL.md") for f in status.findings)
    assert (sibling / "note").read_bytes() == b"human work"


def test_status_checks_shared_outside_resources_and_inaccessible_links(tmp_path):
    from plugin_fork_io.machine import LocalFileSystem
    from plugin_fork_io.records import SourceMapping

    source = tmp_path / "source"
    write_skill(source, "review")
    (source / "shared").mkdir()
    (source / "shared/helper").write_bytes(b"resource")
    roots = Roots(tmp_path / "content", tmp_path / "state")
    chosen = replace(selection("review"), resources=(SourceMapping("shared", "shared"),))
    fork = plan_fork(SourceSpec(str(source), "local"), chosen, roots, intent=(), services=Services.local())
    applied(roots, fork)
    applied(roots, install(roots, fork.variant_id, tmp_path))
    link = tmp_path / "client/.claude/skills/shared"

    class InaccessibleFS(LocalFileSystem):
        def mode(self, path):
            if path == link:
                raise PermissionError("link cannot be inspected")
            return super().mode(path)

    status = read_status(roots, fork.variant_id, services=replace(Services.local(), filesystem=InaccessibleFS()))
    assert not status.allowed
    assert any(f.code == "binding.inaccessible" for f in status.findings)
    assert link.is_symlink()
    link.unlink()
    status = read_status(roots, fork.variant_id, services=Services.local())
    assert any(f.code == "binding.missing" and f.path == str(link) for f in status.findings)
    assert (roots.content / "shared/helper").read_bytes() == b"resource"


@pytest.mark.parametrize("damage", ["direct-link", "child-kind", "missing-owner"])
def test_status_preserves_replaced_direct_content_and_broken_shared_owner(tmp_path, damage):
    roots, variant = forked(tmp_path)
    if damage != "missing-owner":
        content = tmp_path / "client/.agents/skills"
        content.parent.mkdir(parents=True)
        shutil.move(roots.content, content)
        roots = Roots(content, roots.state)
    applied(roots, install(roots, variant, tmp_path, agents=("codex",)))
    skill = roots.content / "local-review"
    if damage == "direct-link":
        foreign = roots.content / "human-copy"
        skill.rename(foreign)
        Services.local().filesystem.link("human-copy", skill)
    elif damage == "child-kind":
        (skill / "SKILL.md").unlink()
        (skill / "SKILL.md").mkdir()
    else:
        shutil.rmtree(skill)
    status = read_status(roots, variant, services=Services.local())
    assert not status.allowed
    assert any(
        f.code == ("binding.missing" if damage == "missing-owner" else "binding.foreign") for f in status.findings
    )
    if damage == "direct-link":
        assert skill.is_symlink() and (foreign / "SKILL.md").is_file()
    elif damage == "child-kind":
        assert (skill / "SKILL.md").is_dir()
    else:
        assert not skill.exists()


@pytest.mark.parametrize("mode", ["direct", "shared"])
def test_ancillary_local_deletion_remains_update_and_accept_eligible(tmp_path, mode):
    source = tmp_path / "source"
    write_skill(source, "review")
    (source / "skills/review/notes.txt").write_bytes(b"editable ancillary notes")
    content = tmp_path / "client/.agents/skills" if mode == "direct" else tmp_path / "content"
    roots = Roots(content, tmp_path / "state")
    fork = plan_fork(SourceSpec(str(source), "local"), selection("review"), roots, intent=(), services=Services.local())
    applied(roots, fork)
    applied(roots, install(roots, fork.variant_id, tmp_path, agents=("codex",)))
    (content / "review/notes.txt").unlink()
    status = read_status(roots, fork.variant_id, services=Services.local())
    assert status.allowed, status
    assert {f.code for f in status.findings} == {"content.modified"}
    update = plan_update(
        roots, fork.variant_id, SourceSpec(str(source), "local"), selection=None, mappings={}, services=Services.local()
    )
    assert update.allowed, update
    accepted = plan_accept(
        roots, fork.variant_id, update.preview_id, review=None, intent=(), resolutions=(), services=Services.local()
    )
    applied(roots, accepted)
    assert not (content / "review/notes.txt").exists()
    assert read_status(roots, fork.variant_id, services=Services.local()).allowed


@pytest.mark.parametrize("owned_kind", ["skill", "directory-resource", "file-resource"])
@pytest.mark.parametrize("replacement", ["link", "kind"])
def test_shared_binding_refuses_replaced_owner_root_without_following_it(tmp_path, owned_kind, replacement):
    from plugin_fork_io.records import SourceMapping

    source = tmp_path / "source"
    write_skill(source, "review")
    resource = source / "shared"
    if owned_kind == "file-resource":
        resource.write_bytes(b"original resource")
    else:
        resource.mkdir()
        (resource / "helper").write_bytes(b"original resource")
    chosen = replace(selection("review"), resources=(SourceMapping("shared", "shared"),))
    roots = Roots(tmp_path / "content", tmp_path / "state")
    fork = plan_fork(SourceSpec(str(source), "local"), chosen, roots, intent=(), services=Services.local())
    applied(roots, fork)
    applied(roots, install(roots, fork.variant_id, tmp_path))
    owned = roots.content / ("review" if owned_kind == "skill" else "shared")
    discovery = tmp_path / "client/.claude/skills" / owned.name
    original_link = Services.local().filesystem.readlink(discovery)
    moved = tmp_path / "foreign"
    owned.rename(moved)
    if replacement == "link":
        Services.local().filesystem.link(str(moved), owned)
    elif owned_kind == "file-resource":
        owned.mkdir()
    else:
        owned.write_bytes(b"human replacement")
    status = read_status(roots, fork.variant_id, services=Services.local())
    assert not status.allowed, status
    assert any(f.code == "binding.foreign" and f.path == str(discovery) for f in status.findings)
    assert Services.local().filesystem.readlink(discovery) == original_link
    if replacement == "link":
        assert Services.local().filesystem.readlink(owned) == str(moved)
    elif owned_kind == "file-resource":
        assert owned.is_dir()
    else:
        assert owned.read_bytes() == b"human replacement"
    original = (
        moved / "SKILL.md"
        if owned_kind == "skill"
        else moved / "helper"
        if owned_kind == "directory-resource"
        else moved
    )
    assert original.read_bytes()


def test_shared_binding_allows_content_root_alias_outside_owned_components(tmp_path):
    roots, variant = forked(tmp_path)
    applied(roots, install(roots, variant, tmp_path))
    moved = tmp_path / "aliased-content"
    roots.content.rename(moved)
    Services.local().filesystem.link(str(moved), roots.content)
    status = read_status(roots, variant, services=Services.local())
    assert status.allowed, status
    assert not status.findings
    assert (moved / "local-review/SKILL.md").is_file()
