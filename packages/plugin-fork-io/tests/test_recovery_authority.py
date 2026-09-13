from dataclasses import replace

import pytest
from plugin_fork_io import Services, apply_preview, plan_rollback
from test_persistence_guards import reseal_record
from test_transactions import FailingFS, prepared_fork


def interrupted(tmp_path):
    roots, preview = prepared_fork(tmp_path)
    result = apply_preview(
        roots.state,
        preview.preview_id,
        services=replace(Services.local(), filesystem=FailingFS("SKILL.md", when="after")),
    )
    assert not result.applied and result.findings[0].code == "transaction.io"
    recovery = plan_rollback(roots, preview.variant_id, services=Services.local())
    assert recovery.allowed
    return roots, preview, recovery


@pytest.mark.parametrize(
    "case", ["schema", "identity", "journal-digest", "observed", "journal-complete", "journal-schema"]
)
def test_recovery_authority_must_still_match_the_retained_journal(tmp_path, case):
    roots, preview, recovery = interrupted(tmp_path)
    path = roots.state / "previews" / recovery.preview_id / "recovery.json"
    journal = roots.state / "transactions" / preview.preview_id / "journal.json"
    if case == "journal-complete":
        reseal_record(journal, lambda data: data.__setitem__("phase", "complete"))
    elif case == "journal-schema":
        reseal_record(journal, lambda data: data.__setitem__("schema_version", 2))
    else:
        field, value = {
            "schema": ("schema_version", 2),
            "identity": ("id", "foreign"),
            "journal-digest": ("journal_digest", "0" * 64),
            "observed": ("observed", []),
        }[case]
        reseal_record(path, lambda data: data.__setitem__(field, value))
    before = (roots.content / "review/SKILL.md").read_bytes()
    result = apply_preview(roots.state, recovery.preview_id, services=Services.local())
    assert not result.applied and not result.allowed
    assert (roots.content / "review/SKILL.md").read_bytes() == before
    assert journal.exists()


def test_recovery_rejects_two_incomplete_journals_for_one_variant(tmp_path):
    from plugin_fork_io.transactions import load_journal, persist_journal

    roots, preview, _recovery = interrupted(tmp_path)
    journal = load_journal(roots.state, preview.preview_id)
    persist_journal(roots.state, replace(journal, id="second-journal"), services=Services.local())
    result = plan_rollback(roots, preview.variant_id, services=Services.local())
    assert not result.allowed and result.findings[0].code == "recovery.unavailable"
    assert (roots.content / "review/SKILL.md").exists()


def test_recovery_preserves_journal_when_lock_acquisition_changes_preview(tmp_path):
    roots, _preview, recovery = interrupted(tmp_path)
    path = roots.state / "previews" / recovery.preview_id / "recovery.json"
    fs = Services.local().filesystem

    class Race:
        def __getattr__(self, name):
            return getattr(fs, name)

        def write_exclusive(self, target, content):
            fs.write_exclusive(target, content)
            if target.name == "ownership.lock":
                path.write_bytes(path.read_bytes() + b" ")

    result = apply_preview(roots.state, recovery.preview_id, services=replace(Services.local(), filesystem=Race()))
    assert not result.applied
    assert (roots.content / "review/SKILL.md").exists()
    assert not list((roots.state / "locks").glob("*.lock"))
