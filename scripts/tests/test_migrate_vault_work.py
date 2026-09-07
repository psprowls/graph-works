"""The carried-over acceptance suite for `scripts/migrate_vault_work.py` (D-013).

Every case here is `packages/work-tracker-okf/tests/test_migration.py`'s, with
the import line rebound to the harvested copy. The carry-over is the whole
safety story for the harvest and is not optional: once C7 deletes the package
module, this file is the only thing that still exercises the planner.

Deliberately NOT carried: the four `_legacy_boundary_violations` cases. They
assert that no module under `packages/*/src/` parses the legacy dialect. Their
glob never reaches `scripts/`, so a harvested copy is outside their scope by
construction, and D-043 assigns their relocation to C7.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from okf_io import load_bundle, parse
from work_tracker_okf.dependencies import DependencyEdge

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from migrate_vault_work import (  # noqa: E402
    LEGACY_IGNORE,
    MigrationManifestEntry,
    MigrationPlan,
    _LegacyNode,
    _resolve_legacy,
    plan_migration,
)
from migrate_vault_work import (  # noqa: E402, PLC2701 -- unit cases reach module internals, as the original did
    _convert_document,
    _dependency_edges,
    _desired_member,
    _projected_refusals,
    _source_entries,
    _targets,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LEGACY_GRAPH_WIKI = FIXTURES / "legacy_graph_wiki"
FIXTURE_ROOT = LEGACY_GRAPH_WIKI


def _legacy_node(
    old_path: str = "work/2026-08-22-feature-child",
    *,
    basename: str = "2026-08-22-feature-child",
    type_name: str = "Feature",
    frontmatter: str = "type: Feature\nworkflow_status: open\n",
    archived: bool = False,
) -> _LegacyNode:
    page_path = f"{old_path}.md"
    document = parse(f"---\n{frontmatter}---\n# Body\n", path=Path(page_path))
    return _LegacyNode(old_path, page_path, basename, archived, type_name, document, False, None, False, ())


def _snapshot_bundle(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _planned_bytes(plan, member: str) -> bytes:
    return next(write.after for write in plan.mutation.writes if write.member == member)


def test_migration_dry_run_is_complete_and_write_free(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    shutil.copytree(FIXTURE_ROOT, legacy_root)
    before = _snapshot_bundle(legacy_root)

    plan = plan_migration(load_bundle(legacy_root, ignore=LEGACY_IGNORE))

    assert plan.ok, plan.mutation.refusals
    assert any(entry.old_path.endswith("2026-08-22-feature-child") for entry in plan.manifest)
    assert {entry.new_path for entry in plan.manifest} == {
        "work/epic-parent",
        "work/epic-parent/children/feature-child",
        "work/_archive/bug-old",
    }
    assert "01-design.md" in plan.diff()
    assert "workflow_status -> work_status" in plan.diff()
    assert _snapshot_bundle(legacy_root) == before


def test_migration_composes_reference_repairs_and_conversion_in_final_bytes(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    shutil.copytree(FIXTURE_ROOT, legacy_root)

    plan = plan_migration(load_bundle(legacy_root, ignore=LEGACY_IGNORE))

    child = "work/epic-parent/children/feature-child"
    writes = {write.member: write for write in plan.mutation.writes}
    assert f"{child}/01-design-spec.md" not in writes
    assert f"{child}/references/01-design.md" in writes
    assert f"{child}/references/02-plan.md" in writes
    assert f"{child}/references/.tasks.json" in {move.dest for move in plan.mutation.moves}
    page = writes[f"{child}.md"].after.decode("utf-8")
    assert "workflow_status:" not in page
    assert "work_status: in-progress" in page
    assert "\nparent:" not in page
    assert "\nchildren:" not in page
    assert "id: design" in page
    assert f"resource: /{child}/references/01-design.md" in page
    assert "path: work/_archive/bug-old" in page
    assert "blocks: execute" in page
    assert "needs: resolved" in page
    design = writes[f"{child}/references/01-design.md"].after.decode("utf-8")
    assert "/work/_archive/bug-old.md" in design
    parent = writes["work/epic-parent.md"].after.decode("utf-8")
    assert f"/{child}.md" in parent
    assert "path: work/_archive/bug-old" in parent
    assert "blocks: design" in parent
    archived = writes["work/_archive/bug-old.md"].after.decode("utf-8")
    assert f"superseded_by: {child}" in archived
    assert all("/01-design-spec.md" not in write.after.decode("utf-8", errors="ignore") for write in writes.values())


def test_migration_preserves_opaque_and_nested_attachments(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    shutil.copytree(FIXTURE_ROOT, legacy_root)
    plan = plan_migration(load_bundle(legacy_root, ignore=LEGACY_IGNORE))
    child = "work/epic-parent/children/feature-child/references"
    moves = {move.dest: move.source for move in plan.mutation.moves}

    assert moves[f"{child}/attachment.md"].endswith("/references/attachment.md")
    assert moves[f"{child}/nested/payload.bin"].endswith("/references/nested/payload.bin")
    assert any("attachment.md" in warning for warning in plan.opaque_warnings)


def test_migration_preserves_unregistered_legacy_named_attachment(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    shutil.copytree(FIXTURE_ROOT, legacy_root)
    attachment = legacy_root / "work/2026-08-22-feature-child/references/nested/01-design-spec.md"
    attachment.write_bytes(b"opaque legacy-named attachment\n")

    plan = plan_migration(load_bundle(legacy_root, ignore=LEGACY_IGNORE))
    moves = {move.source: move.dest for move in plan.mutation.moves}

    assert plan.ok, plan.mutation.refusals
    assert (
        moves["work/2026-08-22-feature-child/references/nested/01-design-spec.md"]
        == "work/epic-parent/children/feature-child/references/nested/01-design-spec.md"
    )


@pytest.mark.parametrize(
    ("content", "kind"),
    [
        ("---\ntype: Bug\n", "parse-error"),
        ("---\ntitle: Missing type\n---\n", "missing-type"),
        ("---\ntype: 7\n---\n", "type-invalid"),
        ("---\ntype: Unknown\n---\n", "unknown-type"),
    ],
)
def test_migration_refuses_incomplete_manifest_members(
    tmp_path: Path,
    content: str,
    kind: str,
) -> None:
    legacy_root = tmp_path / "legacy"
    shutil.copytree(FIXTURE_ROOT, legacy_root)
    (legacy_root / "work/2026-08-24-broken.md").write_text(content, encoding="utf-8", newline="")

    plan = plan_migration(load_bundle(legacy_root, ignore=LEGACY_IGNORE))

    assert not plan.ok
    assert any(
        refusal.path in {"work/2026-08-24-broken", "work/2026-08-24-broken.md"} and refusal.kind == kind
        for refusal in plan.mutation.refusals
    )


def test_migration_refuses_unreadable_manifest_member(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    shutil.copytree(FIXTURE_ROOT, legacy_root)
    (legacy_root / "work/2026-08-24-broken.md").write_bytes(b"\xff")

    plan = plan_migration(load_bundle(legacy_root, ignore=LEGACY_IGNORE))

    assert not plan.ok
    assert any(
        refusal.path == "work/2026-08-24-broken.md" and refusal.kind == "unreadable"
        for refusal in plan.mutation.refusals
    )


def test_migration_refuses_ignored_raw_manifest_member(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    shutil.copytree(FIXTURE_ROOT, legacy_root)
    hidden = legacy_root / "work/schema/children/feature-hidden.md"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("---\ntype: Unknown\n---\n", encoding="utf-8", newline="")
    bundle = load_bundle(legacy_root, ignore=LEGACY_IGNORE)

    assert "work/schema/children/feature-hidden.md" in bundle.ignored

    plan = plan_migration(bundle)

    assert not plan.ok
    assert any(
        refusal.path == "work/schema/children/feature-hidden" and refusal.kind == "unknown-type"
        for refusal in plan.mutation.refusals
    )


def test_migration_refuses_disagreeing_hierarchy(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    shutil.copytree(FIXTURE_ROOT, legacy_root)
    parent = legacy_root / "work/2026-08-20-epic-parent.md"
    parent.write_text(
        parent.read_text(encoding="utf-8").replace("  - 2026-08-22-feature-child\n", ""),
        encoding="utf-8",
        newline="",
    )

    plan = plan_migration(load_bundle(legacy_root, ignore=LEGACY_IGNORE))

    assert not plan.ok
    assert any(refusal.kind == "hierarchy-disagreement" for refusal in plan.mutation.refusals)


def test_migration_refuses_ambiguous_date_free_destinations(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    shutil.copytree(FIXTURE_ROOT, legacy_root)
    parent = legacy_root / "work/2026-08-20-epic-parent.md"
    parent.write_text(
        parent.read_text(encoding="utf-8").replace(
            "children:\n  - 2026-08-22-feature-child\n",
            "",
        ),
        encoding="utf-8",
        newline="",
    )
    duplicate = legacy_root / "work/2026-08-23-feature-child.md"
    duplicate_text = (legacy_root / "work/2026-08-22-feature-child.md").read_text(encoding="utf-8")
    duplicate_text = duplicate_text.replace(
        "sources:\n"
        "  - id: design-spec\n"
        "    resource: /work/2026-08-22-feature-child/01-design-spec.md\n"
        "    title: Legacy design\n"
        "  - id: plan\n"
        "    resource: /work/2026-08-22-feature-child/02-plan-plan.md\n"
        "    title: Legacy plan\n",
        "",
    )
    duplicate_text = duplicate_text.replace("depends_on:\n  - 2026-08-19-bug-old\n", "")
    duplicate.write_text(duplicate_text, encoding="utf-8", newline="")

    plan = plan_migration(load_bundle(legacy_root, ignore=LEGACY_IGNORE))

    assert not plan.ok
    assert any(refusal.kind == "destination-collision" for refusal in plan.mutation.refusals)


def test_migration_refuses_missing_hierarchy_nodes(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    shutil.copytree(FIXTURE_ROOT, legacy_root)
    child = legacy_root / "work/2026-08-22-feature-child.md"
    child.write_text(
        child.read_text(encoding="utf-8").replace(
            "parent: 2026-08-20-epic-parent",
            "parent: missing-parent",
        ),
        encoding="utf-8",
        newline="",
    )

    plan = plan_migration(load_bundle(legacy_root, ignore=LEGACY_IGNORE))

    assert not plan.ok
    assert any(refusal.kind == "missing-node" for refusal in plan.mutation.refusals)


def test_migration_refuses_missing_physical_parent_without_raising(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    shutil.copytree(FIXTURE_ROOT, legacy_root)
    page = legacy_root / "work/missing/children/feature-orphan.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\ntype: Feature\n---\n", encoding="utf-8", newline="")

    plan = plan_migration(load_bundle(legacy_root, ignore=LEGACY_IGNORE))

    assert not plan.ok
    assert any(
        refusal.path == "work/missing/children/feature-orphan" and refusal.kind == "missing-node"
        for refusal in plan.mutation.refusals
    )


def test_migration_refuses_physical_child_omitted_by_declared_children(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    shutil.copytree(FIXTURE_ROOT, legacy_root)
    parent_name = "2026-08-20-epic-parent"
    child_name = "2026-08-22-feature-child"
    parent_page = legacy_root / f"work/{parent_name}.md"
    parent_page.write_text(
        parent_page.read_text(encoding="utf-8").replace(
            "children:\n  - 2026-08-22-feature-child\n",
            "children: []\n",
        ),
        encoding="utf-8",
        newline="",
    )
    child_page = legacy_root / f"work/{child_name}.md"
    child_page.write_text(
        child_page.read_text(encoding="utf-8").replace(
            "parent: 2026-08-20-epic-parent\n",
            "",
        ),
        encoding="utf-8",
        newline="",
    )
    child_lane = legacy_root / f"work/{parent_name}/children"
    child_lane.mkdir(parents=True)
    shutil.move(child_page, child_lane / child_page.name)
    shutil.move(legacy_root / f"work/{child_name}", child_lane / child_name)

    plan = plan_migration(load_bundle(legacy_root, ignore=LEGACY_IGNORE))

    assert not plan.ok
    assert any(
        refusal.path == f"work/{parent_name}/children/{child_name}" and refusal.kind == "hierarchy-disagreement"
        for refusal in plan.mutation.refusals
    )


def test_migration_refuses_hierarchy_cycles(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    shutil.copytree(FIXTURE_ROOT, legacy_root)
    parent = legacy_root / "work/2026-08-20-epic-parent.md"
    child = legacy_root / "work/2026-08-22-feature-child.md"
    parent_text = parent.read_text(encoding="utf-8").replace(
        "children:\n  - 2026-08-22-feature-child\n",
        "parent: 2026-08-22-feature-child\nchildren:\n  - 2026-08-22-feature-child\n",
    )
    child_text = child.read_text(encoding="utf-8").replace(
        "parent: 2026-08-20-epic-parent\n",
        "parent: 2026-08-20-epic-parent\nchildren:\n  - 2026-08-20-epic-parent\n",
    )
    parent.write_text(parent_text, encoding="utf-8", newline="")
    child.write_text(child_text, encoding="utf-8", newline="")

    plan = plan_migration(load_bundle(legacy_root, ignore=LEGACY_IGNORE))

    assert not plan.ok
    assert any(refusal.kind == "hierarchy-cycle" for refusal in plan.mutation.refusals)


def test_projected_migration_reloads_as_only_path_native_items(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    shutil.copytree(FIXTURE_ROOT, legacy_root)
    plan = plan_migration(load_bundle(legacy_root, ignore=LEGACY_IGNORE))

    assert plan.ok, plan.mutation.refusals
    assert set(plan.mutation.validate_paths) == {entry.new_path for entry in plan.manifest}
    index_members = {write.member for write in plan.mutation.writes if write.member.endswith("/index.md")}
    assert index_members >= {
        "work/index.md",
        "work/_archive/index.md",
        "work/epic-parent/children/index.md",
        "work/epic-parent/children/_archive/index.md",
        "work/epic-parent/children/feature-child/children/index.md",
        "work/epic-parent/children/feature-child/children/_archive/index.md",
    }


def test_projected_migration_refuses_a_remaining_unmanifested_work_member(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    shutil.copytree(FIXTURE_ROOT, legacy_root)
    plan = plan_migration(load_bundle(legacy_root, ignore=LEGACY_IGNORE))
    (legacy_root / "work/unmanifested.md").write_text("---\ntitle: Missing type\n---\n", encoding="utf-8", newline="")

    refusals = _projected_refusals(plan.mutation, plan.manifest)

    assert any(refusal.path == "work/unmanifested" and refusal.kind == "projection-unexpected" for refusal in refusals)


def test_projected_migration_refuses_an_ignored_raw_work_member(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    shutil.copytree(FIXTURE_ROOT, legacy_root)
    plan = plan_migration(load_bundle(legacy_root, ignore=LEGACY_IGNORE))
    hidden = legacy_root / "work/sections/children/feature-hidden.md"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("---\ntype: Unknown\n---\n", encoding="utf-8", newline="")

    refusals = _projected_refusals(plan.mutation, plan.manifest)

    assert any(
        refusal.path == "work/sections/children/feature-hidden" and refusal.kind == "projection-unknown-type"
        for refusal in refusals
    )


def test_migration_source_entry_conversion_validates_shape_and_managed_ids() -> None:
    assert _source_entries(None) is None
    assert _source_entries("not-a-list") is None
    assert _source_entries(("not-a-mapping",)) is None
    assert _source_entries(
        (
            {"resource": "/work/feature/references/01-design.md", "title": "Design"},
            {"resource": "/work/feature/references/note.md"},
        )
    ) == [
        {"resource": "/work/feature/references/01-design.md", "title": "Design", "id": "design"},
        {"resource": "/work/feature/references/note.md"},
    ]


def test_migration_document_conversion_handles_refusals_and_normalization(tmp_path: Path) -> None:
    node = _legacy_node(
        frontmatter=(
            "type: Feature\nworkflow_status: accepted\nparent: old\nchildren: [child]\nslug: old\n"
            "depends_on: [old]\nsources:\n  - resource: /work/x/references/02-plan.md\n"
        )
    )
    edge = DependencyEdge("work/new", "execute", "resolved")
    converted, refusal = _convert_document(
        node.document.serialize().encode(),
        path=tmp_path / node.page_path,
        node=node,
        edges=(edge,),
        superseded_by="work/replacement",
    )
    assert refusal is None and converted is not None
    text = converted.decode()
    assert "work_status: accepted" in text and "workflow_status:" not in text
    assert "parent:" not in text and "children:" not in text and "slug:" not in text
    assert "id: plan" in text and "superseded_by: work/replacement" in text and "path: work/new" in text

    assert _convert_document(b"\xff", path=tmp_path / "bad.md", node=None, edges=(), superseded_by=None)[1]
    assert _convert_document(b"---\ntype: Bug\n", path=tmp_path / "bad.md", node=None, edges=(), superseded_by=None)[1]
    disagreement = _legacy_node(frontmatter="type: Feature\nworkflow_status: open\nwork_status: resolved\n")
    disagreement_refusal = _convert_document(
        disagreement.document.serialize().encode(),
        path=tmp_path / disagreement.page_path,
        node=disagreement,
        edges=(),
        superseded_by=None,
    )[1]
    assert disagreement_refusal is not None and disagreement_refusal.kind == "frontmatter-disagreement"
    bad_sources = _legacy_node(frontmatter="type: Feature\nsources: invalid\n")
    sources_refusal = _convert_document(
        bad_sources.document.serialize().encode(),
        path=tmp_path / bad_sources.page_path,
        node=bad_sources,
        edges=(),
        superseded_by=None,
    )[1]
    assert sources_refusal is not None and sources_refusal.kind == "sources-invalid"


def test_migration_resolution_targets_and_member_projection() -> None:
    aliases = {"child": ("work/child",), "ambiguous": ("work/a", "work/b")}
    assert _resolve_legacy("child.md", aliases, owner="work/owner", field="parent") == ("work/child", None)
    assert _resolve_legacy("missing", aliases, owner="work/owner", field="parent")[1].kind == "missing-node"
    assert _resolve_legacy("ambiguous", aliases, owner="work/owner", field="parent")[1].kind == "ambiguous-node"

    invalid = _legacy_node(basename="BAD!name")
    assert _targets((invalid,), {invalid.old_path: None})[1][0].kind == "basename-invalid"
    first = _legacy_node("work/2026-08-22-feature-same", basename="2026-08-22-feature-same")
    second = _legacy_node("work/2026-08-23-feature-same", basename="2026-08-23-feature-same")
    targets, refusals = _targets((first, second), {first.old_path: None, second.old_path: None})
    assert targets[first.old_path] == "work/feature-same" and refusals[0].kind == "destination-collision"

    targets = {first.old_path: "work/feature-same"}
    assert _desired_member(first.page_path, (first,), targets, frozenset()) == "work/feature-same.md"
    assert _desired_member(f"{first.old_path}/children/index.md", (first,), targets, frozenset()).endswith(
        "/children/index.md"
    )
    legacy_plan = f"{first.old_path}/references/02-plan-plan.md"
    assert _desired_member(legacy_plan, (first,), targets, frozenset({legacy_plan})).endswith("/references/02-plan.md")
    loose_design = f"{first.old_path}/01-design-spec.md"
    assert _desired_member(loose_design, (first,), targets, frozenset({loose_design})).endswith(
        "/references/01-design.md"
    )
    assert _desired_member("outside.bin", (first,), targets, frozenset()) == "outside.bin"


@pytest.mark.parametrize(
    "frontmatter,kind",
    [
        ("type: Feature\ndepends_on: invalid\n", "dependency-missing-node"),
        ("type: Feature\ndepends_on: 7\n", "dependency-invalid"),
        ("type: Feature\ndepends_on: [7]\n", "dependency-invalid"),
        ("type: Feature\ndepends_on:\n  - path: missing\n", "dependency-missing-node"),
        ("type: Feature\ndepends_on:\n  - path: child\n    blocks: 7\n", "dependency-invalid"),
    ],
)
def test_migration_dependency_conversion_refuses_invalid_edges(frontmatter: str, kind: str) -> None:
    node = _legacy_node(frontmatter=frontmatter)
    _edges, refusals = _dependency_edges(node, {"child": (node.old_path,)}, {node.old_path: "work/feature-child"})
    assert refusals[0].kind == kind


def test_migration_dependency_conversion_accepts_string_and_mapping_edges() -> None:
    node = _legacy_node(
        frontmatter=("type: Feature\ndepends_on:\n  - child\n  - path: child\n    blocks: finish\n    needs: execute\n")
    )
    edges, refusals = _dependency_edges(node, {"child": (node.old_path,)}, {node.old_path: "work/feature-child"})
    assert refusals == ()
    assert edges == (
        DependencyEdge("work/feature-child", "execute", "resolved"),
        DependencyEdge("work/feature-child", "finish", "execute"),
    )


def test_harvested_copy_still_matches_the_package_module() -> None:
    """While both exist, a divergence is a bug. C7 deletes the package half."""
    package_module = (
        Path(__file__).resolve().parents[2]
        / "packages" / "work-tracker-okf" / "src" / "work_tracker_okf" / "migration.py"
    )
    if not package_module.exists():  # pragma: no cover -- post-C7
        pytest.skip("package module already removed by C7")
    harvested = (Path(__file__).resolve().parents[1] / "migrate_vault_work.py").read_text(encoding="utf-8")
    original = package_module.read_text(encoding="utf-8")
    # The harvest adds a provenance paragraph to the module docstring and
    # changes nothing else, so everything from the first import onwards is equal.
    marker = "from __future__ import annotations"
    assert harvested[harvested.index(marker) :] == original[original.index(marker) :]
