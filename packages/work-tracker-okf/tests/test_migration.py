from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest
from okf_io import load_bundle, parse
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.migration import (
    LEGACY_IGNORE,
    _convert_document,
    _dependency_edges,
    _desired_member,
    _LegacyNode,
    _projected_refusals,
    _resolve_legacy,
    _source_entries,
    _targets,
    plan_migration,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "legacy_graph_wiki"


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
    (legacy_root / "work/2026-08-24-broken.md").write_text(content, encoding="utf-8")

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
    hidden.write_text("---\ntype: Unknown\n---\n", encoding="utf-8")
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
    duplicate.write_text(duplicate_text, encoding="utf-8")

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
    )

    plan = plan_migration(load_bundle(legacy_root, ignore=LEGACY_IGNORE))

    assert not plan.ok
    assert any(refusal.kind == "missing-node" for refusal in plan.mutation.refusals)


def test_migration_refuses_missing_physical_parent_without_raising(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    shutil.copytree(FIXTURE_ROOT, legacy_root)
    page = legacy_root / "work/missing/children/feature-orphan.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\ntype: Feature\n---\n", encoding="utf-8")

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
    )
    child_page = legacy_root / f"work/{child_name}.md"
    child_page.write_text(
        child_page.read_text(encoding="utf-8").replace(
            "parent: 2026-08-20-epic-parent\n",
            "",
        ),
        encoding="utf-8",
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
    parent.write_text(parent_text, encoding="utf-8")
    child.write_text(child_text, encoding="utf-8")

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
    (legacy_root / "work/unmanifested.md").write_text("---\ntitle: Missing type\n---\n", encoding="utf-8")

    refusals = _projected_refusals(plan.mutation, plan.manifest)

    assert any(refusal.path == "work/unmanifested" and refusal.kind == "projection-unexpected" for refusal in refusals)


def test_projected_migration_refuses_an_ignored_raw_work_member(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    shutil.copytree(FIXTURE_ROOT, legacy_root)
    plan = plan_migration(load_bundle(legacy_root, ignore=LEGACY_IGNORE))
    hidden = legacy_root / "work/sections/children/feature-hidden.md"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("---\ntype: Unknown\n---\n", encoding="utf-8")

    refusals = _projected_refusals(plan.mutation, plan.manifest)

    assert any(
        refusal.path == "work/sections/children/feature-hidden" and refusal.kind == "projection-unknown-type"
        for refusal in refusals
    )


def _legacy_boundary_violations(repo_root: Path) -> list[str]:
    packages_root = repo_root / "packages"
    dialect_roots = (
        packages_root / "work-tracker-okf/src/work_tracker_okf",
        packages_root / "graph-works-core/src/graph_works_core",
    )
    legacy_module = dialect_roots[0] / "migration.py"
    migration_command = dialect_roots[1] / "work/commands.py"
    forbidden = {"workflow_status", "parent", "children"}
    violations: list[str] = []
    for path in sorted(packages_root.glob("*/src/**/*.py")):
        if path == legacy_module:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        checks_dialect = any(path.is_relative_to(root) for root in dialect_roots)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and "DATE_PREFIX" in node.id:
                violations.append(f"{path.name}:{node.lineno}:{node.id}")
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and (r"\d{4}-\d{2}-\d{2}-" in node.value or "YYYY-MM-DD-" in node.value)
            ):
                violations.append(f"{path.name}:{node.lineno}:legacy-date-prefix")
            if (
                checks_dialect
                and isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "workflow_status" in node.value
            ):
                violations.append(f"{path.name}:{node.lineno}:workflow_status")
            imports_migration = (
                isinstance(node, ast.Import) and any(alias.name == "work_tracker_okf.migration" for alias in node.names)
            ) or (
                isinstance(node, ast.ImportFrom)
                and (
                    node.module == "work_tracker_okf.migration"
                    or (node.module == "work_tracker_okf" and any(alias.name == "migration" for alias in node.names))
                    or (
                        node.level > 0
                        and path.is_relative_to(dialect_roots[0])
                        and (
                            node.module == "migration"
                            or (node.module is None and any(alias.name == "migration" for alias in node.names))
                        )
                    )
                )
            )
            if imports_migration:
                ancestor = parents.get(node)
                while ancestor is not None and not isinstance(ancestor, ast.FunctionDef):
                    ancestor = parents.get(ancestor)
                allowed = (
                    path == migration_command
                    and isinstance(ancestor, ast.FunctionDef)
                    and ancestor.name == "run_migrate_layout"
                    and isinstance(parents.get(ancestor), ast.Module)
                    and parents.get(node) is ancestor
                )
                if not allowed:
                    violations.append(f"{path.name}:{node.lineno}:legacy-import")
            if not checks_dialect or not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"set", "insert", "__setitem__"} or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and first.value in forbidden:
                violations.append(f"{path.name}:{node.lineno}:{first.value}")
    return violations


def test_legacy_parsing_and_frontmatter_writes_are_isolated_to_migration_module() -> None:
    repo_root = Path(__file__).parents[3]

    assert _legacy_boundary_violations(repo_root) == []


def test_legacy_boundary_scans_cli_and_plain_imports(tmp_path: Path) -> None:
    rogue = tmp_path / "packages/graph-works-cli/src/graph_works_cli/rogue.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text("import work_tracker_okf.migration\n", encoding="utf-8")
    second = tmp_path / "packages/code-wiki-okf/src/code_wiki_okf/rogue_from.py"
    second.parent.mkdir(parents=True)
    second.write_text("from work_tracker_okf.migration import plan_migration\n", encoding="utf-8")

    violations = _legacy_boundary_violations(tmp_path)

    assert {"rogue.py:1:legacy-import", "rogue_from.py:1:legacy-import"} <= set(violations)


def test_legacy_boundary_rejects_relative_import_forms(tmp_path: Path) -> None:
    package = tmp_path / "packages/work-tracker-okf/src/work_tracker_okf"
    package.mkdir(parents=True)
    direct = package / "rogue_relative.py"
    direct.write_text("from .migration import plan_migration\n", encoding="utf-8")
    sibling = package / "rogue_sibling.py"
    sibling.write_text("from . import migration\n", encoding="utf-8")

    violations = _legacy_boundary_violations(tmp_path)

    assert {"rogue_relative.py:1:legacy-import", "rogue_sibling.py:1:legacy-import"} <= set(violations)


def test_legacy_boundary_rejects_same_named_class_method(tmp_path: Path) -> None:
    commands = tmp_path / "packages/graph-works-core/src/graph_works_core/work/commands.py"
    commands.parent.mkdir(parents=True)
    commands.write_text(
        "class Rogue:\n"
        "    def run_migrate_layout(self):\n"
        "        from work_tracker_okf.migration import plan_migration\n",
        encoding="utf-8",
    )

    violations = _legacy_boundary_violations(tmp_path)

    assert "commands.py:3:legacy-import" in violations


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
