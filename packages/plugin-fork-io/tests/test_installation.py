from plugin_fork_io import Services, apply_preview, load_ledger
from plugin_fork_io.installation import plan_install
from test_updates import forked


def test_copy_keeps_original_upstream_but_has_independent_identity(tmp_path):
    roots, variant = forked(tmp_path)
    preview = plan_install(
        roots,
        variant,
        agents=("claude",),
        scope="project",
        mode="copy",
        project=tmp_path / "client",
        home=tmp_path / "home",
        destination=None,
        services=Services.local(),
    )
    result = apply_preview(roots.state, preview.preview_id, services=Services.local())
    copied_id = result.data["created_variants"][0]
    original = load_ledger(roots.state, variant)
    copied = load_ledger(roots.state, copied_id)
    assert copied_id != variant
    assert copied.source == original.source and copied.base_digest == original.base_digest
    assert copied.content_root != original.content_root


def install(roots, variant, tmp_path, **kwargs):
    return plan_install(
        roots,
        variant,
        agents=kwargs.pop("agents", ("claude",)),
        scope=kwargs.pop("scope", "project"),
        mode=kwargs.pop("mode", "shared"),
        project=kwargs.pop("project", tmp_path / "client"),
        home=kwargs.pop("home", tmp_path / "home"),
        destination=kwargs.pop("destination", None),
        services=kwargs.pop("services", Services.local()),
        **kwargs,
    )


def applied(roots, preview):
    assert preview.allowed, preview
    result = apply_preview(roots.state, preview.preview_id, services=Services.local())
    assert result.applied, result
    return result


def test_shared_links_observe_one_root_and_ignore_git_unchanged(tmp_path):
    from plugin_fork_io import read_status
    from plugin_fork_io.store import load_binding

    roots, variant = forked(tmp_path)
    preview = install(roots, variant, tmp_path, agents=("claude", "codex"))
    assert not (tmp_path / "client").exists()
    assert load_binding(roots.state, variant) is None
    applied(roots, preview)
    claude = tmp_path / "client/.claude/skills/local-review"
    codex = tmp_path / "client/.agents/skills/local-review"
    assert claude.is_symlink() and codex.is_symlink()
    assert not Services.local().filesystem.readlink(claude).startswith("/")
    (claude / "SKILL.md").write_bytes((claude / "SKILL.md").read_bytes() + b"shared edit\n")
    assert (codex / "SKILL.md").read_bytes() == (roots.content / "local-review/SKILL.md").read_bytes()
    assert read_status(roots, variant, services=Services.local()).allowed
    assert not (tmp_path / "client/.gitignore").exists()
    assert not (tmp_path / "client/.plugin-fork").exists()


def test_codex_pi_direct_shared_root_creates_no_content_copy(tmp_path):
    import shutil

    from plugin_fork_io import Roots
    from plugin_fork_io.store import load_binding

    roots, variant = forked(tmp_path)
    content = tmp_path / "client/.agents/skills"
    content.parent.mkdir(parents=True)
    shutil.move(roots.content, content)
    roots = Roots(content, roots.state)
    preview = install(roots, variant, tmp_path, agents=("codex", "pi"))
    result = applied(roots, preview)
    assert result.data["created_variants"] == []
    assert not (content / "local-review").is_symlink()
    assert not (tmp_path / "client/.pi").exists()
    assert len(load_binding(roots.state, variant).targets) == 2


def test_distinct_copy_roots_are_independent_and_identical_override_shares_owner(tmp_path):
    from plugin_fork_io import read_status

    roots, variant = forked(tmp_path)
    first = applied(roots, install(roots, variant, tmp_path, agents=("codex", "claude"), mode="copy"))
    ids = first.data["created_variants"]
    assert len(ids) == len(set(ids)) == 2
    (roots.content / "local-review/SKILL.md").write_bytes(b"source edit")
    assert b"source edit" not in (tmp_path / "client/.agents/skills/local-review/SKILL.md").read_bytes()
    from plugin_fork_io import Roots

    for copied in ids:
        ledger = load_ledger(roots.state, copied)
        assert read_status(
            Roots((roots.state / ledger.content_root).resolve(), roots.state), copied, services=Services.local()
        ).allowed
    # Use another independent source because changed SKILL metadata is invalid.
    other, other_id = forked(tmp_path / "other")
    result = applied(
        other,
        install(
            other, other_id, tmp_path / "other", agents=("codex", "pi"), mode="copy", destination=tmp_path / "override"
        ),
    )
    assert len(result.data["created_variants"]) == 1
    assert result.data["bindings"][0]["agents"] == ["codex", "pi"]


