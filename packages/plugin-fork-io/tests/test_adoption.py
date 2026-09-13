import shutil

import pytest
from helpers import selection, write_skill
from plugin_fork_io import Roots, Services, SourceSpec, apply_preview
from plugin_fork_io.adoption import plan_adopt
from plugin_fork_io.snapshots import capture
from plugin_fork_io.status import read_status
from plugin_fork_io.store import load_ledger


def test_unknown_adoption_never_calls_existing_copy_upstream(tmp_path):
    roots = Roots(tmp_path / "client", tmp_path / "external")
    skill = write_skill(roots.content, "review")
    before = skill.read_bytes()
    preview = plan_adopt(roots, selection("review"), base=None, evidence={}, services=Services.local())
    assert preview.allowed, preview
    result = apply_preview(roots.state, preview.preview_id, services=Services.local())
    assert result.applied, result
    assert skill.read_bytes() == before
    ledger = load_ledger(roots.state, result.variant_id)
    assert ledger.origin == "uncertain" and ledger.base_digest is None
    assert ledger.mappings[0].destination == "skills/review"
    tracking = roots.state / "forks" / result.variant_id
    assert not (tracking / "base.tar.gz").exists()
    assert (tracking / "observation.tar.gz").exists()
    assert read_status(roots, result.variant_id, services=Services.local()).allowed
    assert sorted(p.relative_to(roots.content).as_posix() for p in roots.content.rglob("*")) == [
        "skills",
        "skills/review",
        "skills/review/SKILL.md",
    ]


def test_known_reconciliation_preserves_identity_intent_and_content(tmp_path):
    roots = Roots(tmp_path / "content", tmp_path / "state")
    skill = write_skill(roots.content, "review", b"Local edits.\n")
    initial = plan_adopt(
        roots, selection("review"), base=None, evidence={}, intent=("Keep local",), services=Services.local()
    )
    assert apply_preview(roots.state, initial.preview_id, services=Services.local()).applied
    source = tmp_path / "source"
    write_skill(source, "review")
    base = SourceSpec(str(source), "local")
    digest = capture(base, (), services=Services.local()).digest
    evidence = {
        "digest": digest,
        "declaration": "I copied this original and edited it.",
        "mappings": [{"source": "skills/review", "destination": "skills/review"}],
    }
    before = skill.read_bytes()
    preview = plan_adopt(
        roots,
        selection("review"),
        variant_id=initial.variant_id,
        base=base,
        evidence=evidence,
        services=Services.local(),
    )
    assert preview.allowed, preview
    assert preview.data["local_deltas"]
    assert load_ledger(roots.state, initial.variant_id).origin == "uncertain"
    result = apply_preview(roots.state, preview.preview_id, services=Services.local())
    assert result.applied, result
    ledger = load_ledger(roots.state, result.variant_id)
    assert ledger.variant_id == initial.variant_id and ledger.generation == 2
    assert ledger.origin == "known" and ledger.intent == ("Keep local",)
    assert skill.read_bytes() == before
    shutil.rmtree(source)
    shutil.rmtree(roots.state / "transactions")
    moved = tmp_path / "moved"
    moved.mkdir()
    shutil.move(roots.content, moved / "content")
    shutil.move(roots.state, moved / "state")
    status = read_status(Roots(moved / "content", moved / "state"), result.variant_id, services=Services.local())
    assert status.allowed, status


def test_known_adoption_requires_original_evidence(tmp_path):
    roots = Roots(tmp_path / "content", tmp_path / "state")
    write_skill(roots.content, "review")
    result = plan_adopt(
        roots, selection("review"), base=SourceSpec(str(roots.content), "local"), evidence={}, services=Services.local()
    )
    assert not result.allowed
    assert not roots.state.exists()


def test_adoption_rejects_stale_content_and_overlapping_owner(tmp_path):
    from helpers import codes

    roots = Roots(tmp_path / "content", tmp_path / "state")
    skill = write_skill(roots.content, "review")
    preview = plan_adopt(roots, selection("review"), base=None, evidence={}, services=Services.local())
    skill.write_bytes(skill.read_bytes() + b"changed after preview\n")
    assert "preview.stale" in codes(apply_preview(roots.state, preview.preview_id, services=Services.local()))
    fresh = plan_adopt(roots, selection("review"), base=None, evidence={}, services=Services.local())
    assert apply_preview(roots.state, fresh.preview_id, services=Services.local()).applied
    overlap = plan_adopt(roots, selection("review"), base=None, evidence={}, services=Services.local())
    assert not overlap.allowed
    assert "overlap" in overlap.findings[0].message.lower()


