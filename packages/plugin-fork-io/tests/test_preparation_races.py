"""A preview cannot authorize bytes that changed during its own preparation."""

from dataclasses import replace
from pathlib import Path

import pytest
from plugin_fork_io import Services, apply_preview, load_ledger
from plugin_fork_io.acceptance import plan_accept
from test_acceptance import prepared


@pytest.mark.parametrize("target", ["maintained", "tracking", "candidate", "review"])
def test_acceptance_detects_changes_while_writing_private_candidate(tmp_path, target):
    import json

    from plugin_fork_io import Review, SourceSpec
    from plugin_fork_io.snapshots import capture
    from plugin_fork_io.store import record_bytes

    roots, variant, update = prepared(tmp_path)
    ledger = load_ledger(roots.state, variant)
    source_candidate = Path(update.data["candidate_path"])
    review = None
    review_file = tmp_path / "review.json"
    if target == "review":
        digest = capture(SourceSpec(str(source_candidate), "local"), (), services=Services.local()).digest
        review = Review(digest, ())
        review_file.write_bytes(record_bytes(review))
        review = replace(review, evidence_path=str(review_file))
    fs = Services.local().filesystem
    changed = []

    class ConcurrentEdit:
        def __getattr__(self, name):
            return getattr(fs, name)

        def write_exclusive(self, path, content):
            fs.write_exclusive(path, content)
            if changed or "candidate" not in path.parts:
                return
            changed.append(True)
            if target == "maintained":
                (roots.content / "local-review/new-local").write_bytes(b"concurrent human edit")
            elif target == "tracking":
                path = roots.state / "forks" / variant / "ledger.json"
                value = json.loads(path.read_bytes())
                value["intent"] = ["concurrent tracking change"]
                path.write_bytes(json.dumps(value).encode())
            elif target == "candidate":
                (source_candidate / "local-review/new-candidate").write_bytes(b"new candidate edit")
            else:
                value = json.loads(review_file.read_bytes())
                value["findings"] = [
                    {"code": "new.finding", "severity": "warn", "path": None, "line": None, "message": "Changed review"}
                ]
                review_file.write_bytes(json.dumps(value).encode())

    result = plan_accept(
        roots,
        variant,
        update.preview_id,
        review=review,
        intent=(),
        resolutions=(),
        services=replace(Services.local(), filesystem=ConcurrentEdit()),
    )
    assert changed and not result.allowed and not result.applied
    assert load_ledger(roots.state, variant).generation == ledger.generation
    assert result.findings[0].code == "accept.invalid"


@pytest.mark.parametrize("case", ["unowned-file", "candidate-link"])
def test_acceptance_refuses_candidate_ownership_expansion(tmp_path, case):
    roots, variant, update = prepared(tmp_path)
    candidate = Path(update.data["candidate_path"])
    if case == "unowned-file":
        (candidate / "unowned").write_bytes(b"outside selection")
    else:
        (candidate / "local-review/link").symlink_to("SKILL.md")
    result = plan_accept(
        roots, variant, update.preview_id, review=None, intent=(), resolutions=(), services=Services.local()
    )
    assert not result.allowed and not result.applied
    assert not (roots.content / "unowned").exists()


@pytest.mark.parametrize("case", ["missing-before-ledger", "corrupt-history"])
def test_portable_rollback_refuses_unattested_before_tracking(tmp_path, case):
    from plugin_fork_io import plan_rollback
    from test_acceptance import accept
    from test_persistence_guards import reseal_record

    roots, variant, update = prepared(tmp_path)
    acceptance = accept(roots, variant, update)
    assert apply_preview(roots.state, acceptance.preview_id, services=Services.local()).applied
    path = roots.state / "forks" / variant / "history" / (acceptance.preview_id + ".json")
    if case == "missing-before-ledger":
        reseal_record(path, lambda data: data.__setitem__("before_tracking", []))
    else:
        path.write_bytes(b"{}")
    current = (roots.content / "local-review/SKILL.md").read_bytes()
    result = plan_rollback(roots, variant, services=Services.local())
    assert not result.allowed and not result.applied
    assert (roots.content / "local-review/SKILL.md").read_bytes() == current


@pytest.mark.parametrize("changed", ["source", "copy"])
def test_install_final_verification_detects_post_promotion_edits(tmp_path, changed):
    from plugin_fork_io import load_preview
    from plugin_fork_io.installation import verify_install_content
    from plugin_fork_io.transactions import TransactionError
    from test_installation import install
    from test_updates import forked

    roots, variant = forked(tmp_path)
    result = install(roots, variant, tmp_path, mode="copy")
    preview = load_preview(roots.state, result.preview_id)
    assert apply_preview(roots.state, result.preview_id, services=Services.local()).applied
    target = roots.content if changed == "source" else Path(preview.installation_targets[0].root)
    human_file = target / "local-review/added-after-promotion"
    human_file.write_bytes(b"human bytes")
    with pytest.raises(TransactionError, match=r"snapshot|inventory") as error:
        verify_install_content(preview, services=Services.local())
    assert error.value.code == "transaction.verify"
    assert human_file.read_bytes() == b"human bytes"


@pytest.mark.parametrize("changed", ["candidate", "artifact", "destination"])
def test_install_change_planning_refuses_drift_before_live_promotion(tmp_path, changed):
    from plugin_fork_io import load_preview
    from plugin_fork_io.installation import installation_changes
    from test_installation import install
    from test_updates import forked

    roots, variant = forked(tmp_path)
    result = install(roots, variant, tmp_path, mode="copy")
    preview = load_preview(roots.state, result.preview_id)
    before = (roots.content / "local-review/SKILL.md").read_bytes()
    if changed == "candidate":
        (Path(preview.candidate_path) / "local-review/SKILL.md").write_bytes(b"tampered")
    elif changed == "artifact":
        (Path(preview.candidate_path).parent / preview.artifacts[0].path).write_bytes(b"tampered")
    else:
        root = Path(preview.installation_targets[0].root)
        root.mkdir(parents=True)
        (root / "local-review").mkdir()
    with pytest.raises(ValueError):
        installation_changes(preview, services=Services.local())
    assert (roots.content / "local-review/SKILL.md").read_bytes() == before


@pytest.mark.parametrize("change", ["generation", "operations"])
def test_install_revalidation_refuses_changed_owner_generation_or_operations(tmp_path, change):
    from plugin_fork_io import load_preview
    from plugin_fork_io.installation import revalidate_install
    from test_installation import install
    from test_updates import forked

    roots, variant = forked(tmp_path)
    result = install(roots, variant, tmp_path)
    preview = load_preview(roots.state, result.preview_id)
    if change == "generation":
        preview = replace(preview, owner_observations=((str(roots.state), variant, 99),))
    else:
        preview = replace(preview, operations=())
    with pytest.raises(ValueError):
        revalidate_install(preview, services=Services.local())
    assert not (tmp_path / "client").exists()
