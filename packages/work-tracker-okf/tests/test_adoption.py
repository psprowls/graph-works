from datetime import date
from pathlib import Path

import pytest
from okf_io import Bundle, Source, load, load_bundle
from work_helpers import make_item, write_item
from work_tracker_okf.adoption import (
    AdoptionApplication,
    AmbiguousDraft,
    ChildSpecMove,
    apply_adoption,
    plan_adoption,
)
from work_tracker_okf.init import install_bundle
from work_tracker_okf.items import IGNORE, load_items
from work_tracker_okf.paths import artifact_path

TODAY = date(2026, 8, 18)
EPIC = "2026-08-18-epic-parent"
CHILD = "2026-08-18-epic-feature-alpha-beta"


@pytest.fixture
def bundle(tmp_path: Path) -> Bundle:
    root = tmp_path / "bundle"
    install_bundle(root, today=TODAY, dry_run=False)
    write_item(root, EPIC, "type: Epic\nworkflow_status: open\nstatus: draft\n")
    write_item(
        root,
        CHILD,
        f"type: Feature\nworkflow_status: open\nstatus: draft\nparent: {EPIC}\n",
    )
    return load_bundle(root, ignore=IGNORE)


def write_draft(root: Path, epic_slug: str, stem: str) -> Path:
    path = root / "work" / epic_slug / "references/child-specs" / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Design spec — {stem}\n", encoding="utf-8")
    return path


def write_old_layout_draft(root: Path, epic_slug: str, stem: str) -> Path:
    path = root / "work" / epic_slug / "child-specs" / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Legacy draft — {stem}\n", encoding="utf-8")
    return path


def snapshot_bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_plan_matches_only_migrated_drafts_by_child_slug_suffix(bundle: Bundle) -> None:
    draft = write_draft(bundle.root, EPIC, "alpha-beta")

    plan = plan_adoption(bundle, load_items(bundle), EPIC)

    assert plan.adopted == (
        ChildSpecMove(
            child_slug=CHILD,
            draft=draft,
            destination=bundle.root / f"work/{CHILD}/references/01-design-spec.md",
            child_page=bundle.root / f"work/{CHILD}.md",
            source_ref=artifact_path(CHILD, "design", "spec"),
            source_title="Design spec — alpha-beta",
            register_source=True,
        ),
    )
    assert plan.orphaned == ()
    assert plan.ambiguous == ()


def test_donor_layout_is_never_scanned(bundle: Bundle) -> None:
    write_old_layout_draft(bundle.root, EPIC, "alpha-beta")

    plan = plan_adoption(bundle, load_items(bundle), EPIC)

    assert plan.adopted == ()
    assert plan.unseeded == (CHILD,)


def test_orphan_and_ambiguous_drafts_are_classified_not_moved(bundle: Bundle) -> None:
    orphan = write_draft(bundle.root, EPIC, "orphan")
    ambiguous = write_draft(bundle.root, EPIC, "same-words")
    write_item(
        bundle.root,
        "2026-08-18-feature-same-words",
        f"type: Feature\nworkflow_status: open\nstatus: draft\nparent: {EPIC}\n",
    )
    write_item(
        bundle.root,
        "2026-08-18-bug-same-words",
        f"type: Bug\nworkflow_status: open\nstatus: draft\nparent: {EPIC}\n",
    )
    reloaded = load_bundle(bundle.root, ignore=IGNORE)

    plan = plan_adoption(reloaded, load_items(reloaded), EPIC)

    assert plan.orphaned == (orphan,)
    assert plan.ambiguous == (
        AmbiguousDraft(
            draft=ambiguous,
            candidate_slugs=("2026-08-18-bug-same-words", "2026-08-18-feature-same-words"),
        ),
    )


def test_unknown_and_non_epic_targets_refuse_without_classifying_drafts(bundle: Bundle) -> None:
    draft = write_draft(bundle.root, EPIC, "alpha-beta")
    items = load_items(bundle)

    unknown = plan_adoption(bundle, items, "2026-08-18-epic-missing")
    non_epic = plan_adoption(bundle, items, CHILD)

    assert unknown.refusal == "unknown-epic"
    assert non_epic.refusal == "not-an-epic"
    assert unknown.adopted == non_epic.adopted == ()
    assert draft.exists()


