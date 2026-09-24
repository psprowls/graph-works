"""Canonical placement for the code-wiki vocabulary.

This module is deliberately pure: resource identity is the only input to a
canonical member, and no workspace, bundle, schema set, or filesystem state is
consulted.  Writers can therefore refuse unsafe or colliding destinations
before touching disk, while validators use the same computation against loaded
documents.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from okf_io import Finding, Rule, RuleContext, Severity

CODE_WIKI_TYPES = frozenset({"Repository", "Package", "App", "AgentPlugin", "TestSuite", "File", "Dependency"})

_RESOURCE_PREFIXES = {
    "Repository": "repo",
    "Package": "pkg",
    "App": "app",
    "AgentPlugin": "agent_plugin",
    "TestSuite": "test_suite",
    "File": "file",
    "Dependency": "dependency",
}

CODE_GRAPH_LANE = "code-graph"
_ENTITIES = "entities"
_FILE_SYSTEM = "file-system"

_ENTITY_LANES = {
    "Package": "packages",
    "App": "apps",
    "TestSuite": "test-suites",
    "AgentPlugin": "agent-plugins",
    "Dependency": "dependencies",
}
_TYPE_BY_LANE = {lane: type_name for type_name, lane in _ENTITY_LANES.items()}

_INVALID_COMPONENT_CHARACTERS = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
        "COM¹",
        "COM²",
        "COM³",
        "LPT¹",
        "LPT²",
        "LPT³",
    }
)

_CODE_DIRECTORY = "placement.directory-mismatch"
_CODE_DUPLICATE = "placement.duplicate-resource"
_SPEC = "code_wiki_okf.placement"


@dataclass(frozen=True, slots=True)
class PlacementContext:
    type_name: str
    resource: str
    name: str | None = None
    repository: str | None = None
    ecosystem: str | None = None
    source_path: str | None = None


class PlacementError(ValueError):
    def __init__(self, *, resource: str, reason: str, expected: str | None = None) -> None:
        self.resource = resource
        self.reason = reason
        self.expected = expected
        suffix = f"; expected {expected}" if expected is not None else ""
        super().__init__(f"{resource}: {reason}{suffix}")


def is_code_wiki_type(type_name: str) -> bool:
    """Whether *type_name* is owned by the code-wiki vocabulary."""
    return type_name in CODE_WIKI_TYPES


@dataclass(frozen=True, slots=True)
class EntityPage:
    """A loaded concept ID's structural entity identity.

    `name` is the final path segment -- the slug, not the unescaped graph
    name -- and, for a Repository page, the repository name itself.
    """

    type_name: str
    repository: str
    name: str
    ecosystem: str | None = None


def entity_page(concept_id: str) -> EntityPage | None:
    """Parse *concept_id* by **position** into its entity identity.

    Recognises exactly ``code-graph/<repo>``,
    ``code-graph/<repo>/entities/<lane>/<slug>`` for the four repository
    lanes, and ``code-graph/<repo>/entities/dependencies/<eco>/<slug>``.
    Everything else -- ``file-system/`` mirrors included -- is ``None``.
    Spelling never decides: an entity named ``repository`` or ``entities``
    parses like any other. Exact resource-to-ID agreement remains
    :func:`placement_rule`'s job.
    """
    parts = concept_id.split("/")
    if any(not part for part in parts) or parts[0] != CODE_GRAPH_LANE:
        return None
    if len(parts) == 2:
        return EntityPage(type_name="Repository", repository=parts[1], name=parts[1])
    if len(parts) < 5 or parts[2] != _ENTITIES:
        return None
    type_name = _TYPE_BY_LANE.get(parts[3])
    if type_name is None:
        return None
    if type_name == "Dependency":
        if len(parts) != 6:
            return None
        return EntityPage(type_name=type_name, repository=parts[1], name=parts[5], ecosystem=parts[4])
    if len(parts) != 5:
        return None
    return EntityPage(type_name=type_name, repository=parts[1], name=parts[4])


def is_entity_lane_page(concept_id: str) -> bool:
    """Whether *concept_id* has a canonical non-File entity shape."""
    return entity_page(concept_id) is not None


def _malformed(resource: str, reason: str) -> PlacementError:
    return PlacementError(resource=resource, reason=f"malformed resource ({reason})")


def _repo_identity(resource: str, payload: str) -> tuple[str, str]:
    parts = payload.split("/")
    if len(parts) != 2 or not all(parts):
        raise _malformed(resource, "expected <organization>/<repository>")
    return parts[0], parts[1]


def _repo_member_identity(resource: str, payload: str, *, member: str) -> tuple[str, str, str]:
    parts = payload.split("/", 2)
    if len(parts) != 3 or not parts[0] or not parts[1] or not parts[2]:
        raise _malformed(resource, f"expected <organization>/<repository>/<{member}>")
    return parts[0], parts[1], parts[2]


def _dependency_identity(resource: str, payload: str) -> tuple[str, str, str, str]:
    parts = payload.split("/", 3)
    if len(parts) != 4 or not all(parts):
        raise _malformed(resource, "expected <organization>/<repository>/<ecosystem>/<name>")
    return parts[0], parts[1], parts[2], parts[3]


def context_from_resource(type_name: str, resource: str) -> PlacementContext:
    """Parse one canonical graph resource into placement identity.

    The resource prefix must agree with *type_name*.  Organization is parsed
    to prove the URI has its canonical shape but does not participate in the
    bundle path; repositories with the same name can consequently collide,
    and callers can detect that collision by comparing canonical IDs before a
    write.
    """
    expected_prefix = _RESOURCE_PREFIXES.get(type_name)
    if expected_prefix is None:
        raise PlacementError(resource=resource, reason=f"unsupported code-wiki type {type_name}")

    prefix, separator, payload = resource.partition(":")
    if not separator or prefix != expected_prefix:
        raise _malformed(resource, f"expected {expected_prefix}: resource for {type_name}")

    if type_name == "Repository":
        _organization, repository = _repo_identity(resource, payload)
        return PlacementContext(type_name=type_name, resource=resource, repository=repository)

    if type_name == "Dependency":
        _organization, repository, ecosystem, name = _dependency_identity(resource, payload)
        return PlacementContext(
            type_name=type_name, resource=resource, repository=repository, ecosystem=ecosystem, name=name
        )

    member = "source-path" if type_name == "File" else "name"
    _organization, repository, identity = _repo_member_identity(resource, payload, member=member)
    if type_name == "File":
        return PlacementContext(
            type_name=type_name,
            resource=resource,
            repository=repository,
            source_path=identity,
        )
    return PlacementContext(type_name=type_name, resource=resource, name=identity, repository=repository)


def _validated_context(context: PlacementContext) -> PlacementContext:
    parsed = context_from_resource(context.type_name, context.resource)
    for field_name in ("name", "repository", "ecosystem", "source_path"):
        explicit = getattr(context, field_name)
        derived = getattr(parsed, field_name)
        if explicit is not None and explicit != derived:
            raise PlacementError(
                resource=context.resource,
                reason=f"explicit {field_name} {explicit!r} conflicts with resource identity",
                expected=derived,
            )
    return parsed


def _required(value: str | None, *, resource: str, field: str) -> str:
    if value is None or not value:
        raise PlacementError(resource=resource, reason=f"missing {field}")
    return value


def _safe_component(value: str, *, resource: str) -> str:
    if not value:
        raise PlacementError(resource=resource, reason="empty path component")
    if value in {".", ".."}:
        raise PlacementError(resource=resource, reason=f"unsafe path component {value!r}")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise PlacementError(resource=resource, reason=f"absolute path component {value!r}")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise PlacementError(resource=resource, reason=f"control character in path component {value!r}")
    invalid = sorted(set(value) & _INVALID_COMPONENT_CHARACTERS)
    if invalid:
        raise PlacementError(resource=resource, reason=f"invalid character in path component {value!r}")
    if value.endswith((".", " ")):
        raise PlacementError(resource=resource, reason=f"path component has a trailing dot or space: {value!r}")
    device_name = value.partition(".")[0].upper()
    if device_name in _WINDOWS_RESERVED_NAMES:
        raise PlacementError(resource=resource, reason=f"Windows reserved device name {value!r}")
    return value


def _slug(value: str, *, resource: str) -> str:
    slug = value.replace("/", "__")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise PlacementError(resource=resource, reason=f"absolute entity name {value!r}")
    return _safe_component(slug, resource=resource)


def _safe_source_path(value: str, *, resource: str) -> str:
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise PlacementError(resource=resource, reason=f"absolute source path {value!r}")
    raw_parts = value.split("/")
    if any(not part for part in raw_parts):
        raise PlacementError(resource=resource, reason="empty source path component")
    for part in raw_parts:
        _safe_component(part, resource=resource)
    return "/".join(_safe_component(part, resource=resource) for part in PurePosixPath(value).parts)


def repository_directory(repository: str) -> str:
    """``code-graph/<repo>``: the directory beside the Repository page."""
    return f"{CODE_GRAPH_LANE}/{_safe_component(repository, resource=f'{CODE_GRAPH_LANE}/{repository}')}"


def entities_directory(repository: str) -> str:
    """``code-graph/<repo>/entities``."""
    return f"{repository_directory(repository)}/{_ENTITIES}"


def file_system_directory(repository: str) -> str:
    """``code-graph/<repo>/file-system``: the File mirror lane."""
    return f"{repository_directory(repository)}/{_FILE_SYSTEM}"


def lane_directory(repository: str, type_name: str, *, ecosystem: str | None = None) -> str:
    """One entity lane under ``entities/``; a Dependency may add its ecosystem."""
    lane = _ENTITY_LANES.get(type_name)
    base = entities_directory(repository)
    if lane is None:
        raise PlacementError(resource=f"{base}/?", reason=f"{type_name} has no entity lane")
    directory = f"{base}/{lane}"
    if type_name == "Dependency" and ecosystem is not None:
        directory = f"{directory}/{_safe_component(ecosystem, resource=f'{directory}/{ecosystem}')}"
    return directory


def canonical_concept_id(context: PlacementContext) -> str:
    """Return the exact bundle concept ID dictated by *context.resource*."""
    context = _validated_context(context)
    resource = context.resource
    repository = _safe_component(
        _required(context.repository, resource=resource, field="repository"), resource=resource
    )

    if context.type_name == "Repository":
        return repository_directory(repository)
    if context.type_name == "File":
        source = _safe_source_path(
            _required(context.source_path, resource=resource, field="source path"), resource=resource
        )
        return f"{file_system_directory(repository)}/{source}"
    if context.type_name == "Dependency":
        ecosystem = _safe_component(
            _required(context.ecosystem, resource=resource, field="ecosystem"), resource=resource
        )
        name = _slug(_required(context.name, resource=resource, field="name"), resource=resource)
        return f"{lane_directory(repository, 'Dependency', ecosystem=ecosystem)}/{name}"
    if context.type_name in _ENTITY_LANES:
        name = _slug(_required(context.name, resource=resource, field="name"), resource=resource)
        return f"{lane_directory(repository, context.type_name)}/{name}"
    raise PlacementError(resource=resource, reason=f"unsupported code-wiki type {context.type_name}")


def canonical_member(context: PlacementContext) -> str:
    """Return the canonical bundle member, including its ``.md`` suffix."""
    return f"{canonical_concept_id(context)}.md"


def filesystem_member_identity(member: str) -> str:
    """Return the cross-platform collision identity for a bundle member.

    Bundle members retain the exact spelling carried by graph resources, but
    writes must be safe when the same checkout is used on a case-insensitive
    filesystem or one that normalizes Unicode filenames.  NFC plus casefold
    gives planners and appliers one deterministic identity without changing
    the displayed member spelling.
    """
    return unicodedata.normalize("NFC", member).casefold()


def affected_directories(context: PlacementContext) -> tuple[str, ...]:
    """Return ordered directory IDs whose indexes a canonical member affects.

    Every member affects each directory from ``code-graph/<repo>`` down to
    its own parent. The Repository page (``code-graph/<repo>``) affects
    ``code-graph/<repo>`` alone -- the stub index there links it. The
    bundle-root and ``code-graph`` indexes stay catalog-owned and are never
    returned.
    """
    concept_id = canonical_concept_id(context)
    if context.type_name == "Repository":
        return (concept_id,)
    parts = concept_id.split("/")[:-1]
    return tuple("/".join(parts[:depth]) for depth in range(2, len(parts) + 1))


def placement_rule(*, severity: Literal["error", "warning"] = "error") -> Rule:
    """Build the code-wiki rule for exact placement and duplicate resources."""
    severity_map: dict[Literal["error", "warning"], Severity] = {"error": "error", "warning": "warn"}
    try:
        finding_severity = severity_map[severity]
    except KeyError as exc:
        raise ValueError("severity must be 'error' or 'warning'") from exc

    def rule(context: RuleContext) -> Iterable[Finding]:
        first_claim: dict[str, str] = {}
        for concept_id in sorted(context.bundle.concepts):
            document = context.bundle.concepts[concept_id]
            if document.parse_error is not None:
                continue

            type_name = (document.fm.type or "").strip()
            if not is_code_wiki_type(type_name):
                continue
            resource = document.fm.resource
            if resource is None:
                continue

            try:
                expected = canonical_concept_id(context_from_resource(type_name, resource))
            except PlacementError as exc:
                yield Finding(
                    code=_CODE_DIRECTORY,
                    severity=finding_severity,
                    message=f"`{concept_id}` has no safe canonical placement: {exc}",
                    spec=_SPEC,
                    path=f"{concept_id}.md",
                    line=document.frontmatter_line("resource"),
                )
            else:
                if concept_id != expected:
                    yield Finding(
                        code=_CODE_DIRECTORY,
                        severity=finding_severity,
                        message=f"actual concept ID `{concept_id}` does not match expected concept ID `{expected}`",
                        spec=_SPEC,
                        path=f"{concept_id}.md",
                        line=document.frontmatter_line("resource"),
                    )

            winner = first_claim.setdefault(resource, concept_id)
            if winner == concept_id:
                continue
            yield Finding(
                code=_CODE_DUPLICATE,
                severity=finding_severity,
                message=f"`{resource}` is already claimed by `{winner}.md`; this page is unreachable by resource",
                spec=_SPEC,
                path=f"{concept_id}.md",
                line=document.frontmatter_line("resource"),
            )

    return rule


__all__ = [
    "CODE_GRAPH_LANE",
    "CODE_WIKI_TYPES",
    "EntityPage",
    "PlacementContext",
    "PlacementError",
    "affected_directories",
    "canonical_concept_id",
    "canonical_member",
    "context_from_resource",
    "entities_directory",
    "entity_page",
    "file_system_directory",
    "filesystem_member_identity",
    "is_code_wiki_type",
    "is_entity_lane_page",
    "lane_directory",
    "placement_rule",
    "repository_directory",
]