def test_personal_override_and_uncertain_copy(tmp_path):
    from helpers import selection, write_skill
    from plugin_fork_io import Roots, plan_adopt, read_status

    roots = Roots(tmp_path / "content", tmp_path / "state")
    write_skill(roots.content, "review")
    adopted = plan_adopt(roots, selection("review"), base=None, evidence={}, services=Services.local())
    applied(roots, adopted)
    preview = install(roots, adopted.variant_id, tmp_path, agents=("pi",), scope="personal", mode="copy")
    result = applied(roots, preview)
    copied = load_ledger(roots.state, result.data["created_variants"][0])
    assert copied.origin == "uncertain" and copied.base_digest is None
    destination = tmp_path / "home/.pi/agent/skills"
    assert (destination / "review/SKILL.md").is_file()
    assert read_status(Roots(destination, roots.state), copied.variant_id, services=Services.local()).allowed


def test_existing_foreign_link_and_case_collision_refuse(tmp_path):
    roots, variant = forked(tmp_path)
    target = tmp_path / "client/.claude/skills"
    target.mkdir(parents=True)
    Services.local().filesystem.link(str(roots.content / "local-review"), target / "local-review")
    preview = install(roots, variant, tmp_path)
    assert not preview.allowed
    (target / "local-review").unlink()
    (target / "LOCAL-REVIEW").mkdir()
    preview = install(roots, variant, tmp_path)
    assert not preview.allowed
    assert "binding.collision" in {f.code for f in preview.findings}


def test_missing_requirement_blocks_even_without_behavioral_review(tmp_path):
    roots, variant = forked(tmp_path)
    path = roots.content / "local-review/SKILL.md"
    path.write_bytes(path.read_bytes() + b"[required helper](absent.py)\n")
    preview = install(roots, variant, tmp_path)
    assert not preview.allowed
    assert "dependency.missing" in {f.code for f in preview.findings}


def test_source_new_file_and_discovery_new_collision_stale_previews(tmp_path):
    roots, variant = forked(tmp_path)
    preview = install(roots, variant, tmp_path, mode="copy")
    (roots.content / "local-review/new.txt").write_bytes(b"local addition")
    assert not apply_preview(roots.state, preview.preview_id, services=Services.local()).applied
    next_preview = install(roots, variant, tmp_path)
    collision = tmp_path / "client/.claude/skills/LOCAL-REVIEW"
    collision.mkdir(parents=True)
    assert not apply_preview(roots.state, next_preview.preview_id, services=Services.local()).applied


def test_shared_resources_are_linked_and_guarded(tmp_path):
    from dataclasses import replace

    from helpers import selection, write_skill
    from plugin_fork_io import Roots, SourceSpec, plan_fork
    from plugin_fork_io.records import SourceMapping

    source = tmp_path / "source"
    write_skill(source, "review")
    (source / "shared").mkdir()
    (source / "shared/helper").write_bytes(b"resource")
    roots = Roots(tmp_path / "content", tmp_path / "state")
    fork = plan_fork(
        SourceSpec(str(source), "local"),
        replace(selection("review"), resources=(SourceMapping("shared", "shared"),)),
        roots,
        intent=(),
        services=Services.local(),
    )
    applied(roots, fork)
    preview = install(roots, fork.variant_id, tmp_path)
    (roots.content / "shared/helper").write_bytes(b"changed")
    assert not apply_preview(roots.state, preview.preview_id, services=Services.local()).applied
    applied(roots, install(roots, fork.variant_id, tmp_path))
    assert (tmp_path / "client/.claude/skills/shared").is_symlink()
    assert (tmp_path / "client/.claude/skills/shared/helper").read_bytes() == b"changed"


