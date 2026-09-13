from pathlib import Path

from helpers import write_skill
from plugin_fork_io import Services, SourceSpec, apply_preview, load_ledger, load_preview, read_snapshot
from plugin_fork_io.acceptance import plan_accept
from plugin_fork_io.updates import plan_update
from test_updates import forked


def prepared(tmp_path):
    roots, variant = forked(tmp_path)
    incoming = tmp_path / "incoming"
    write_skill(incoming, "review", b"New upstream.\n")
    update = plan_update(
        roots, variant, SourceSpec(str(incoming), "local"), selection=None, mappings={}, services=Services.local()
    )
    return roots, variant, update


def accept(roots, variant, update, **kwargs):
    return plan_accept(
        roots,
        variant,
        update.preview_id,
        review=kwargs.get("review"),
        intent=kwargs.get("intent", ()),
        resolutions=kwargs.get("resolutions", ()),
        services=Services.local(),
    )


def test_accept_advances_unadapted_base_and_retains_final_local_name(tmp_path):
    roots, variant, update = prepared(tmp_path)
    accepted = accept(roots, variant, update)
    assert accepted.allowed, accepted
    assert load_preview(roots.state, accepted.preview_id).review_state == "not_requested"
    applied = apply_preview(roots.state, accepted.preview_id, services=Services.local())
    assert applied.applied, applied
    assert load_ledger(roots.state, variant).generation == 2
    assert b"name: local-review" in (roots.content / "local-review/SKILL.md").read_bytes()
    base = read_snapshot(roots.state / "forks" / variant / "base.tar.gz")
    assert b"name: review" in next(e.content for e in base.entries if e.path.endswith("SKILL.md"))


def test_original_candidate_edit_after_accept_preview_refuses(tmp_path):
    roots, variant, update = prepared(tmp_path)
    accepted = accept(roots, variant, update)
    (Path(update.data["candidate_path"]) / "local-review/SKILL.md").write_bytes(b"changed")
    result = apply_preview(roots.state, accepted.preview_id, services=Services.local())
    assert not result.applied


def test_fresh_accept_uses_candidate_revisions_and_revalidates_requirements(tmp_path):
    roots, variant, update = prepared(tmp_path)
    candidate = Path(update.data["candidate_path"]) / "local-review/SKILL.md"
    candidate.write_bytes(candidate.read_bytes() + b"[required helper](missing.py)\n")
    blocked = accept(roots, variant, update)
    assert not blocked.allowed
    assert "dependency.missing" in {f.code for f in blocked.findings}
    (candidate.parent / "missing.py").write_bytes(b"# inert helper\n")
    accepted = accept(roots, variant, update)
    assert accepted.allowed, accepted
    assert apply_preview(roots.state, accepted.preview_id, services=Services.local()).applied


def test_maintained_edit_or_new_file_after_update_refuses(tmp_path):
    roots, variant, update = prepared(tmp_path)
    (roots.content / "local-review/new.txt").write_bytes(b"human addition")
    assert not accept(roots, variant, update).allowed


def test_conflict_requires_explicit_matching_resolution(tmp_path):
    from hashlib import sha256

    from plugin_fork_io import Resolution

    roots, variant = forked(tmp_path)
    (roots.content / "local-review/binary").write_bytes(b"\0local")
    incoming = tmp_path / "incoming"
    write_skill(incoming, "review")
    (incoming / "skills/review/binary").write_bytes(b"\0upstream")
    update = plan_update(
        roots, variant, SourceSpec(str(incoming), "local"), selection=None, mappings={}, services=Services.local()
    )
    conflict = load_preview(roots.state, update.preview_id).conflicts[0]
    assert not accept(roots, variant, update).allowed
    resolution = Resolution(conflict.id, conflict.path, sha256(b"\0local").hexdigest())
    accepted = accept(roots, variant, update, resolutions=(resolution,))
    assert accepted.allowed, accepted
    assert apply_preview(roots.state, accepted.preview_id, services=Services.local()).applied


def test_review_preserves_findings_and_binds_final_candidate(tmp_path):
    from plugin_fork_io import Finding, Review, capture

    roots, variant, update = prepared(tmp_path)
    snapshot = capture(SourceSpec(update.data["candidate_path"], "local"), (), services=Services.local())
    review = Review(
        snapshot.digest, (Finding("behavior.checked", "error", None, None, "Keep the approval gate; reconciled"),)
    )
    accepted = accept(roots, variant, update, review=review)
    assert accepted.allowed, accepted
    assert load_preview(roots.state, accepted.preview_id).review_state == "completed_with_findings"
    assert any(f.message == review.findings[0].message for f in accepted.findings)
    candidate = Path(update.data["candidate_path"]) / "local-review/SKILL.md"
    candidate.write_bytes(candidate.read_bytes() + b"Later edit\n")
    assert not accept(roots, variant, update, review=review).allowed


def test_candidate_partial_selection_refuses(tmp_path):
    roots, variant, update = prepared(tmp_path)
    (Path(update.data["candidate_path"]) / "local-review/SKILL.md").unlink()
    assert not accept(roots, variant, update).allowed


