from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
from code_wiki_okf.placement import (
    CODE_GRAPH_LANE,
    CODE_WIKI_TYPES,
    EntityPage,
    PlacementContext,
    PlacementError,
    affected_directories,
    canonical_concept_id,
    canonical_member,
    context_from_resource,
    entities_directory,
    entity_page,
    file_system_directory,
    filesystem_member_identity,
    is_code_wiki_type,
    is_entity_lane_page,
    lane_directory,
    placement_rule,
    repository_directory,
)
from okf_io import load_bundle, validate

_TODAY = date(2026, 1, 1)


@pytest.mark.parametrize(
    ("type_name", "resource", "expected"),
    [
        ("Repository", "repo:acme/demo", "code-graph/demo"),
        ("Package", "pkg:acme/demo/lib", "code-graph/demo/entities/packages/lib"),
        ("App", "app:acme/demo/web", "code-graph/demo/entities/apps/web"),
        ("AgentPlugin", "agent_plugin:acme/demo/reviewer", "code-graph/demo/entities/agent-plugins/reviewer"),
        ("TestSuite", "test_suite:acme/demo/unit", "code-graph/demo/entities/test-suites/unit"),
        ("File", "file:acme/demo/src/main.py", "code-graph/demo/file-system/src/main.py"),
        ("Dependency", "dependency:acme/demo/pypi/httpx", "code-graph/demo/entities/dependencies/pypi/httpx"),
        ("Dependency", "dependency:acme/demo/npm/@acme/web", "code-graph/demo/entities/dependencies/npm/@acme__web"),
    ],
)
def test_canonical_placement_matrix(type_name: str, resource: str, expected: str) -> None:
    assert canonical_concept_id(context_from_resource(type_name, resource)) == expected


def test_context_from_resource_returns_the_parsed_identity() -> None:
    assert context_from_resource("Package", "pkg:acme/demo/@acme/lib") == PlacementContext(
        type_name="Package",
        resource="pkg:acme/demo/@acme/lib",
        name="@acme/lib",
        repository="demo",
    )
    assert context_from_resource("File", "file:acme/demo/src/main.py") == PlacementContext(
        type_name="File",
        resource="file:acme/demo/src/main.py",
        repository="demo",
        source_path="src/main.py",
    )
    assert context_from_resource("Dependency", "dependency:acme/demo/npm/@acme/lib") == PlacementContext(
        type_name="Dependency",
        resource="dependency:acme/demo/npm/@acme/lib",
        repository="demo",
        ecosystem="npm",
        name="@acme/lib",
    )


def test_same_dependency_name_in_two_ecosystems_has_distinct_placement() -> None:
    pypi = canonical_concept_id(context_from_resource("Dependency", "dependency:acme/demo/pypi/shared"))
    npm = canonical_concept_id(context_from_resource("Dependency", "dependency:acme/demo/npm/shared"))
    assert (pypi, npm) == (
        "code-graph/demo/entities/dependencies/pypi/shared",
        "code-graph/demo/entities/dependencies/npm/shared",
    )


@pytest.mark.parametrize(
    ("type_name", "resource"),
    [
        ("Repository", "repo:acme"),
        ("Package", "pkg:acme/demo"),
        ("Package", "pkg:acme//lib"),
        ("Dependency", "dependency:pypi/httpx"),
        ("Dependency", "dependency:acme/demo/pypi"),
        ("Dependency", "dependency:acme//pypi/httpx"),
        ("Dependency", "dependency:acme/demo/pypi/"),
        ("File", "file:acme/demo"),
        ("File", "file:acme//src/main.py"),
        ("Package", "app:acme/demo/lib"),
        ("Unknown", "unknown:acme/demo/lib"),
    ],
)
def test_malformed_resources_are_refused(type_name: str, resource: str) -> None:
    with pytest.raises(PlacementError, match=re.escape(resource)):
        context_from_resource(type_name, resource)


def test_old_two_part_dependency_resource_is_refused_with_the_four_part_shape() -> None:
    with pytest.raises(PlacementError, match=r"expected <organization>/<repository>/<ecosystem>/<name>"):
        context_from_resource("Dependency", "dependency:pypi/httpx")


