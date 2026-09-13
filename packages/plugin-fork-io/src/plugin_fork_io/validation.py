from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError

from .records import Finding, JsonValue


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str


SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def parse_skill_metadata(content: bytes, *, path: str) -> tuple[SkillMetadata | None, tuple[Finding, ...]]:
    try:
        text = content.decode("utf-8").removeprefix("\ufeff")
    except UnicodeDecodeError as error:
        line = content[: error.start].count(b"\n") + 1
        return None, (_finding("skill.encoding", path, line, "SKILL.md is not valid UTF-8"),)
    lines = text.splitlines(keepends=True)
    if not lines or lines[0] not in {"---\n", "---\r\n"}:
        return None, (_finding("skill.frontmatter", path, 1, "SKILL.md must start with YAML frontmatter"),)
    closing = next(
        (index for index, line in enumerate(lines[1:], start=1) if line in {"---", "---\n", "---\r\n"}),
        None,
    )
    if closing is None:
        return None, (_finding("skill.frontmatter", path, 1, "SKILL.md frontmatter is not closed"),)
    yaml = YAML(typ="safe")
    try:
        loaded = yaml.load("".join(lines[1:closing]))
    except MarkedYAMLError as error:
        line = error.problem_mark.line + 2 if error.problem_mark is not None else 1
        return None, (_finding("skill.yaml", path, line, "SKILL.md frontmatter is invalid YAML"),)
    if not isinstance(loaded, Mapping):
        return None, (_finding("skill.yaml", path, 2, "SKILL.md frontmatter must be a mapping"),)
    data: Mapping[object, object] = loaded
    findings: list[Finding] = []
    name = data.get("name")
    description = data.get("description")
    if not isinstance(name, str) or not SKILL_NAME.fullmatch(name) or len(name) > 64:
        findings.append(
            _finding(
                "skill.name",
                path,
                _key_line(text, "name"),
                "Skill name must use 1-64 lowercase ASCII letters, digits, and single hyphens",
            )
        )
    if not isinstance(description, str) or not description.strip():
        findings.append(
            _finding(
                "skill.description",
                path,
                _key_line(text, "description"),
                "Skill description must be a non-empty string",
            )
        )
    if findings:
        return None, tuple(findings)
    assert isinstance(name, str)
    assert isinstance(description, str)
    return SkillMetadata(name.strip(), description.strip()), ()


def _key_line(text: str, key: str) -> int:
    for number, line in enumerate(text.splitlines(), start=1):
        if line.startswith(f"{key}:"):
            return number
    return 2


def _finding(code: str, path: str, line: int, message: str) -> Finding:
    return Finding(code=code, severity="error", path=path, line=line, message=message)


def metadata_json(metadata: SkillMetadata, path: str) -> dict[str, JsonValue]:
    return {"name": metadata.name, "description": metadata.description, "path": path}


def closed_object(value: Mapping[str, JsonValue], fields: set[str]) -> None:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"Expected fields: {sorted(fields)}")


def object_list(value: JsonValue) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("Expected array of objects")
    return [item for item in value if isinstance(item, dict)]


def string(value: JsonValue) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Expected nonempty string")
    return value


def integer(value: JsonValue) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Expected nonnegative integer")
    return value
