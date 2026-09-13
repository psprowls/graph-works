import shutil

import pytest
from plugin_fork_io import Roots, Services, apply_preview, load_ledger, read_status
from plugin_fork_io.recovery import plan_rollback
from plugin_fork_io.store import load_binding
from test_acceptance import accept, prepared
from test_installation import install


@pytest.mark.parametrize("scope", ["project", "personal"])
@pytest.mark.parametrize("direct", [False, True])
def test_source_loss_and_independent_store_move_preserve_portable_acceptance_rollback(tmp_path, scope, direct):
    roots, variant, update = prepared(tmp_path)
    local = roots.content / "local-review/SKILL.md"
    before = local.read_bytes()
    accepted = accept(roots, variant, update)
    assert apply_preview(roots.state, accepted.preview_id, services=Services.local()).applied
    ledger_before = load_ledger(roots.state, variant)
    shutil.rmtree(tmp_path / "source")
    shutil.rmtree(tmp_path / "incoming")
    # Only portable state travels; completed journals/previews are dispensable.
    moved_state = tmp_path / "external-tracking/records"
    moved_state.parent.mkdir()
    shutil.move(roots.state / "forks", moved_state)
    moved_state.rename(moved_state.parent / "forks")
    moved_state = moved_state.parent
    shutil.rmtree(roots.state)
    root = tmp_path / ("home" if scope == "personal" else "client")
    content = root / ".agents/skills" if direct else tmp_path / "independently-moved/content"
    content.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(roots.content, content)
    roots = Roots(content, moved_state)
    preview = install(roots, variant, tmp_path, agents=("codex",), scope=scope)
    assert preview.allowed, preview.findings
    assert load_binding(moved_state, variant) is None
    assert apply_preview(moved_state, preview.preview_id, services=Services.local()).applied
    assert load_ledger(moved_state, variant) == ledger_before
    installed = root / ".agents/skills/local-review"
    assert installed.is_symlink() is (not direct)
    assert installed.readlink().is_absolute() is False if not direct else installed.is_dir()
    status = read_status(roots, variant, services=Services.local())
    assert status.allowed, status.findings
    rollback = plan_rollback(roots, variant, services=Services.local())
    assert rollback.allowed, rollback.findings
    assert apply_preview(moved_state, rollback.preview_id, services=Services.local()).applied
    assert (content / "local-review/SKILL.md").read_bytes() == before
    assert installed.joinpath("SKILL.md").read_bytes() == before
    assert load_ledger(moved_state, variant).generation == ledger_before.generation + 1