@pytest.mark.parametrize(
    "context",
    [
        PlacementContext(type_name="Package", resource="pkg:acme/demo/lib", repository="other"),
        PlacementContext(type_name="Package", resource="pkg:acme/demo/lib", name="other"),
        PlacementContext(type_name="Dependency", resource="dependency:acme/demo/pypi/httpx", ecosystem="npm"),
        PlacementContext(type_name="File", resource="file:acme/demo/src/main.py", source_path="src/other.py"),
    ],
)
def test_explicit_context_cannot_override_resource_identity(context: PlacementContext) -> None:
    with pytest.raises(PlacementError, match=re.escape(context.resource)):
        canonical_concept_id(context)


@pytest.mark.parametrize(
    "resource",
    [
        "file:acme/demo/../secret",
        "file:acme/demo/src/./main.py",
        "file:acme/demo/src//main.py",
        "file:acme/demo//etc/passwd",
        "file:acme/demo/src/CON.txt",
        "dependency:acme/demo/npm/CON",
        "dependency:acme/demo/npm/con.txt",
        "dependency:acme/demo/npm/trailing ",
        "pkg:acme/demo/trailing.",
        "pkg:acme/demo/bad:name",
        "pkg:acme/demo/bad\\name",
        "pkg:acme/demo/bad\x00name",
        "pkg:acme/demo//absolute",
    ],
)
def test_unsafe_components_are_refused(resource: str) -> None:
    type_name = {"file": "File", "dependency": "Dependency", "pkg": "Package"}[resource.partition(":")[0]]
    with pytest.raises(PlacementError, match=re.escape(resource)):
        canonical_member(context_from_resource(type_name, resource))


def test_canonical_member_adds_the_markdown_suffix_after_validation() -> None:
    context = context_from_resource("Package", "pkg:acme/demo/@acme/web")
    assert canonical_member(context) == "code-graph/demo/entities/packages/@acme__web.md"


def test_filesystem_member_identity_preserves_display_but_collides_case_and_unicode() -> None:
    composed = "code-graph/Demo/entities/packages/caf\N{LATIN SMALL LETTER E WITH ACUTE}.md"
    decomposed = "code-graph/demo/entities/packages/cafe\N{COMBINING ACUTE ACCENT}.md"

    assert composed != decomposed
    assert filesystem_member_identity(composed) == filesystem_member_identity(decomposed)


@pytest.mark.parametrize(
    ("type_name", "resource", "expected"),
    [
        ("Repository", "repo:acme/demo", ("code-graph/demo",)),
        (
            "Package",
            "pkg:acme/demo/lib",
            ("code-graph/demo", "code-graph/demo/entities", "code-graph/demo/entities/packages"),
        ),
        (
            "File",
            "file:acme/demo/src/pkg/main.py",
            (
                "code-graph/demo",
                "code-graph/demo/file-system",
                "code-graph/demo/file-system/src",
                "code-graph/demo/file-system/src/pkg",
            ),
        ),
        (
            "Dependency",
            "dependency:acme/demo/pypi/httpx",
            (
                "code-graph/demo",
                "code-graph/demo/entities",
                "code-graph/demo/entities/dependencies",
                "code-graph/demo/entities/dependencies/pypi",
            ),
        ),
    ],
)
def test_affected_directories_are_the_ordered_index_ancestors(
    type_name: str, resource: str, expected: tuple[str, ...]
) -> None:
    assert affected_directories(context_from_resource(type_name, resource)) == expected


def test_code_wiki_type_ownership_is_an_exact_allow_list() -> None:
    assert (
        frozenset({"Repository", "Package", "App", "AgentPlugin", "TestSuite", "File", "Dependency"}) == CODE_WIKI_TYPES
    )
    assert all(is_code_wiki_type(type_name) for type_name in CODE_WIKI_TYPES)
    assert not is_code_wiki_type("Concept")
    assert not is_code_wiki_type(" Package ")