def test_link_failure_recovers_only_staging_and_never_target(tmp_path):
    from dataclasses import replace

    from plugin_fork_io import plan_rollback
    from test_transactions import FailingFS

    roots, variant = forked(tmp_path)
    preview = install(roots, variant, tmp_path)
    before = (roots.content / "local-review/SKILL.md").read_bytes()
    result = apply_preview(
        roots.state,
        preview.preview_id,
        services=replace(Services.local(), filesystem=FailingFS(".plugin-fork-", "link", "after")),
    )
    assert not result.applied
    assert "binding.link-unavailable" in {f.code for f in result.findings}
    undo = plan_rollback(roots, variant, services=Services.local())
    applied(roots, undo)
    assert (roots.content / "local-review/SKILL.md").read_bytes() == before
    assert not (tmp_path / "client/.claude/skills/local-review").exists()


def test_explicit_relocation_binding_is_written_only_on_apply(tmp_path):
    import shutil

    from plugin_fork_io import Roots, read_status
    from plugin_fork_io.store import load_binding

    roots, variant = forked(tmp_path)
    ledger = load_ledger(roots.state, variant)
    moved = tmp_path / "independently-moved"
    shutil.move(roots.content, moved)
    relocated = Roots(moved, roots.state)
    assert not read_status(relocated, variant, services=Services.local()).allowed
    preview = install(relocated, variant, tmp_path)
    assert load_binding(roots.state, variant) is None
    applied(roots, preview)
    assert load_ledger(roots.state, variant) == ledger
    assert read_status(relocated, variant, services=Services.local()).allowed
    assert load_binding(roots.state, variant).content_root == str(moved)


def test_changed_relocation_identity_and_overlapping_copy_refuse(tmp_path):
    import shutil

    from plugin_fork_io import Roots

    roots, variant = forked(tmp_path)
    assert not install(roots, variant, tmp_path, mode="copy", destination=roots.content).allowed
    moved = tmp_path / "moved"
    shutil.move(roots.content, moved)
    (moved / "local-review/extra").write_bytes(b"unattested")
    assert not install(Roots(moved, roots.state), variant, tmp_path).allowed


def test_known_other_store_owner_is_checked_and_locked(tmp_path):
    from plugin_fork_io import DiscoveryContext

    other, _other_id = forked(tmp_path / "other")
    roots, variant = forked(tmp_path / "source")
    preview = install(
        roots, variant, tmp_path, destination=other.content, discovery=DiscoveryContext((), state_roots=(other.state,))
    )
    assert not preview.allowed
    preview = install(roots, variant, tmp_path, discovery=DiscoveryContext((), state_roots=(other.state,)))
    (other.state / "locks/ownership.lock").write_bytes(b"held by another process")
    result = apply_preview(roots.state, preview.preview_id, services=Services.local())
    assert not result.applied and "lock.present" in {f.code for f in result.findings}
    assert not (tmp_path / "client/.claude/skills/local-review").exists()


def test_configured_discovery_and_reserved_claude_name(tmp_path):
    from helpers import selection, write_skill
    from plugin_fork_io import DiscoveryContext, Roots, SourceSpec, plan_fork

    configured = tmp_path / "configured"
    (configured / "LOCAL-REVIEW").mkdir(parents=True)
    roots, variant = forked(tmp_path)
    assert not install(roots, variant, tmp_path, discovery=DiscoveryContext((configured,))).allowed
    source = tmp_path / "synced-source"
    write_skill(source, "synced")
    synced_roots = Roots(tmp_path / "synced-content", tmp_path / "synced-state")
    fork = plan_fork(
        SourceSpec(str(source), "local"), selection("synced"), synced_roots, intent=(), services=Services.local()
    )
    applied(synced_roots, fork)
    assert not install(synced_roots, fork.variant_id, tmp_path).allowed


def test_core_symlinks_false_is_reported_without_config_changes(tmp_path):
    roots, variant = forked(tmp_path)
    config = tmp_path / "client/.git/config"
    config.parent.mkdir(parents=True)
    original = b"[core]\n\tsymlinks = false\n"
    config.write_bytes(original)
    preview = install(roots, variant, tmp_path)
    assert "binding.git-symlinks-disabled" in {f.code for f in preview.findings}
    applied(roots, preview)
    assert config.read_bytes() == original


def test_cross_volume_relative_links_refuse_and_no_fallback(tmp_path, monkeypatch):
    from plugin_fork_io import installation

    roots, variant = forked(tmp_path)
    monkeypatch.setattr(installation, "portable_content_root", lambda *a, **k: None)
    preview = install(roots, variant, tmp_path)
    assert not preview.allowed
    assert {f.code for f in preview.findings} == {"binding.link-unavailable"}
    assert not (tmp_path / "client").exists()


