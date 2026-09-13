"""Located Markdown references and explicit dependency evidence, never recursive selection."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping
from dataclasses import asdict, replace
from typing import Literal, cast
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.rules_inline import link
from markdown_it.rules_inline.backticks import backtick
from markdown_it.rules_inline.state_inline import StateInline
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import MappingNode, ScalarNode, SequenceNode

from .records import Dependency, Finding, JsonValue
from .validation import closed_object, integer, string

INVOCATION = re.compile(
    r"(?<![\w/\\>])(?:\$|/)(?:[a-z0-9]+(?:-[a-z0-9]+)*:)?([a-z0-9]+(?:-[a-z0-9]+)*)(?![\w/-]|\.[A-Za-z0-9])"
)
KINDS = {"required-skill", "required-resource", "alternative", "optional", "host"}
EVIDENCE = {"markdown", "invocation", "required-statement", "manifest", "user", "agent", "heuristic"}


def decode_dependency(value: Mapping[str, JsonValue]) -> Dependency:
    closed_object(
        value, {"kind", "target", "path", "line", "start", "end", "evidence", "required", "group", "satisfied"}
    )
    if string(value["kind"]) not in KINDS or string(value["evidence"]) not in EVIDENCE:
        raise ValueError("Unsupported dependency kind/evidence")
    if type(value["required"]) is not bool or type(value["satisfied"]) is not bool:
        raise ValueError("Dependency flags must be booleans")
    if value["group"] is not None and not isinstance(value["group"], str):
        raise ValueError("Dependency group must be a string or null")
    start, end = integer(value["start"]), integer(value["end"])
    if end < start:
        raise ValueError("Invalid dependency span")
    return Dependency(
        cast('Literal["required-skill", "required-resource", "alternative", "optional", "host"]', value["kind"]),
        string(value["target"]),
        string(value["path"]),
        integer(value["line"]),
        start,
        end,
        cast(
            'Literal["markdown", "invocation", "required-statement", "manifest", "user", "agent", "heuristic"]',
            value["evidence"],
        ),
        value["required"],
        value["group"],
        value["satisfied"],
    )


def dependency_json(dependency: Dependency) -> dict[str, JsonValue]:
    return asdict(dependency)


def _located_link(state: StateInline, silent: bool) -> bool:
    start, count = state.pos, len(state.tokens)
    ok = link(state, silent)
    if ok and not silent:
        for token in state.tokens[count:]:
            if token.type == "link_open" and "span" not in token.meta:
                token.meta["reference_start"] = start
                label_end = state.md.helpers.parseLinkLabel(state, start, True)
                pos = label_end + 1
                if pos < len(state.src) and state.src[pos] == "(":
                    pos += 1
                    while pos < len(state.src) and state.src[pos].isspace():
                        pos += 1
                    dest = state.md.helpers.parseLinkDestination(state.src, pos, len(state.src))
                    if dest.ok:
                        token.meta["span"] = (pos, dest.pos)
                break
    return ok


def _located_code(state: StateInline, silent: bool) -> bool:
    start, count = state.pos, len(state.tokens)
    ok = backtick(state, silent)
    if ok and not silent:
        for token in state.tokens[count:]:
            if token.type == "code_inline":
                token.meta["span"] = (start + len(token.markup), state.pos - len(token.markup))
    return ok


def scan_markdown(
    content: bytes, path: str, known_names: tuple[str, ...] = ()
) -> tuple[tuple[Dependency, ...], tuple[Finding, ...]]:
    try:
        original = content.decode("utf-8")
    except UnicodeError:
        return (), (Finding("reference.encoding", "warn", path, None, "Cannot scan non-UTF-8 Markdown"),)
    # markdown-it normalizes CRLF; retain an exact normalized-character -> byte map.
    text, offsets, byte = "", [], 0
    for char in original:
        if char != "\r":
            offsets.append(byte)
            text += char
        byte += len(char.encode("utf-8"))
    offsets.append(byte)
    md = MarkdownIt("commonmark", {"store_labels": True})
    md.inline.ruler.at("link", _located_link)
    md.inline.ruler.at("backticks", _located_code)
    dependencies: list[Dependency] = []
    findings: list[Finding] = []
    lines = text.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    # Hide frontmatter from Markdown parsing without changing offsets.
    parsed = text
    if lines and lines[0].lstrip("\ufeff").rstrip("\n") == "---":
        closing = next((i for i in range(1, len(lines)) if lines[i].rstrip("\n") == "---"), None)
        if closing is not None:
            parsed = (
                "".join("\n" if c == "\n" else " " for c in text[: starts[closing + 1]]) + text[starts[closing + 1] :]
            )

    def add(
        target: str,
        start: int,
        end: int,
        kind: Literal["required-skill", "required-resource"],
        evidence: Literal["markdown", "invocation", "required-statement"],
        *,
        located: bool = True,
        line: int = 1,
        prefix: str = "",
    ) -> None:
        # Only an explicit marker immediately attached to this reference is evidence
        # of optionality. A different reference or "Nonoptional" never grants it.
        optional = (
            evidence != "required-statement"
            and re.search(r"(?i)(?:^|[.;!?\n])\s*(?:[-*>]\s*)*optional\s*:\s*[`*]*\s*$", prefix) is not None
        )
        dependencies.append(
            Dependency(
                "optional" if optional else kind,
                target,
                path,
                text[:start].count("\n") + 1 if located else line,
                offsets[start] if located else 0,
                (offsets[end - 1] + len(text[end - 1].encode("utf-8"))) if located and end > start else 0,
                evidence,
                not optional,
            )
        )

    env: dict[str, object] = {}
    blocks = md.parse(parsed, env)
    statement_lines: set[int] = set()
    for block in blocks:
        if block.type != "inline" or block.map is None:
            continue
        statement_lines.update(range(block.map[0], block.map[1]))
        lo, hi = starts[block.map[0]], starts[block.map[1]]
        region = text[lo:hi]
        relative = region.find(block.content)
        located = relative >= 0 and region.find(block.content, relative + 1) < 0
        if not located:
            findings.append(
                Finding(
                    "reference.location",
                    "warn",
                    path,
                    block.map[0] + 1,
                    "Semantic references retained; Markdown container has no unambiguous edit span",
                )
            )
        base = lo + relative if located else lo
        for token in block.children or []:
            span = token.meta.get("span")
            span_located = located and span is not None
            reference_start = token.meta.get("reference_start", 0)
            if token.type == "link_open" and span is None:
                references = env.get("references")
                label = token.meta.get("label")
                ref = references.get(label) if isinstance(references, dict) else None
                if isinstance(ref, dict) and isinstance(ref.get("map"), list):
                    definition = starts[ref["map"][0]]
                    definition_end = starts[ref["map"][1]]
                    region = text[definition:definition_end]
                    colon = region.find("]:")
                    position = colon + 2
                    while position < len(region) and region[position].isspace():
                        position += 1
                    destination = md.helpers.parseLinkDestination(region, position, len(region))
                    if colon >= 0 and destination.ok:
                        span = (definition + position - base, definition + destination.pos - base)
                        span_located = True
            if token.type == "link_open":
                url = urlsplit(str(token.attrGet("href") or ""))
                if url.scheme or url.netloc or not url.path:
                    continue
                target = posixpath.normpath(posixpath.join(posixpath.dirname(path), unquote(url.path)))
                if target == ".." or target.startswith("../") or url.path.startswith("/"):
                    findings.append(
                        Finding(
                            "reference.escape",
                            "error",
                            path,
                            block.map[0] + 1,
                            "Supporting resource escapes source bounds",
                        )
                    )
                # Missing location is never missing semantic evidence.
                add(
                    target,
                    base + span[0] if span is not None else 0,
                    base + span[1] if span is not None else 0,
                    "required-resource",
                    "markdown",
                    located=span_located,
                    line=block.map[0] + 1,
                    prefix=block.content[:reference_start],
                )
            elif token.type in {"text", "code_inline"}:
                if token.type == "code_inline" and span is not None:
                    raw, position = block.content[span[0] : span[1]], span[0]
                    context_known = True
                    token_located = located
                else:
                    raw = token.content
                    position = block.content.find(raw)
                    context_known = position >= 0 and block.content.find(raw, position + 1) < 0
                    token_located = located and context_known
                for invocation in INVOCATION.finditer(raw):
                    start = base + max(position, 0) + invocation.start()
                    end = base + max(position, 0) + invocation.end()
                    # Ambiguous text cannot borrow another occurrence's optionality.
                    # The semantic requirement survives even when its edit span does not.
                    prefix = block.content[: position + invocation.start()] if context_known else ""
                    add(
                        invocation.group(1),
                        start,
                        end,
                        "required-skill",
                        "invocation",
                        located=token_located,
                        line=block.map[0] + 1,
                        prefix=prefix,
                    )
        for name in known_names:
            if any(
                t.type == "text" and re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", t.content)
                for t in block.children or []
            ):
                findings.append(
                    Finding(
                        "reference.heuristic",
                        "warn",
                        path,
                        block.map[0] + 1,
                        f"Prose mention of {name}; left unchanged",
                    )
                )
    for index, line in enumerate(lines):
        marker = "REQUIRED SUB-SKILL:"
        if marker in line and index in statement_lines:
            tail_start = line.index(marker) + len(marker)
            tail = line[tail_start:].strip().strip("`* ")
            explicit = INVOCATION.search(tail)
            target = explicit.group(1) if explicit else tail.rsplit(":", 1)[-1]
            if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", target):
                start = starts[index] + line.index(tail, tail_start)
                end = start + len(tail)
                existing = [i for i, d in enumerate(dependencies) if d.line == index + 1 and d.target == target]
                if existing:
                    for dependency_index in existing:
                        dependencies[dependency_index] = replace(
                            dependencies[dependency_index],
                            kind="required-skill",
                            required=True,
                            evidence="required-statement",
                        )
                else:
                    add(target, start, end, "required-skill", "required-statement")
    return tuple(dependencies), tuple(findings)


def scan_host_metadata(content: bytes, path: str) -> tuple[tuple[Dependency, ...], tuple[Finding, ...]]:
    try:
        data = YAML(typ="safe").load(content.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Host metadata must be an object")
        dependencies = data.get("dependencies", {})
        if not isinstance(dependencies, dict) or not isinstance(dependencies.get("tools", []), list):
            raise ValueError("Invalid declared tools")
        text = content.decode("utf-8")
        root = YAML(typ="safe").compose(text)
        tool_nodes: list[MappingNode] = []
        if isinstance(root, MappingNode):
            for key, value in root.value:
                if isinstance(key, ScalarNode) and key.value == "dependencies" and isinstance(value, MappingNode):
                    for tools_key, tools_value in value.value:
                        if (
                            isinstance(tools_key, ScalarNode)
                            and tools_key.value == "tools"
                            and isinstance(tools_value, SequenceNode)
                        ):
                            tool_nodes = [node for node in tools_value.value if isinstance(node, MappingNode)]
        result = []
        for index, tool in enumerate(dependencies.get("tools", [])):
            if not isinstance(tool, dict) or not isinstance(tool.get("value"), str):
                raise ValueError("Invalid declared tool")
            nodes = [
                value for key, value in tool_nodes[index].value if isinstance(key, ScalarNode) and key.value == "value"
            ]
            if len(nodes) != 1 or not isinstance(nodes[0], ScalarNode):
                raise ValueError("Ambiguous declared tool span")
            scalar = nodes[0]
            start = len(text[: scalar.start_mark.index].encode("utf-8"))
            end = len(text[: scalar.end_mark.index].encode("utf-8"))
            result.append(Dependency("host", tool["value"], path, scalar.start_mark.line + 1, start, end, "manifest"))
        return tuple(result), ()
    except (UnicodeError, YAMLError, ValueError):
        return (), (Finding("dependency.metadata", "error", path, 1, "Cannot parse declared host requirements"),)
