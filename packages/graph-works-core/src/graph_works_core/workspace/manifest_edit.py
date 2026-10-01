"""Byte-preserving edits to `workspace.yaml`.

`workspace.manifest.set_value` is the validated writer, but it re-dumps the whole file and drops every comment.
An edit that must leave the rest of an authored manifest untouched splices text at compose source marks instead
(the technique `workspace/init.py`'s `_insert_dispatch_reference` uses), then re-parses the result and checks that
exactly the intended keys changed.
"""

from __future__ import annotations

import json
import re

from ruamel.yaml import YAML
from ruamel.yaml.nodes import MappingNode, Node, ScalarNode

from graph_works_core.workspace.errors import WorkspaceError

__all__ = ["repoint_repository"]

_PLAIN = re.compile(r"[A-Za-z_./][A-Za-z0-9_./-]*\Z")


def _member(node: MappingNode, key: str) -> tuple[Node, Node] | None:
    return next(((k, v) for k, v in node.value if isinstance(k, ScalarNode) and k.value == key), None)


def _scalar(value: str) -> str:
    """*value* as a YAML scalar: plain when that is unambiguous, else a JSON (= YAML double-quoted) string."""
    return (
        value
        if _PLAIN.match(value)
        and value.lower() not in {"true", "false", "null", "yes", "no", "on", "off", ".nan", ".inf"}
        else json.dumps(value)
    )


def repoint_repository(text: str, name: str, *, path: str, checkout: str) -> str:
    """Set `repositories.<name>.path` to *path* and add `checkout: <checkout>` right after it.

    Only a block-style entry with a scalar `path` and no `checkout` is spliced; anything else raises
    `WorkspaceError` and the caller refuses. Trailing comments on the `path` line, every other key, and the
    file's line endings are kept byte for byte.
    """
    root = YAML(typ="safe").compose(text)
    if not isinstance(root, MappingNode):
        raise WorkspaceError("workspace.yaml must hold a mapping")
    where = f"repositories.{name}"
    repositories = _member(root, "repositories")
    mapping = repositories[1] if repositories else None
    entry = _member(mapping, name) if isinstance(mapping, MappingNode) else None
    if entry is None:
        raise WorkspaceError(f"workspace.yaml declares no {where}")
    node = entry[1]
    if (
        not isinstance(node, MappingNode)
        or node.flow_style
        or not isinstance(mapping, MappingNode)
        or mapping.flow_style
    ):
        raise WorkspaceError(f"workspace.yaml: {where} must be a block mapping to be repointed")
    if _member(node, "checkout") is not None:
        raise WorkspaceError(f"workspace.yaml: {where} already declares checkout")
    found = _member(node, "path")
    if found is None or not isinstance(found[1], ScalarNode):
        raise WorkspaceError(f"workspace.yaml: {where}.path must be a scalar to be repointed")
    key = found[0]
    value = found[1]
    key_start: int = key.start_mark.index
    line_start = text.rfind("\n", 0, key_start) + 1
    if (
        value.style in {"|", ">"}
        or key.start_mark.line != value.start_mark.line
        or value.start_mark.line != value.end_mark.line
        or text[line_start:key_start].strip()
    ):
        raise WorkspaceError(f"workspace.yaml: {where}.path must be an inline scalar to be repointed")
    newline = "\r\n" if "\r\n" in text else "\n"
    start: int = value.start_mark.index
    end: int = value.end_mark.index
    line_end = text.find("\n", end)
    tail_end = len(text) if line_end == -1 else line_end + 1
    tail = text[end:tail_end] if line_end != -1 else text[end:] + newline
    column: int = key.start_mark.column
    insertion = " " * column + f"checkout: {_scalar(checkout)}{newline}"
    after = text[:start] + _scalar(path) + tail + insertion + text[tail_end:]
    _check(text, after, name, path=path, checkout=checkout)
    return after


def _check(before: str, after: str, name: str, *, path: str, checkout: str) -> None:
    loader = YAML(typ="safe")
    old, new = loader.load(before), loader.load(after)
    expected_entry = {**old["repositories"][name], "path": path, "checkout": checkout}
    expected = {**old, "repositories": {**old["repositories"], name: expected_entry}}
    if new != expected:
        raise WorkspaceError(f"workspace.yaml: repointing repositories.{name} would change more than path and checkout")