def test_review_file_changes_require_new_accept_preview(tmp_path):
    import json

    from plugin_fork_io import Review, capture
    from plugin_fork_io.cli import app
    from plugin_fork_io.store import record_bytes
    from typer.testing import CliRunner

    roots, variant, update = prepared(tmp_path)
    snapshot = capture(SourceSpec(update.data["candidate_path"], "local"), (), services=Services.local())
    review_file = tmp_path / "review.json"
    review_file.write_bytes(record_bytes(Review(snapshot.digest, ())))
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "accept",
            variant,
            "--candidate",
            update.preview_id,
            "--review",
            str(review_file),
            "--state-dir",
            str(roots.state),
            "--content-dir",
            str(roots.content),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    preview_id = json.loads(result.output)["preview_id"]
    review_file.write_bytes(review_file.read_bytes() + b" ")
    applied = runner.invoke(app, ["accept", "--apply", preview_id, "--state-dir", str(roots.state), "--json"])
    assert applied.exit_code == 3, applied.output
    assert not json.loads(applied.output)["applied"]


def test_git_commit_does_not_stale_acceptance(tmp_path):
    import subprocess

    roots, variant, update = prepared(tmp_path)
    accepted = accept(roots, variant, update)
    subprocess.run(["git", "init", "--quiet", str(roots.content)], check=True)
    subprocess.run(["git", "-C", str(roots.content), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(roots.content),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "local content",
        ],
        check=True,
    )
    result = apply_preview(roots.state, accepted.preview_id, services=Services.local())
    assert result.applied, result


def test_new_live_file_after_accept_preparation_refuses(tmp_path):
    roots, variant, update = prepared(tmp_path)
    accepted = accept(roots, variant, update)
    (roots.content / "local-review/new-file").write_bytes(b"new work")
    assert not apply_preview(roots.state, accepted.preview_id, services=Services.local()).applied
    assert (roots.content / "local-review/new-file").read_bytes() == b"new work"


def test_immutable_accepted_candidate_edit_refuses(tmp_path):
    roots, variant, update = prepared(tmp_path)
    accepted = accept(roots, variant, update)
    (Path(accepted.data["candidate_path"]) / "local-review/SKILL.md").write_bytes(b"edited after authorization")
    assert not apply_preview(roots.state, accepted.preview_id, services=Services.local()).applied


def test_mapping_acceptance_supports_next_update_and_rollback(tmp_path):
    from plugin_fork_io import plan_rollback
    from test_updates import fork_with_helper

    roots, variant, incoming = fork_with_helper(tmp_path)
    (incoming / "skills/review/helper.txt").rename(incoming / "skills/review/new.txt")
    update = plan_update(
        roots,
        variant,
        SourceSpec(str(incoming), "local"),
        selection=None,
        mappings={"skills/review/helper.txt": "skills/review/new.txt"},
        services=Services.local(),
    )
    accepted = accept(roots, variant, update)
    assert accepted.allowed, accepted
    result = apply_preview(roots.state, accepted.preview_id, services=Services.local())
    assert result.applied, result
    second = plan_update(
        roots, variant, SourceSpec(str(incoming), "local"), selection=None, mappings={}, services=Services.local()
    )
    assert second.allowed, second
    undo = plan_rollback(roots, variant, services=Services.local())
    assert apply_preview(roots.state, undo.preview_id, services=Services.local()).applied
    assert not accept(roots, variant, second).allowed


def test_acceptance_keeps_shared_views_and_independent_content(tmp_path):
    import shutil

    from plugin_fork_io import plan_rollback

    roots, variant, update = prepared(tmp_path)
    shared = tmp_path / "shared-view"
    Services.local().filesystem.link(str(roots.content / "local-review"), shared)
    copy = tmp_path / "independent"
    shutil.copytree(roots.content, copy)
    independent = (copy / "local-review/SKILL.md").read_bytes()
    accepted = accept(roots, variant, update)
    assert apply_preview(roots.state, accepted.preview_id, services=Services.local()).applied
    assert b"New upstream" in (shared / "SKILL.md").read_bytes()
    assert (copy / "local-review/SKILL.md").read_bytes() == independent
    undo = plan_rollback(roots, variant, services=Services.local())
    assert apply_preview(roots.state, undo.preview_id, services=Services.local()).applied
    assert shared.is_symlink()
    assert (shared / "SKILL.md").read_bytes() == independent


def test_portable_history_retains_original_review_and_resolution_evidence(tmp_path):
    import shutil

    from plugin_fork_io import Finding, Review, capture
    from plugin_fork_io.status import _attestation

    roots, variant, update = prepared(tmp_path)
    final = capture(SourceSpec(update.data["candidate_path"], "local"), (), services=Services.local())
    review = Review(final.digest, (Finding("behavior", "error", None, None, "Reconciled finding remains recorded"),))
    accepted = accept(roots, variant, update, review=review)
    assert apply_preview(roots.state, accepted.preview_id, services=Services.local()).applied
    shutil.rmtree(roots.state / "transactions")
    shutil.rmtree(roots.state / "previews")
    assert _attestation(roots.state, variant).review == review