def test_known_adoption_compares_original_mapping_without_rewriting(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.records import Component

    roots = Roots(tmp_path / "content", tmp_path / "state")
    skill = write_skill(roots.content, "local-review", b"locally renamed and edited\n")
    source = tmp_path / "upstream"
    write_skill(source, "review")
    base = SourceSpec(str(source), "local")
    evidence = {
        "digest": capture(base, (), services=Services.local()).digest,
        "declaration": "Original before my rename",
        "mappings": [{"source": "skills/review", "destination": "skills/local-review"}],
    }
    selected = replace(selection("review"), skills=(Component("skills/local-review", "local-review"),))
    before = skill.read_bytes()
    prepared = plan_adopt(roots, selected, base=base, evidence=evidence, services=Services.local())
    assert prepared.allowed, prepared
    assert "skills/local-review/SKILL.md" in prepared.data["local_deltas"]
    assert apply_preview(roots.state, prepared.preview_id, services=Services.local()).applied
    ledger = load_ledger(roots.state, prepared.variant_id)
    assert ledger.components[0].source == "skills/review"
    assert ledger.mappings[0].destination == "skills/local-review"
    assert skill.read_bytes() == before


def test_tracking_only_failure_uses_existing_recovery_engine(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.recovery import plan_rollback
    from test_transactions import FailingFS

    roots = Roots(tmp_path / "content", tmp_path / "state")
    skill = write_skill(roots.content, "review")
    before = skill.read_bytes()
    prepared = plan_adopt(roots, selection("review"), base=None, evidence={}, services=Services.local())
    result = apply_preview(
        roots.state,
        prepared.preview_id,
        services=replace(Services.local(), filesystem=FailingFS("ledger.json", when="after")),
    )
    assert not result.applied
    recovery = plan_rollback(roots, prepared.variant_id, services=Services.local())
    assert recovery.allowed, recovery
    assert apply_preview(roots.state, recovery.preview_id, services=Services.local()).applied
    assert skill.read_bytes() == before
    assert not (roots.state / "forks" / prepared.variant_id).exists()


def test_portable_fork_with_retained_completed_journal(tmp_path):
    from test_transactions import prepared_fork

    roots, prepared = prepared_fork(tmp_path)
    assert apply_preview(roots.state, prepared.preview_id, services=Services.local()).applied
    relocated = tmp_path / "relocated"
    relocated.mkdir()
    shutil.move(roots.content, relocated / "content")
    shutil.move(roots.state, relocated / "state")
    status = read_status(
        Roots(relocated / "content", relocated / "state"), prepared.variant_id, services=Services.local()
    )
    assert status.allowed, status


def test_cli_adopt_and_status_resolve_config_without_client_files(tmp_path, monkeypatch):
    import json

    from plugin_fork_io.cli import app
    from plugin_fork_io.previews import _encode
    from typer.testing import CliRunner

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "absent-config"))
    client = tmp_path / "client"
    write_skill(client, "review")
    config = tmp_path / "config.json"
    config.write_bytes(
        json.dumps({"schema_version": 1, "defaults": {"content_dir": "client", "state_dir": "external"}}).encode()
    )
    selected = tmp_path / "selection.json"
    selected.write_bytes(json.dumps(_encode(selection("review"))).encode())
    runner = CliRunner()
    result = runner.invoke(app, ["adopt", "--config", str(config), "--selection", str(selected), "--json"])
    assert result.exit_code == 0, result.output
    preview = json.loads(result.output)
    assert (
        runner.invoke(app, ["adopt", "--config", str(config), "--apply", preview["preview_id"], "--json"]).exit_code
        == 0
    )
    status = runner.invoke(app, ["status", preview["variant_id"], "--config", str(config), "--json"])
    assert status.exit_code == 0, status.output
    assert not (tmp_path / "absent-config").exists()
    assert not (client / ".plugin-fork").exists()
    malformed = tmp_path / "bad.json"
    malformed.write_bytes(b"{")
    rejected = runner.invoke(app, ["status", preview["variant_id"], "--config", str(malformed), "--json"])
    assert rejected.exit_code == 2
    assert json.loads(rejected.output)["findings"][0]["code"] == "config.invalid"


def test_external_null_binding_requires_explicit_cli_content(tmp_path, monkeypatch):
    import json

    from plugin_fork_io.cli import app
    from typer.testing import CliRunner

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-config"))
    monkeypatch.setattr("plugin_fork_io.adoption.portable_content_root", lambda *args, **kwargs: None)
    roots = Roots(tmp_path / "client", tmp_path / "external")
    write_skill(roots.content, "review")
    prepared = plan_adopt(roots, selection("review"), base=None, evidence={}, services=Services.local())
    assert apply_preview(roots.state, prepared.preview_id, services=Services.local()).applied
    assert load_ledger(roots.state, prepared.variant_id).content_root is None
    runner = CliRunner()
    missing = runner.invoke(app, ["status", prepared.variant_id, "--state-dir", str(roots.state), "--json"])
    assert missing.exit_code == 3
    assert json.loads(missing.output)["findings"][0]["code"] == "tracking.binding"
    supplied = runner.invoke(
        app,
        ["status", prepared.variant_id, "--state-dir", str(roots.state), "--content-dir", str(roots.content), "--json"],
    )
    assert supplied.exit_code == 0, supplied.output


