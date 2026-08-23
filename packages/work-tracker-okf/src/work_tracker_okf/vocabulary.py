"""Closed vocabulary for path-native work items and their declarations."""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType

from okf_ext.tags import TagDefinition

TYPES: frozenset[str] = frozenset({"Release", "Epic", "Feature", "Bug", "TechDebt", "TestGap", "Spike"})
PARENT_TYPES: frozenset[str] = frozenset({"Release", "Epic", "Feature"})
ROOT_ONLY_TYPES: frozenset[str] = frozenset({"Release"})

WORK_STATUSES: frozenset[str] = frozenset(
    {"open", "accepted", "in-progress", "mitigated", "resolved", "wontfix", "superseded"}
)
PHASES: frozenset[str] = frozenset({"design", "plan", "execute", "finish", "done"})
EFFORTS: frozenset[str] = frozenset({"xtra-small", "small", "medium", "large", "xtra-large"})
DOCUMENT_STATUSES: frozenset[str] = frozenset({"draft", "stable", "deprecated"})
BLAST_RADII: frozenset[str] = frozenset({"file", "package", "domain", "system"})
TERMINAL_STATUSES: frozenset[str] = frozenset({"resolved", "wontfix", "superseded"})
BUG_LIKE_TYPES: frozenset[str] = frozenset({"Bug", "TechDebt", "TestGap"})
DIAGNOSIS_TYPES: frozenset[str] = frozenset({"Bug"})
SMALL_EFFORTS: frozenset[str] = frozenset({"xtra-small", "small"})

SLUG_PREFIXES: Mapping[str, str] = MappingProxyType(
    {
        "Release": "release",
        "Epic": "epic",
        "Feature": "feature",
        "Bug": "bug",
        "TechDebt": "tech-debt",
        "TestGap": "test-gap",
        "Spike": "spike",
    }
)

CONTRIBUTED_TAGS: tuple[TagDefinition, ...] = (
    TagDefinition(name="perf", description="A defect whose impact is performance."),
    TagDefinition(name="security", description="A defect with a security impact."),
)

#: Source ids are item-local names derived from managed artifact filenames.
SOURCE_ID_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
_SOURCE_ID_RE = re.compile(SOURCE_ID_PATTERN)

#: The two artifacts whose presence remains meaningful to the item projection.
SPEC_SOURCE_ID = "design"
PLAN_SOURCE_ID = "plan"


def is_source_id(value: str) -> bool:
    """Whether *value* is a kebab-case source identifier."""
    return _SOURCE_ID_RE.fullmatch(value) is not None


__all__ = [
    "BLAST_RADII",
    "BUG_LIKE_TYPES",
    "CONTRIBUTED_TAGS",
    "DIAGNOSIS_TYPES",
    "DOCUMENT_STATUSES",
    "EFFORTS",
    "PARENT_TYPES",
    "PHASES",
    "PLAN_SOURCE_ID",
    "ROOT_ONLY_TYPES",
    "SLUG_PREFIXES",
    "SMALL_EFFORTS",
    "SOURCE_ID_PATTERN",
    "SPEC_SOURCE_ID",
    "TERMINAL_STATUSES",
    "TYPES",
    "WORK_STATUSES",
    "is_source_id",
]