def test_planning_writes_nothing_and_apply_moves_then_registers(bundle: Bundle) -> None:
    write_draft(bundle.root, EPIC, "alpha-beta")
    before = snapshot_bytes(bundle.root)
    reloaded = load_bundle(bundle.root, ignore=IGNORE)

    plan = plan_adoption(reloaded, load_items(reloaded), EPIC)

    assert snapshot_bytes(bundle.root) == before
    applied = apply_adoption(plan)
    assert applied.moved == (plan.adopted[0].destination,)
    assert applied.registered == (plan.adopted[0].child_slug,)
    source = load(bundle.root / f"work/{CHILD}.md").fm.sources[0]
    assert source.id == "design-spec"
    assert source.resource.endswith("/references/01-design-spec.md")


def test_donor_disappearing_after_planning_never_creates_a_destination(bundle: Bundle) -> None:
    draft = write_draft(bundle.root, EPIC, "alpha-beta")
    plan = plan_adoption(bundle, load_items(bundle), EPIC)
    destination = plan.adopted[0].destination
    draft.unlink()

    with pytest.raises(FileNotFoundError):
        apply_adoption(plan)

    assert not destination.exists()
    assert load(bundle.root / f"work/{CHILD}.md").fm.sources == ()


def test_failed_destination_write_removes_only_the_file_created_by_adoption(
    bundle: Bundle,
    monkeypatch,
) -> None:
    draft = write_draft(bundle.root, EPIC, "alpha-beta")
    plan = plan_adoption(bundle, load_items(bundle), EPIC)
    destination = plan.adopted[0].destination
    original_open = Path.open

    class FailingStream:
        def __init__(self, stream) -> None:
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.stream.close()
            return False

        def write(self, _data: bytes) -> int:
            raise OSError("destination write failed")

    def fail_destination(path: Path, mode: str = "r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return FailingStream(stream) if path == destination and mode == "xb" else stream

    monkeypatch.setattr(Path, "open", fail_destination)

    with pytest.raises(OSError, match="destination write failed"):
        apply_adoption(plan)

    assert draft.exists()
    assert not destination.exists()
    assert load(bundle.root / f"work/{CHILD}.md").fm.sources == ()


def test_archived_child_with_valid_artifact_source_is_seeded(bundle: Bundle) -> None:
    artifact = artifact_path(CHILD, "design", "spec", archived=True)
    artifact.path(bundle.root).parent.mkdir(parents=True, exist_ok=True)
    artifact.path(bundle.root).write_text("# Existing design\n", encoding="utf-8")
    archived = make_item(
        CHILD,
        archived=True,
        path=f"work/_archive/{CHILD}.md",
        parent=EPIC,
        sources=(Source(id="design-spec", resource=artifact.resource),),
        has_spec_doc=True,
    )

    plan = plan_adoption(bundle, (make_item(EPIC, type="Epic"), archived), EPIC)

    assert plan.unseeded == ()
    assert plan.adopted == ()


def test_second_run_has_no_writes(bundle: Bundle) -> None:
    write_draft(bundle.root, EPIC, "alpha-beta")
    reloaded = load_bundle(bundle.root, ignore=IGNORE)
    first = plan_adoption(reloaded, load_items(reloaded), EPIC)
    apply_adoption(first)
    reloaded = load_bundle(bundle.root, ignore=IGNORE)

    second = plan_adoption(reloaded, load_items(reloaded), EPIC)

    assert second.adopted == ()
    assert apply_adoption(second) == AdoptionApplication()


def test_existing_canonical_artifact_without_source_plans_registration_only(bundle: Bundle) -> None:
    destination = artifact_path(CHILD, "design", "spec").path(bundle.root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("# Existing design\n", encoding="utf-8")
    reloaded = load_bundle(bundle.root, ignore=IGNORE)

    plan = plan_adoption(reloaded, load_items(reloaded), EPIC)

    assert len(plan.adopted) == 1
    assert plan.adopted[0].draft is None
    assert plan.adopted[0].register_source is True


def test_destination_conflict_keeps_draft_and_warns(bundle: Bundle) -> None:
    draft = write_draft(bundle.root, EPIC, "alpha-beta")
    destination = artifact_path(CHILD, "design", "spec").path(bundle.root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("authored destination\n", encoding="utf-8")
    reloaded = load_bundle(bundle.root, ignore=IGNORE)

    plan = plan_adoption(reloaded, load_items(reloaded), EPIC)

    assert draft.exists()
    assert len(plan.adopted) == 1 and plan.adopted[0].draft is None
    assert plan.unseeded == ()
    assert any("destination" in warning for warning in plan.warnings)


def test_authored_noncanonical_source_is_seeded_without_rewriting_it(bundle: Bundle) -> None:
    authored = bundle.root / f"work/{CHILD}/references/authored-design.md"
    authored.parent.mkdir(parents=True, exist_ok=True)
    authored.write_text("# Authored design\n", encoding="utf-8")
    page = load(bundle.root / f"work/{CHILD}.md")
    page.set(
        "sources",
        [{"id": "design-spec", "resource": f"/work/{CHILD}/references/authored-design.md", "title": "Authored"}],
    )
    page.save()
    reloaded = load_bundle(bundle.root, ignore=IGNORE)

    plan = plan_adoption(reloaded, load_items(reloaded), EPIC)

    assert plan.adopted == ()
    assert plan.unseeded == ()
    assert apply_adoption(plan) == AdoptionApplication()
    assert load(bundle.root / f"work/{CHILD}.md").fm.sources[0].resource.endswith("authored-design.md")


def test_missing_authored_noncanonical_source_is_not_rewritten(bundle: Bundle) -> None:
    draft = write_draft(bundle.root, EPIC, "alpha-beta")
    page = load(bundle.root / f"work/{CHILD}.md")
    page.set(
        "sources",
        [{"id": "design-spec", "resource": f"/work/{CHILD}/references/missing.md", "title": "Authored"}],
    )
    page.save()
    reloaded = load_bundle(bundle.root, ignore=IGNORE)

    plan = plan_adoption(reloaded, load_items(reloaded), EPIC)

    assert plan.adopted == ()
    assert plan.unseeded == (CHILD,)
    assert any("authored" in warning for warning in plan.warnings)
    assert draft.exists()
    assert load(bundle.root / f"work/{CHILD}.md").fm.sources[0].resource.endswith("missing.md")


def test_apply_skips_a_stale_plan_when_an_authored_source_appears(bundle: Bundle) -> None:
    draft = write_draft(bundle.root, EPIC, "alpha-beta")
    plan = plan_adoption(bundle, load_items(bundle), EPIC)
    page = load(bundle.root / f"work/{CHILD}.md")
    page.set(
        "sources",
        [{"id": "design-spec", "resource": f"/work/{CHILD}/references/authored.md", "title": "Authored"}],
    )
    page.save()

    applied = apply_adoption(plan)

    assert applied.moved == ()
    assert applied.registered == ()
    assert applied.skipped == (CHILD,)
    assert any("authored" in warning for warning in applied.warnings)
    assert draft.exists()
    assert not plan.adopted[0].destination.exists()
    assert load(bundle.root / f"work/{CHILD}.md").fm.sources[0].resource.endswith("authored.md")


def test_second_plan_keeps_conflict_warning_after_canonical_source_registration(bundle: Bundle) -> None:
    draft = write_draft(bundle.root, EPIC, "alpha-beta")
    destination = artifact_path(CHILD, "design", "spec").path(bundle.root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("# Existing design\n", encoding="utf-8")
    first_bundle = load_bundle(bundle.root, ignore=IGNORE)
    first = plan_adoption(first_bundle, load_items(first_bundle), EPIC)
    apply_adoption(first)
    reloaded = load_bundle(bundle.root, ignore=IGNORE)

    second = plan_adoption(reloaded, load_items(reloaded), EPIC)

    assert second.adopted == ()
    assert draft.exists()
    assert any("destination" in warning for warning in second.warnings)


def test_directory_at_canonical_destination_is_unseeded_conflict(bundle: Bundle) -> None:
    draft = write_draft(bundle.root, EPIC, "alpha-beta")
    destination = artifact_path(CHILD, "design", "spec").path(bundle.root)
    destination.mkdir(parents=True)
    reloaded = load_bundle(bundle.root, ignore=IGNORE)

    plan = plan_adoption(reloaded, load_items(reloaded), EPIC)

    assert plan.adopted == ()
    assert plan.unseeded == (CHILD,)
    assert any("destination" in warning for warning in plan.warnings)
    assert draft.exists()


def test_non_utf8_canonical_destination_is_unseeded_conflict(bundle: Bundle) -> None:
    draft = write_draft(bundle.root, EPIC, "alpha-beta")
    destination = artifact_path(CHILD, "design", "spec").path(bundle.root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"\xff")
    reloaded = load_bundle(bundle.root, ignore=IGNORE)

    plan = plan_adoption(reloaded, load_items(reloaded), EPIC)

    assert plan.adopted == ()
    assert plan.unseeded == (CHILD,)
    assert any("destination" in warning for warning in plan.warnings)
    assert draft.exists()
