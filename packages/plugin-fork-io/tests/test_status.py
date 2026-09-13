import pytest
from plugin_fork_io import Roots, Services
from plugin_fork_io.status import read_status


def test_status_missing_store_is_read_only(tmp_path):
    roots = Roots(tmp_path / "content", tmp_path / "state")
    result = read_status(roots, "missing", services=Services.local())
    assert not result.allowed
    assert not roots.state.exists()


def test_direct_edits_and_git_commit_do_not_advance_tracking(tmp_path):
    import subprocess

    from plugin_fork_io.store import load_ledger
    from plugin_fork_io.transactions import apply_preview
    from test_transactions import prepared_fork

    roots, prepared = prepared_fork(tmp_path)
    assert apply_preview(roots.state, prepared.preview_id, services=Services.local()).applied
    tracking = roots.state / "forks" / prepared.variant_id
    before = {p.relative_to(tracking).as_posix(): p.read_bytes() for p in tracking.rglob("*") if p.is_file()}
    target = roots.content / "review/SKILL.md"
    target.write_bytes(target.read_bytes() + b"Local workflow edit.\n")
    subprocess.run(["git", "init", "-q", str(roots.content)], check=True)
    subprocess.run(["git", "-C", str(roots.content), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(roots.content),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.test",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            "local edit",
        ],
        check=True,
    )
    result = read_status(roots, prepared.variant_id, services=Services.local())
    assert "content.modified" in {f.code for f in result.findings}
    assert load_ledger(roots.state, prepared.variant_id).generation == 1
    assert {p.relative_to(tracking).as_posix(): p.read_bytes() for p in tracking.rglob("*") if p.is_file()} == before


def test_clean_status_is_read_only(tmp_path):
    from plugin_fork_io.transactions import apply_preview
    from test_transactions import prepared_fork

    roots, prepared = prepared_fork(tmp_path)
    assert apply_preview(roots.state, prepared.preview_id, services=Services.local()).applied

    def snapshot():
        return {str(p): (p.lstat().st_mtime_ns, p.read_bytes() if p.is_file() else None) for p in tmp_path.rglob("*")}

    before = snapshot()
    result = read_status(roots, prepared.variant_id, services=Services.local())
    assert result.allowed, result
    assert not result.findings
    assert before == snapshot()


def test_tracking_tampering_reported(tmp_path):
    import json

    from plugin_fork_io.transactions import apply_preview
    from test_transactions import prepared_fork

    roots, prepared = prepared_fork(tmp_path)
    assert apply_preview(roots.state, prepared.preview_id, services=Services.local()).applied
    path = roots.state / "forks" / prepared.variant_id / "ledger.json"
    value = json.loads(path.read_bytes())
    value["intent"] = ["silently changed"]
    path.write_bytes(json.dumps(value).encode())
    result = read_status(roots, prepared.variant_id, services=Services.local())
    assert "tracking.modified" in {f.code for f in result.findings}


def test_incomplete_transaction_status_preserves_evidence(tmp_path):
    from dataclasses import replace

    from plugin_fork_io.transactions import apply_preview
    from test_transactions import FailingFS, prepared_fork

    roots, prepared = prepared_fork(tmp_path)
    apply_preview(
        roots.state,
        prepared.preview_id,
        services=replace(Services.local(), filesystem=FailingFS("SKILL.md", when="after")),
    )
    before = {str(p): p.read_bytes() for p in roots.state.rglob("*") if p.is_file()}
    result = read_status(roots, prepared.variant_id, services=Services.local())
    assert "transaction.incomplete" in {f.code for f in result.findings}
    assert before == {str(p): p.read_bytes() for p in roots.state.rglob("*") if p.is_file()}


def test_new_local_files_and_links_are_reported_without_following(tmp_path):
    from plugin_fork_io.transactions import apply_preview
    from test_transactions import prepared_fork

    roots, prepared = prepared_fork(tmp_path)
    assert apply_preview(roots.state, prepared.preview_id, services=Services.local()).applied
    (roots.content / "review/new").write_bytes(b"new local file")
    (roots.content / "review/link").symlink_to(tmp_path / "elsewhere")
    result = read_status(roots, prepared.variant_id, services=Services.local())
    assert "content.modified" in {f.code for f in result.findings}


def test_wrong_root_and_tampered_base_are_reported(tmp_path):
    from plugin_fork_io.transactions import apply_preview
    from test_transactions import prepared_fork

    roots, prepared = prepared_fork(tmp_path)
    assert apply_preview(roots.state, prepared.preview_id, services=Services.local()).applied
    wrong = read_status(Roots(tmp_path / "other-content", roots.state), prepared.variant_id, services=Services.local())
    assert "tracking.binding" in {f.code for f in wrong.findings}
    (roots.state / "forks" / prepared.variant_id / "base.tar.gz").write_bytes(b"corrupt archive")
    assert not read_status(roots, prepared.variant_id, services=Services.local()).allowed


@pytest.mark.parametrize("history_kind", ["empty", "unrelated", "missing-evidence"])
def test_edited_history_cannot_disable_tracking_attestation(tmp_path, history_kind):
    import json
    import shutil

    from helpers import selection, write_skill
    from plugin_fork_io import SourceSpec, apply_preview, plan_fork
    from test_transactions import prepared_fork

    roots, prepared = prepared_fork(tmp_path)
    assert apply_preview(roots.state, prepared.preview_id, services=Services.local()).applied
    history = []
    if history_kind == "unrelated":
        source = tmp_path / "other-source"
        write_skill(source, "second")
        other = plan_fork(
            SourceSpec(str(source), "local"), selection("second"), roots, intent=(), services=Services.local()
        )
        assert apply_preview(roots.state, other.preview_id, services=Services.local()).applied
        history = [other.preview_id]
    elif history_kind == "missing-evidence":
        shutil.rmtree(roots.state / "transactions" / prepared.preview_id)
    path = roots.state / "forks" / prepared.variant_id / "ledger.json"
    value = json.loads(path.read_bytes())
    value["intent"] = ["silently changed"]
    value["history"] = history
    path.write_bytes(json.dumps(value).encode())
    before = {str(p): p.read_bytes() for p in roots.state.rglob("*") if p.is_file()}
    result = read_status(roots, prepared.variant_id, services=Services.local())
    assert not result.allowed
    assert any(f.code.startswith("tracking.") for f in result.findings)
    assert before == {str(p): p.read_bytes() for p in roots.state.rglob("*") if p.is_file()}


def test_claimed_variant_journal_must_attest_its_own_ledger(tmp_path):
    from dataclasses import replace

    from helpers import selection, write_skill
    from plugin_fork_io import SourceSpec, apply_preview, plan_fork
    from plugin_fork_io.store import sealed_bytes
    from plugin_fork_io.transactions import journal_path, load_journal
    from test_transactions import prepared_fork

    roots, prepared = prepared_fork(tmp_path)
    assert apply_preview(roots.state, prepared.preview_id, services=Services.local()).applied
    source = tmp_path / "other-source"
    write_skill(source, "second")
    other = plan_fork(
        SourceSpec(str(source), "local"), selection("second"), roots, intent=(), services=Services.local()
    )
    assert apply_preview(roots.state, other.preview_id, services=Services.local()).applied
    journal = load_journal(roots.state, other.preview_id)
    forged_claim = replace(journal, variant_ids=(*journal.variant_ids, prepared.variant_id))
    journal_path(roots.state, other.preview_id).write_bytes(sealed_bytes(forged_claim))
    result = read_status(roots, prepared.variant_id, services=Services.local())
    assert not result.allowed
    assert "tracking.invalid" in {f.code for f in result.findings}