def test_install_binding_survives_accept_and_rollback_copies_do_not_follow(tmp_path):
    from helpers import write_skill
    from plugin_fork_io import SourceSpec, plan_accept, plan_rollback, plan_update, read_status
    from plugin_fork_io.store import load_binding

    roots, variant = forked(tmp_path)
    applied(roots, install(roots, variant, tmp_path))
    copied = applied(roots, install(roots, variant, tmp_path, mode="copy", agents=("codex",)))
    assert copied.data["created_variants"]
    binding = load_binding(roots.state, variant)
    incoming = tmp_path / "incoming"
    write_skill(incoming, "review", b"upstream change\n")
    update = plan_update(
        roots, variant, SourceSpec(str(incoming), "local"), selection=None, mappings={}, services=Services.local()
    )
    accepted = plan_accept(
        roots, variant, update.preview_id, review=None, intent=(), resolutions=(), services=Services.local()
    )
    applied(roots, accepted)
    assert b"upstream change" in (tmp_path / "client/.claude/skills/local-review/SKILL.md").read_bytes()
    assert b"upstream change" not in (tmp_path / "client/.agents/skills/local-review/SKILL.md").read_bytes()
    applied(roots, plan_rollback(roots, variant, services=Services.local()))
    assert load_binding(roots.state, variant) == binding
    assert read_status(roots, variant, services=Services.local()).allowed


def test_copy_portable_history_needs_no_source_store_or_journal(tmp_path):
    import shutil

    from plugin_fork_io import Roots, read_status

    roots, variant = forked(tmp_path)
    result = applied(roots, install(roots, variant, tmp_path, mode="copy"))
    copied = result.data["created_variants"][0]
    new_state = tmp_path / "portable/state"
    new_content = tmp_path / "portable/client/.claude/skills"
    new_state.mkdir(parents=True)
    new_content.parent.mkdir(parents=True)
    shutil.move(tmp_path / "client/.claude/skills", new_content)
    # Preserve the same relative root relation for the independent fork record.
    new_state = tmp_path / "portable/state"
    (new_state / "forks").mkdir()
    shutil.copytree(roots.state / "forks" / copied, new_state / "forks" / copied)
    shutil.rmtree(roots.state)
    assert read_status(Roots(new_content, new_state), copied, services=Services.local()).allowed


def test_install_cli_and_common_binding_resolution(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    from plugin_fork_io.cli import app
    from typer.testing import CliRunner

    roots, variant = forked(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "install",
            variant,
            "--agent",
            "claude",
            "--mode",
            "copy",
            "--state-dir",
            str(roots.state),
            "--project",
            str(tmp_path / "client"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    preview = json.loads(result.output)
    result = runner.invoke(
        app, ["install", "--apply", preview["preview_id"], "--state-dir", str(roots.state), "--json"]
    )
    assert result.exit_code == 0, result.output
    copied = json.loads(result.output)["data"]["created_variants"][0]
    status = runner.invoke(app, ["status", copied, "--state-dir", str(roots.state), "--json"])
    assert status.exit_code == 0, status.output
    mixed = runner.invoke(
        app, ["install", "--apply", preview["preview_id"], "--mode", "copy", "--state-dir", str(roots.state)]
    )
    assert mixed.exit_code == 2


def test_shared_destination_cannot_nest_inside_source(tmp_path):
    roots, variant = forked(tmp_path)
    assert not install(roots, variant, tmp_path, destination=roots.content / "local-review/nested").allowed


def test_failed_install_recovery_locks_other_known_stores(tmp_path):
    from dataclasses import replace

    from plugin_fork_io import DiscoveryContext, plan_rollback
    from test_transactions import FailingFS

    roots, variant = forked(tmp_path / "source")
    other, _ = forked(tmp_path / "other")
    preview = install(roots, variant, tmp_path, discovery=DiscoveryContext((), state_roots=(other.state,)))
    result = apply_preview(
        roots.state,
        preview.preview_id,
        services=replace(Services.local(), filesystem=FailingFS(".plugin-fork-", "link", "after")),
    )
    assert not result.applied
    undo = plan_rollback(roots, variant, services=Services.local())
    assert undo.allowed, undo
    (other.state / "locks/ownership.lock").write_bytes(b"another writer")
    assert not apply_preview(roots.state, undo.preview_id, services=Services.local()).applied
