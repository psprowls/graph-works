"""Query-parameter parsing for route handlers."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

ParamType = Literal["str", "bool", "int", "date", "csv", "list[str]"]
RESERVED = frozenset({"token"})
_BOOLS = {"true": True, "1": True, "false": False, "0": False}
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class ParamError(ValueError):
    """A request's query string does not fit its route's parameters."""


@dataclass(frozen=True, slots=True)
class Param:
    name: str
    type: ParamType
    required: bool = False
    default: object = None
    summary: str = ""
    minimum: int | None = None
    location: Literal["query", "body"] = "query"


def _convert(param: Param, raw: str) -> object:
    if param.type == "str":
        return raw
    if param.type == "bool":
        if raw.lower() not in _BOOLS:
            raise ParamError(f"{param.name}: expected true|false, got {raw!r}")
        return _BOOLS[raw.lower()]
    if param.type == "int":
        try:
            value = int(raw)
        except ValueError as exc:
            raise ParamError(f"{param.name}: expected an integer, got {raw!r}") from exc
        if param.minimum is not None and value < param.minimum:
            raise ParamError(f"{param.name}: must be >= {param.minimum}")
        return value
    if param.type == "date":
        try:
            if (
                len(raw) != 10
                or raw[4] != "-"
                or raw[7] != "-"
                or not raw[:4].isdigit()
                or not raw[5:7].isdigit()
                or not raw[8:].isdigit()
            ):
                raise ValueError(raw)
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ParamError(f"{param.name}: expected YYYY-MM-DD, got {raw!r}") from exc
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def parse(params: Sequence[Param], items: Iterable[tuple[str, str]]) -> dict[str, object]:
    """Convert *items* (a query string's pairs) against *params*, in declaration order."""
    by_name = {param.name: param for param in params}
    seen: dict[str, str] = {}
    for name, item in items:
        if name in RESERVED:
            continue
        if name not in by_name:
            raise ParamError(f"unknown parameter {name}")
        if name in seen:
            raise ParamError(f"{name} given more than once")
        seen[name] = item
    parsed: dict[str, object] = {}
    for param in params:
        raw = seen.get(param.name)
        if raw is None:
            if param.required:
                raise ParamError(f"{param.name} is required")
            parsed[param.name] = () if param.type == "csv" else param.default
            continue
        if raw == "" and param.type != "csv":
            if param.required:
                raise ParamError(f"{param.name} is required")
            parsed[param.name] = param.default
            continue
        parsed[param.name] = _convert(param, raw)
    return parsed


def parse_body(params: Sequence[Param], raw: bytes) -> dict[str, object]:
    """A JSON-object request body, typed against *params*.

    Values stay JSON-native (a date is validated but kept as its ISO string)
    because the parsed mapping is what a mutation digest hashes: ``{}`` and
    ``{"return": false}`` must normalize, and hash, the same.
    """
    try:
        document = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ParamError(f"request body is not JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ParamError("request body must be a JSON object")
    known = {param.name for param in params}
    unknown = sorted(set(document) - known)
    if unknown:
        raise ParamError(f"unknown field(s): {', '.join(unknown)}")
    parsed: dict[str, object] = {}
    for param in params:
        value = document.get(param.name)
        if value is None:
            if param.required:
                raise ParamError(f"missing required field {param.name!r}")
            parsed[param.name] = param.default
            continue
        parsed[param.name] = _body_value(param, value)
    return parsed


def _body_value(param: Param, value: object) -> object:
    if param.type == "str" and isinstance(value, str):
        return value
    if param.type == "bool" and isinstance(value, bool):
        return value
    if param.type == "date" and isinstance(value, str) and _DATE.fullmatch(value):
        try:
            date.fromisoformat(value)
        except ValueError:
            pass
        else:
            return value
    if param.type == "list[str]" and isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ParamError(f"field {param.name!r} must be {param.type}")
