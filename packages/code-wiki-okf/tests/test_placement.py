from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
from code_wiki_okf.placement import (
    CODE_WIKI_TYPES,
    PlacementContext,
    PlacementError,
    affected_directories,
    canonical_concept_id,
    canonical_member,
    context_from_resource,
    filesystem_member_identity,
    is_code_wiki_type,
    is_entity_lane_page,
    placement_rule,
)
from okf_io import load_bundle, validate

_TODAY = date(2026, 1, 1)


@pytest.mark.parametrize(
    ("type_name", "resource", "expected"),
    [
        ("Repository", "repo:acme/demo", "repositories/demo/repository"),
        ("Package", "pkg:acme/demo/lib", "repositories/demo/packages/lib"),
        ("App", "app:acme/demo/web", "repositories/demo/apps/web"),
        ("AgentPlugin", "agent_plugin:acme/demo/reviewer", "repositories/demo/agent-plugins/reviewer"),
        ("TestSuite", "test_suite:acme/demo/unit", "repositories/demo/test-suites/unit"),
        ("File", "file:acme/demo/src/main.py", "repositories/demo/files/src/main.py"),
        ("Dependency", "dependency:pypi/httpx", "dependencies/pypi/httpx"),
        ("Dependency", "dependency:npm/@acme/web", "dependencies/npm/@acme__web"),
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
    assert context_from_resource("Dependency", "dependency:npm/@acme/lib") == PlacementContext(
        type_name="Dependency",
        resource="dependency:npm/@acme/lib",
        name="@acme/lib",
        ecosystem="npm",
    )


def test_same_dependency_name_in_two_ecosystems_has_distinct_placement() -> None:
    pypi = canonical_concept_id(context_from_resource("Dependency", "dependency:pypi/shared"))
    npm = canonical_concept_id(context_from_resource("Dependency", "dependency:npm/shared"))
    assert (pypi, npm) == ("dependencies/pypi/shared", "dependencies/npm/shared")


@pytest.mark.parametrize(
    ("type_name", "resource"),
    [
        ("Repository", "repo:acme"),
        ("Package", "pkg:acme/demo"),
        ("Package", "pkg:acme//lib"),
        ("Dependency", "dependency:/httpx"),
        ("File", "file:acme/demo"),
        ("File", "file:acme//src/main.py"),
        ("Package", "app:acme/demo/lib"),
        ("Unknown", "unknown:acme/demo/lib"),
    ],
)
def test_malformed_resources_are_refused(type_name: str, resource: str) -> None:
    with pytest.raises(PlacementError, match=re.escape(resource)):
        context_from_resource(type_name, resource)


@pytest.mark.parametrize(
    "context",
    [
        PlacementContext(type_name="Package", resource="pkg:acme/demo/lib", repository="other"),
        PlacementContext(type_name="Package", resource="pkg:acme/demo/lib", name="other"),
        PlacementContext(type_name="Dependency", resource="dependency:pypi/httpx", ecosystem="npm"),
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
        "dependency:npm/CON",
        "dependency:npm/con.txt",
        "dependency:npm/trailing ",
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
    assert canonical_member(context) == "repositories/demo/packages/@acme__web.md"


def test_filesystem_member_identity_preserves_display_but_collides_case_and_unicode() -> None:
    composed = "repositories/Demo/packages/caf\N{LATIN SMALL LETTER E WITH ACUTE}.md"
    decomposed = "repositories/demo/packages/cafe\N{COMBINING ACUTE ACCENT}.md"

    assert composed != decomposed
    assert filesystem_member_identity(composed) == filesystem_member_identity(decomposed)


@pytest.mark.parametrize(
    ("type_name", "resource", "expected"),
    [
        ("Repository", "repo:acme/demo", ("repositories/demo",)),
        (
            "Package",
            "pkg:acme/demo/lib",
            ("repositories/demo", "repositories/demo/packages"),
        ),
        (
            "File",
            "file:acme/demo/src/pkg/main.py",
            (
                "repositories/demo",
                "repositories/demo/files",
                "repositories/demo/files/src",
                "repositories/demo/files/src/pkg",
            ),
        ),
        ("Dependency", "dependency:pypi/httpx", ("dependencies", "dependencies/pypi")),
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
        ("repositories/demo/repository", True),
        ("repositories/demo/packages/lib", True),
        ("dependencies/pypi/httpx", True),
        ("repositories/demo/files/src/main.py", False),
        ("repositories//packages/lib", False),
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
        ("Repository", "repo:acme/demo", "repositories/demo/repository"),
        ("Package", "pkg:acme/demo/lib", "repositories/demo/packages/lib"),
        ("App", "app:acme/demo/web", "repositories/demo/apps/web"),
        ("AgentPlugin", "agent_plugin:acme/demo/reviewer", "repositories/demo/agent-plugins/reviewer"),
        ("TestSuite", "test_suite:acme/demo/unit", "repositories/demo/test-suites/unit"),
        ("File", "file:acme/demo/src/main.py", "repositories/demo/files/src/main.py"),
        ("Dependency", "dependency:pypi/httpx", "dependencies/pypi/httpx"),
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
    resource = "dependency:pypi/httpx"
    _write_page(tmp_path, "dependencies/pypi/httpx", type_name="Dependency", resource=resource)
    _write_page(tmp_path, "elsewhere/httpx", type_name="Dependency", resource=resource)

    report = validate(load_bundle(tmp_path), today=_TODAY, extra_rules=[placement_rule()])

    findings = report.by_code("placement.duplicate-resource")
    assert len(findings) == 1
    assert findings[0].path == "elsewhere/httpx.md"
    assert "dependencies/pypi/httpx.md" in findings[0].message


def test_placement_rule_translates_public_warning_severity_to_a_warning_finding(tmp_path: Path) -> None:
    _write_page(tmp_path, "wrong/httpx", type_name="Dependency", resource="dependency:pypi/httpx")

    report = validate(load_bundle(tmp_path), today=_TODAY, extra_rules=[placement_rule(severity="warning")])

    assert report.by_code("placement.directory-mismatch")[0].severity == "warn"


def test_placement_rule_reports_malformed_owned_resource(tmp_path: Path) -> None:
    _write_page(tmp_path, "dependencies/pypi/broken", type_name="Dependency", resource="dependency:/broken")

    report = validate(load_bundle(tmp_path), today=_TODAY, extra_rules=[placement_rule()])

    finding = report.by_code("placement.directory-mismatch")[0]
    assert finding.path == "dependencies/pypi/broken.md"
    assert "no safe canonical placement" in finding.message


def test_placement_rule_leaves_non_code_wiki_vocabulary_pages_alone(tmp_path: Path) -> None:
    _write_page(tmp_path, "concepts/overview", type_name="Concept", resource="concept:overview")

    report = validate(load_bundle(tmp_path), today=_TODAY, extra_rules=[placement_rule()])

    assert report.by_code("placement.directory-mismatch") == ()
    assert report.by_code("placement.duplicate-resource") == ()


def test_placement_rule_rejects_unknown_severity() -> None:
    with pytest.raises(ValueError, match="severity must be"):
        placement_rule(severity="fatal")  # type: ignore[arg-type]