@pytest.mark.parametrize(
    ("concept_id", "expected"),
    [
        ("code-graph/demo", True),
        ("code-graph/demo/entities/packages/lib", True),
        ("code-graph/demo/entities/test-suites/unit", True),
        ("code-graph/demo/entities/dependencies/pypi/httpx", True),
        ("code-graph/demo/entities/packages/repository", True),
        ("code-graph/demo/entities/dependencies/pypi/entities", True),
        ("code-graph", False),
        ("code-graph/demo/entities", False),
        ("code-graph/demo/entities/packages", False),
        ("code-graph/demo/entities/packages/a/b", False),
        ("code-graph/demo/entities/widgets/lib", False),
        ("code-graph/demo/entities/dependencies/pypi", False),
        ("code-graph/demo/entities/dependencies/pypi/a/b", False),
        ("code-graph/demo/file-system/src/main.py", False),
        ("code-graph/demo/file-system/main", False),
        ("code-graph//entities/packages/lib", False),
        ("repositories/demo/repository", False),
        ("repositories/demo/packages/lib", False),
        ("packages/lib", False),
    ],
)
def test_entity_lane_shape_recognizes_only_canonical_catalog_members(concept_id: str, expected: bool) -> None:
    assert is_entity_lane_page(concept_id) is expected


def test_distinct_resources_with_a_collision_prone_slug_are_detectable_before_writes() -> None:
    scoped = context_from_resource("Package", "pkg:acme/demo/@acme/web")
    flat = context_from_resource("Package", "pkg:acme/demo/@acme__web")
    assert scoped.resource != flat.resource
    assert canonical_concept_id(scoped) == canonical_concept_id(flat)


def _write_page(root: Path, concept_id: str, *, type_name: str, resource: str) -> None:
    target = root / f"{concept_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f'---\ntype: {type_name}\ntitle: "page"\nresource: "{resource}"\n---\n\n',
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("type_name", "resource", "expected"),
    [
        ("Repository", "repo:acme/demo", "code-graph/demo"),
        ("Package", "pkg:acme/demo/lib", "code-graph/demo/entities/packages/lib"),
        ("App", "app:acme/demo/web", "code-graph/demo/entities/apps/web"),
        ("AgentPlugin", "agent_plugin:acme/demo/reviewer", "code-graph/demo/entities/agent-plugins/reviewer"),
        ("TestSuite", "test_suite:acme/demo/unit", "code-graph/demo/entities/test-suites/unit"),
        ("File", "file:acme/demo/src/main.py", "code-graph/demo/file-system/src/main.py"),
        ("Dependency", "dependency:acme/demo/pypi/httpx", "code-graph/demo/entities/dependencies/pypi/httpx"),
    ],
)
def test_placement_rule_reports_actual_and_expected_ids_for_every_type(
    tmp_path: Path, type_name: str, resource: str, expected: str
) -> None:
    actual = f"misplaced/{type_name.lower()}"
    _write_page(tmp_path, actual, type_name=type_name, resource=resource)

    report = validate(load_bundle(tmp_path), today=_TODAY, extra_rules=[placement_rule()])

    findings = report.by_code("placement.directory-mismatch")
    assert len(findings) == 1
    assert findings[0].path == f"{actual}.md"
    assert findings[0].severity == "error"
    assert actual in findings[0].message
    assert expected in findings[0].message


def test_placement_rule_reports_duplicate_resources(tmp_path: Path) -> None:
    resource = "dependency:acme/demo/pypi/httpx"
    _write_page(tmp_path, "code-graph/demo/entities/dependencies/pypi/httpx", type_name="Dependency", resource=resource)
    _write_page(tmp_path, "vendored/httpx", type_name="Dependency", resource=resource)

    report = validate(load_bundle(tmp_path), today=_TODAY, extra_rules=[placement_rule()])

    findings = report.by_code("placement.duplicate-resource")
    assert len(findings) == 1
    assert findings[0].path == "vendored/httpx.md"
    assert "code-graph/demo/entities/dependencies/pypi/httpx.md" in findings[0].message


def test_placement_rule_translates_public_warning_severity_to_a_warning_finding(tmp_path: Path) -> None:
    _write_page(tmp_path, "wrong/httpx", type_name="Dependency", resource="dependency:acme/demo/pypi/httpx")

    report = validate(load_bundle(tmp_path), today=_TODAY, extra_rules=[placement_rule(severity="warning")])

    assert report.by_code("placement.directory-mismatch")[0].severity == "warn"


