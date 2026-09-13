"""Reviewed edits target exact byte spans; parsing never reserializes a document."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import MappingNode, ScalarNode

from .records import Adaptation, Finding, JsonValue
from .snapshots import validate_path
from .validation import closed_object, integer, string

KINDS = {"skill-name", "invocation", "resource-path", "materialize-link", "permission"}


def decode_adaptation(value: Mapping[str, JsonValue]) -> Adaptation:
    closed_object(value, {"kind", "path", "start", "end", "expected", "replacement", "purpose"})
    kind = string(value["kind"])
    if kind not in KINDS:
        raise ValueError("Unsupported adaptation kind")
    start, end = integer(value["start"]), integer(value["end"])
    if end < start or not isinstance(value["expected"], str) or not isinstance(value["replacement"], str):
        raise ValueError("Invalid adaptation span or value")
    try:
        expected, replacement = bytes.fromhex(value["expected"]), bytes.fromhex(value["replacement"])
    except ValueError as exc:
        raise ValueError("Adaptation byte values must be hexadecimal") from exc
    if end - start != len(expected):
        raise ValueError("Adaptation span length differs from expected bytes")
    return Adaptation(
        cast("LiteralKind", kind),
        validate_path(string(value["path"])),
        start,
        end,
        expected,
        replacement,
        string(value["purpose"]),
    )


type LiteralKind = Literal["skill-name", "invocation", "resource-path", "materialize-link", "permission"]


def adaptation_json(edit: Adaptation) -> dict[str, JsonValue]:
    return {
        "kind": edit.kind,
        "path": edit.path,
        "start": edit.start,
        "end": edit.end,
        "expected": edit.expected.hex(),
        "replacement": edit.replacement.hex(),
        "purpose": edit.purpose,
    }


def replay(content: bytes, edits: tuple[Adaptation, ...]) -> tuple[bytes, tuple[Finding, ...]]:
    ordered = sorted((e for e in edits if e.kind not in {"materialize-link", "permission"}), key=lambda e: e.start)
    end = 0
    for edit in ordered:
        if (
            edit.start < end
            or edit.end < edit.start
            or edit.end > len(content)
            or edit.end - edit.start != len(edit.expected)
            or content[edit.start : edit.end] != edit.expected
        ):
            return content, (
                Finding(
                    "adaptation.ambiguous",
                    "error",
                    edit.path,
                    None,
                    "Expected byte span changed or overlaps another edit",
                ),
            )
        end = edit.end
    result = content
    for edit in reversed(ordered):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    return result, ()


def name_adaptation(content: bytes, path: str, name: str) -> tuple[Adaptation | None, tuple[Finding, ...]]:
    def refuse() -> tuple[None, tuple[Finding, ...]]:
        return None, (
            Finding(
                "adaptation.ambiguous",
                "error",
                path,
                2,
                "Name scalar is ambiguous or unsupported; provide a reviewed byte-span adaptation",
            ),
        )

    try:
        text = content.decode("utf-8")
        lines = text.splitlines(keepends=True)
        if not lines or lines[0].lstrip("\ufeff") not in {"---\n", "---\r\n"}:
            return refuse()
        close = next(i for i in range(1, len(lines)) if lines[i].rstrip("\r\n") == "---")
        front = "".join(lines[1:close])
        node = YAML(typ="safe").compose(front)
        if not isinstance(node, MappingNode):
            return refuse()
        names = [v for k, v in node.value if isinstance(k, ScalarNode) and k.value == "name"]
        if len(names) != 1 or not isinstance(names[0], ScalarNode):
            return refuse()
        scalar = names[0]
        if scalar.style not in {None, "", "'", '"'} or "\n" in scalar.value or "\r" in scalar.value:
            return refuse()
        raw = front[scalar.start_mark.index : scalar.end_mark.index]
        if raw.startswith(("&", "*", "!")):
            return refuse()
        start = len((lines[0] + front[: scalar.start_mark.index]).encode("utf-8"))
        expected = raw.encode("utf-8")
        quote = scalar.style or ""
        return Adaptation(
            "skill-name",
            path,
            start,
            start + len(expected),
            expected,
            (quote + name + quote).encode(),
            "Standalone folder/name equality",
        ), ()
    except (UnicodeError, YAMLError, StopIteration):
        return refuse()


def resource_replacement(expected: bytes, relative: str) -> bytes:
    """Preserve equivalent destinations and URL suffixes, including escaped links."""
    from urllib.parse import unquote, urlsplit

    from markdown_it import MarkdownIt

    raw = expected.decode("utf-8")
    parsed = MarkdownIt().helpers.parseLinkDestination(raw, 0, len(raw))
    if not parsed.ok:
        raise ValueError("Unsupported Markdown destination")
    url = urlsplit(parsed.str)
    if unquote(url.path) == relative:
        return expected
    suffix = ("?" + url.query if url.query else "") + ("#" + url.fragment if url.fragment else "")
    return ("<" + relative + suffix + ">").encode("utf-8")


def permission_mode(mode: int, edits: tuple[Adaptation, ...]) -> tuple[int, tuple[Finding, ...]]:
    """Explicit permission records carry ASCII octal mode values, hex in JSON."""
    permissions = [edit for edit in edits if edit.kind == "permission"]
    if not permissions:
        return mode & 0o777, ()
    edit = permissions[0]
    try:
        before, after = int(edit.expected, 8), int(edit.replacement, 8)
        if len(permissions) != 1 or before != mode or not 0 <= after <= 0o777 or edit.start != 0:
            raise ValueError("Invalid permission edit")
    except ValueError:
        return mode & 0o777, (
            Finding(
                "adaptation.ambiguous", "error", edit.path, None, "Permission evidence does not match original mode"
            ),
        )
    return after, ()


def relocate(content: bytes, edits: tuple[Adaptation, ...]) -> tuple[tuple[Adaptation, ...], tuple[Finding, ...]]:
    """Keep matching spans, otherwise relocate only one exact expected-byte match."""
    from dataclasses import replace

    relocated: list[Adaptation] = []
    for edit in edits:
        if edit.kind in {"permission", "materialize-link"} or content[edit.start : edit.end] == edit.expected:
            relocated.append(edit)
        elif edit.expected and content.count(edit.expected) == 1:
            start = content.index(edit.expected)
            relocated.append(replace(edit, start=start, end=start + len(edit.expected)))
        else:
            return edits, (
                Finding(
                    "adaptation.ambiguous",
                    "error",
                    edit.path,
                    None,
                    "Changed adaptation span has no unique expected-byte match",
                ),
            )
    _, findings = replay(content, tuple(relocated))
    return tuple(relocated), findings