def test_reconciliation_can_clear_intent_only_when_explicit(tmp_path):
    roots = Roots(tmp_path / "content", tmp_path / "state")
    write_skill(roots.content, "review")
    initial = plan_adopt(
        roots, selection("review"), base=None, evidence={}, intent=("old note",), services=Services.local()
    )
    assert apply_preview(roots.state, initial.preview_id, services=Services.local()).applied
    source = SourceSpec(str(roots.content), "local")
    evidence = {
        "digest": capture(source, (), services=Services.local()).digest,
        "declaration": "These are the original bytes.",
        "mappings": [{"source": "skills/review", "destination": "skills/review"}],
    }
    reconcile = plan_adopt(
        roots,
        selection("review"),
        base=source,
        evidence=evidence,
        variant_id=initial.variant_id,
        intent=(),
        services=Services.local(),
    )
    assert apply_preview(roots.state, reconcile.preview_id, services=Services.local()).applied
    assert load_ledger(roots.state, initial.variant_id).intent == ()


def test_portable_history_cannot_omit_its_ledger_after_image(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.status import _attestation
    from plugin_fork_io.store import sealed_bytes

    roots = Roots(tmp_path / "content", tmp_path / "state")
    write_skill(roots.content, "review")
    prepared = plan_adopt(roots, selection("review"), base=None, evidence={}, services=Services.local())
    assert apply_preview(roots.state, prepared.preview_id, services=Services.local()).applied
    history = _attestation(roots.state, prepared.variant_id)
    history_path = roots.state / "forks" / prepared.variant_id / "history" / (prepared.preview_id + ".json")
    history_path.write_bytes(sealed_bytes(replace(history, tracking=())))
    shutil.rmtree(roots.state / "transactions")
    assert not read_status(roots, prepared.variant_id, services=Services.local()).allowed


@pytest.mark.parametrize("explicit_selection", [False, True])
def test_cli_reconciliation_preserves_declared_missing_requirements(tmp_path, monkeypatch, explicit_selection):
    import json
    from dataclasses import replace

    from plugin_fork_io.cli import app
    from plugin_fork_io.previews import _encode
    from plugin_fork_io.records import Dependency
    from typer.testing import CliRunner

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-config"))
    roots = Roots(tmp_path / "content", tmp_path / "state")
    write_skill(roots.content, "review")
    missing = Dependency("required-skill", "missing-review-helper", "skills/review/SKILL.md", 1, 0, 0, "user")
    selected = replace(selection("review"), dependencies=(missing,))
    initial = plan_adopt(roots, selected, base=None, evidence={}, services=Services.local())
    assert apply_preview(roots.state, initial.preview_id, services=Services.local()).applied
    assert load_ledger(roots.state, initial.variant_id).unresolved == (missing,)
    base = SourceSpec(str(roots.content), "local")
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(
        json.dumps(
            {
                "digest": capture(base, (), services=Services.local()).digest,
                "declaration": "These are the original bytes.",
                "mappings": [{"source": "skills/review", "destination": "skills/review"}],
            }
        ).encode()
    )
    runner = CliRunner()
    selection_options = []
    if explicit_selection:
        selection_path = tmp_path / "selection.json"
        selection_path.write_bytes(json.dumps(_encode(selection("review"))).encode())
        selection_options = ["--selection", str(selection_path), "--content-dir", str(roots.content)]
    prepared = runner.invoke(
        app,
        [
            "adopt",
            *selection_options,
            "--variant",
            initial.variant_id,
            "--base",
            base.locator,
            "--evidence",
            str(evidence),
            "--state-dir",
            str(roots.state),
            "--json",
        ],
    )
    assert prepared.exit_code == 0, prepared.output
    preview_id = json.loads(prepared.output)["preview_id"]
    applied = runner.invoke(app, ["adopt", "--apply", preview_id, "--state-dir", str(roots.state), "--json"])
    assert applied.exit_code == 0, applied.output
    ledger = load_ledger(roots.state, initial.variant_id)
    assert ledger.origin == "known"
    assert ledger.unresolved == (missing,)


def test_documented_fork_directory_transfers_all_portable_tracking(tmp_path):
    from test_transactions import prepared_fork

    roots, prepared = prepared_fork(tmp_path)
    assert apply_preview(roots.state, prepared.preview_id, services=Services.local()).applied
    portable = roots.state / "forks" / prepared.variant_id
    assert (portable / "ledger.json").is_file()
    assert (portable / "base.tar.gz").is_file()
    assert (portable / "history" / (prepared.preview_id + ".json")).is_file()
    target = tmp_path / "transfer"
    target.mkdir()
    shutil.copytree(roots.content, target / "content")
    shutil.copytree(portable, target / "state" / "forks" / prepared.variant_id)
    assert read_status(
        Roots(target / "content", target / "state"), prepared.variant_id, services=Services.local()
    ).allowed
    assert not (roots.state / "variants").exists()