def test_placement_rule_reports_malformed_owned_resource(tmp_path: Path) -> None:
    _write_page(tmp_path, "dependencies/pypi/broken", type_name="Dependency", resource="dependency:pypi/broken")

    report = validate(load_bundle(tmp_path), today=_TODAY, extra_rules=[placement_rule()])

    finding = report.by_code("placement.directory-mismatch")[0]
    assert finding.path == "dependencies/pypi/broken.md"
    assert "no safe canonical placement" in finding.message
    assert "malformed resource" in finding.message


def test_placement_rule_leaves_non_code_wiki_vocabulary_pages_alone(tmp_path: Path) -> None:
    _write_page(tmp_path, "concepts/overview", type_name="Concept", resource="concept:overview")

    report = validate(load_bundle(tmp_path), today=_TODAY, extra_rules=[placement_rule()])

    assert report.by_code("placement.directory-mismatch") == ()
    assert report.by_code("placement.duplicate-resource") == ()


def test_placement_rule_rejects_unknown_severity() -> None:
    with pytest.raises(ValueError, match="severity must be"):
        placement_rule(severity="fatal")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("type_name", "resource"),
    [
        ("Repository", "repo:acme/demo"),
        ("Package", "pkg:acme/demo/lib"),
        ("App", "app:acme/demo/web"),
        ("AgentPlugin", "agent_plugin:acme/demo/reviewer"),
        ("TestSuite", "test_suite:acme/demo/unit"),
        ("Dependency", "dependency:acme/demo/pypi/httpx"),
        ("Package", "pkg:acme/demo/repository"),
    ],
)
def test_entity_page_round_trips_every_non_file_canonical_id(type_name: str, resource: str) -> None:
    context = context_from_resource(type_name, resource)
    parsed = entity_page(canonical_concept_id(context))
    assert isinstance(parsed, EntityPage)
    assert parsed.type_name == type_name
    assert parsed.repository == context.repository
    assert parsed.ecosystem == context.ecosystem
    expected_name = context.repository if type_name == "Repository" else context.name
    assert parsed.name == expected_name


def test_entity_page_returns_none_for_a_file_mirror() -> None:
    concept_id = canonical_concept_id(context_from_resource("File", "file:acme/demo/src/main.py"))
    assert entity_page(concept_id) is None


def test_directory_helpers_compose_the_d003_tree() -> None:
    assert CODE_GRAPH_LANE == "code-graph"
    assert repository_directory("demo") == "code-graph/demo"
    assert entities_directory("demo") == "code-graph/demo/entities"
    assert file_system_directory("demo") == "code-graph/demo/file-system"
    assert lane_directory("demo", "Package") == "code-graph/demo/entities/packages"
    assert lane_directory("demo", "App") == "code-graph/demo/entities/apps"
    assert lane_directory("demo", "AgentPlugin") == "code-graph/demo/entities/agent-plugins"
    assert lane_directory("demo", "TestSuite") == "code-graph/demo/entities/test-suites"
    assert lane_directory("demo", "Dependency") == "code-graph/demo/entities/dependencies"
    assert lane_directory("demo", "Dependency", ecosystem="pypi") == "code-graph/demo/entities/dependencies/pypi"


@pytest.mark.parametrize("type_name", ["Repository", "File", "Concept"])
def test_lane_directory_refuses_a_type_without_an_entity_lane(type_name: str) -> None:
    with pytest.raises(PlacementError, match="no entity lane"):
        lane_directory("demo", type_name)


@pytest.mark.parametrize("repository", ["", "..", "a/b", "CON", "trailing."])
def test_directory_helpers_refuse_unsafe_repository_components(repository: str) -> None:
    with pytest.raises(PlacementError):
        repository_directory(repository)
    with pytest.raises(PlacementError):
        entities_directory(repository)


def test_a_dependency_resource_with_a_blank_repository_is_refused() -> None:
    # A Dependency requires a repository like every repository-owned type; the
    # resource is the only way to express one, so a blank repository segment
    # is the refusal case.
    with pytest.raises(PlacementError):
        canonical_concept_id(PlacementContext(type_name="Dependency", resource="dependency:acme//pypi/httpx"))
