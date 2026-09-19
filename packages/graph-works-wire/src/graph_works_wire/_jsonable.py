"""The one value rule that replaces `json.dumps(..., default=str)`.

`gw config` used to encode `asdict(result)` with `default=str`, which
stringified any non-JSON-native value (a `Path`, an arbitrary `value`,
`default` or `shadowed` object) at encode time. Wire does no encoding, so it
applies the same rule up front. Dataclass values are walked as `asdict` walked
them, scalar subclasses become their exact JSON builtin, accepted containers
are walked, and anything else becomes `str(value)`.

Primitive mapping keys use the encoder's string spelling. Distinct keys that
collapse to one spelling are refused because a plain dictionary cannot retain
both entries and their encoded bytes.

NaN remains a float for legacy byte compatibility. It is the one value for
which an encode/decode round trip cannot compare equal because IEEE NaN is not
equal to itself.
"""

from __future__ import annotations

import math
from dataclasses import fields, is_dataclass


def _mapping_key(key: object) -> str:
    if isinstance(key, str):
        return key
    if key is None:
        return "null"
    if key is True:
        return "true"
    if key is False:
        return "false"
    if isinstance(key, int):
        return int.__repr__(key)
    if isinstance(key, float):
        if math.isnan(key):
            return "NaN"
        if math.isinf(key):
            return "Infinity" if key > 0 else "-Infinity"
        return float.__repr__(key)
    raise TypeError(f"JSON object keys must be str, int, float, bool or None, not {type(key).__name__}")


def jsonable(value: object) -> object:
    """Return *value* as plain JSON data, stringifying what JSON cannot hold."""
    if value is None or type(value) in (str, int, float, bool):
        return value
    if isinstance(value, str):
        return str.__str__(value)
    if isinstance(value, int):
        return int.__int__(value)
    if isinstance(value, float):
        return float.__float__(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        converted: dict[str, object] = {}
        for key, item in value.items():
            converted_key = _mapping_key(key)
            if converted_key in converted:
                raise ValueError(f"distinct mapping keys encode to the same JSON object name: {converted_key!r}")
            converted[converted_key] = jsonable(item)
        return converted
    if isinstance(value, list | tuple):
        return [jsonable(item) for item in value]
    return str(value)
