"""Captured experiments are byte evidence, never executable skill instructions."""

import hashlib
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures/experiments"


def test_attributed_experiment_bytes_match_pinned_member_hashes():
    metadata = json.loads((FIXTURES / "manifest.json").read_bytes())
    assert metadata["archive_sha256"] == "8da6eb76802116c9bf0904fe0bebc3b27b9fcbcc8218b9d16c8e90e1fc78131b"
    for entry in metadata["files"]:
        assert hashlib.sha256((FIXTURES / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]


def experiment_update(tmp_path, *, behavioral=False):
    import shutil

    from plugin_fork_io import Roots, Services, SourceSpec, apply_preview, plan_fork
    from plugin_fork_io.records import Component, Dependency, Selection, SourceMapping
    from plugin_fork_io.updates import plan_update

    metadata = json.loads((FIXTURES / "manifest.json").read_bytes())
    for version in ("v6.2.0", "v6.3.0"):
        shutil.copytree(FIXTURES / "sources" / version, tmp_path / version)
        for entry in metadata["files"]:
            prefix = "sources/" + version + "/"
            if entry["path"].startswith(prefix):
                (tmp_path / version / entry["path"].removeprefix(prefix)).chmod(entry["mode"])
    from plugin_fork_io.adaptations import decode_adaptation

    adaptations = tuple(decode_adaptation(e) for e in json.loads((FIXTURES / "naming-adaptations.json").read_bytes()))
    # The other workflow skills are explicit supplied test-context dependencies.
    dependencies = tuple(
        Dependency("required-skill", name, "test-context", 1, 0, 0, "user", satisfied=True)
        for name in (
            "psprowls-using-git-worktrees",
            "psprowls-requesting-code-review",
            "psprowls-finishing-a-development-branch",
            "psprowls-executing-plans",
        )
    )
    selected = Selection(
        (Component("skills/subagent-driven-development", "psprowls-subagent-driven-development"),),
        (
            SourceMapping(
                "skills/requesting-code-review/code-reviewer.md", "psprowls-requesting-code-review/code-reviewer.md"
            ),
            SourceMapping("LICENSE", "LICENSE"),
        ),
        dependencies,
        adaptations,
        (),
    )
    roots = Roots(tmp_path / "content", tmp_path / "state")
    prepared = plan_fork(
        SourceSpec(str(tmp_path / "v6.2.0"), "local"),
        selected,
        roots,
        intent=("Preserve local names and plan/spec approval gate",),
        services=Services.local(),
    )
    assert prepared.allowed, prepared.findings
    assert apply_preview(roots.state, prepared.preview_id, services=Services.local()).applied
    # Replay the actual independently customized local snapshot, never call it upstream.
    for source in (FIXTURES / "fork-v6.2.0/skills").rglob("*"):
        if source.is_file() and (
            "psprowls-subagent-driven-development" in source.parts or source.name == "code-reviewer.md"
        ):
            destination = roots.content / source.relative_to(FIXTURES / "fork-v6.2.0/skills")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
    if behavioral:
        (roots.content / "psprowls-subagent-driven-development/SKILL.md").write_bytes(
            (
                FIXTURES / "behavior-experiment/older-local/skills/psprowls-subagent-driven-development/SKILL.md"
            ).read_bytes()
        )
    updated = plan_update(
        roots,
        prepared.variant_id,
        SourceSpec(str(tmp_path / "v6.3.0"), "local"),
        selection=selected,
        mappings={},
        services=Services.local(),
    )
    return roots, prepared.variant_id, updated


def test_archived_naming_update_retains_local_names_and_real_upstream_improvements(tmp_path):
    roots, _variant, update = experiment_update(tmp_path)
    assert update.allowed, update.findings
    candidate = Path(update.data["candidate_path"])
    assert update.data["text_conflicts"] == []
    assert (candidate / "psprowls-requesting-code-review/code-reviewer.md").read_bytes() == (
        FIXTURES / "sources/v6.3.0/skills/requesting-code-review/code-reviewer.md"
    ).read_bytes()
    before = (roots.content / "psprowls-subagent-driven-development/SKILL.md").read_bytes()
    after = (candidate / "psprowls-subagent-driven-development/SKILL.md").read_bytes()
    assert b"name: psprowls-subagent-driven-development" in after and after != before
    assert b"If the plan itself is wrong, rule on the correction, ledger it" in after


def test_text_clean_is_not_behaviorally_reviewed(tmp_path):
    _roots, _variant, result = experiment_update(tmp_path, behavioral=True)
    assert result.data["text_conflicts"] == []
    assert result.data["behavioral_review"]["state"] == "not_requested"
    candidate = Path(result.data["candidate_path"])
    sdd = (candidate / "psprowls-subagent-driven-development/SKILL.md").read_text(encoding="utf-8")
    assert "contradicts the specification, ask the human before changing the plan" in sdd
    assert "If the plan itself is wrong, rule on the correction, ledger it" in sdd
    assert result.data["behavioral_review"].get("passed") is not True


def test_archived_reviewed_resolution_is_final_bytes_and_explicit_evidence(tmp_path):
    from plugin_fork_io import Finding, Review, Services, SourceSpec, apply_preview, load_preview
    from plugin_fork_io.acceptance import plan_accept
    from plugin_fork_io.snapshots import capture

    roots, variant, update = experiment_update(tmp_path, behavioral=True)
    candidate = Path(update.data["candidate_path"])
    definition = candidate / "psprowls-subagent-driven-development/SKILL.md"
    archived = FIXTURES / "behavior-experiment"
    assert (
        definition.read_bytes()
        == (archived / "text-merge/skills/psprowls-subagent-driven-development/SKILL.md").read_bytes()
    )
    reviewed = (archived / "resolved-preview/skills/psprowls-subagent-driven-development/SKILL.md").read_bytes()
    assert hashlib.sha256(reviewed).hexdigest() == "fd62fd73a88814227eb26bc1939662646f2e0b75700644097a46db426c1ec15c"
    definition.write_bytes(reviewed)
    assert b"contradicts the specification, ask the human before changing the plan" in reviewed
    assert b"If the plan itself is wrong, rule on the correction, ledger it" not in reviewed
    final = capture(SourceSpec(str(candidate), "local"), (), services=Services.local())
    review = Review(
        final.digest,
        (
            Finding(
                "behavior.plan-spec",
                "warn",
                str(definition),
                None,
                "Archived contradiction reconciled; static scenarios are evidence, not model compliance",
            ),
        ),
    )
    accepted = plan_accept(
        roots, variant, update.preview_id, review=review, intent=(), resolutions=(), services=Services.local()
    )
    assert accepted.allowed, accepted.findings
    assert load_preview(roots.state, accepted.preview_id).review_state == "completed_with_findings"
    assert apply_preview(roots.state, accepted.preview_id, services=Services.local()).applied
    assert (roots.content / "psprowls-subagent-driven-development/SKILL.md").read_bytes() == reviewed
    scenarios = json.loads((archived / "ledger.json").read_bytes())["review"]["scenarios"]
    assert scenarios == {
        "plan_spec_contradiction": "Ask and hold affected work; independent work can continue.",
        "ordinary_variable_name": "Proceed without approval.",
        "fix_cap_contradiction": "Cap does not bypass approval.",
        "approval_received": "Record it, correct plan, resume affected work.",
        "missing_spec_internal_ambiguity": (
            "Record missing spec and provisional ruling; stop only if another stopping condition applies."
        ),
    }


def test_explicit_archived_recipe_has_unique_anchors_and_reproduces_local_bytes():
    from plugin_fork_io.adaptations import decode_adaptation, replay

    edits = tuple(decode_adaptation(value) for value in json.loads((FIXTURES / "naming-adaptations.json").read_bytes()))
    assert len(edits) == 40
    for path in {edit.path for edit in edits}:
        original = (FIXTURES / "sources/v6.2.0" / path).read_bytes()
        incoming = (FIXTURES / "sources/v6.3.0" / path).read_bytes()
        grouped = tuple(edit for edit in edits if edit.path == path)
        for edit in grouped:
            assert original.count(edit.expected) == incoming.count(edit.expected) == 1
        local_path = path.replace("skills/subagent-driven-development/", "skills/psprowls-subagent-driven-development/")
        result, findings = replay(original, grouped)
        assert not findings and result == (FIXTURES / "fork-v6.2.0" / local_path).read_bytes()
